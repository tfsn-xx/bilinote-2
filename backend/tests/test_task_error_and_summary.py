import os
import sys
import threading
import time
import importlib.util
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_task_error = _load_module("task_error_under_test", "app/utils/task_error.py")
TaskErrorCode = _task_error.TaskErrorCode
TaskGenerationError = _task_error.TaskGenerationError
classify_model_exception = _task_error.classify_model_exception
redact_error_text = _task_error.redact_error_text
_chunker = _load_module("request_chunker_under_test", "app/gpt/request_chunker.py")
TokenBudgetChunker = _chunker.TokenBudgetChunker
ChunkPayload = _chunker.ChunkPayload
estimate_mixed_tokens = _chunker.estimate_mixed_tokens
try:
    from app.gpt.universal_gpt import UniversalGPT
    from app.gpt.provider.OpenAI_compatible_provider import OpenAICompatibleProvider
    from app.models.gpt_model import GPTSource
    from app.models.transcriber_model import TranscriptSegment
    _SUMMARY_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    UniversalGPT = OpenAICompatibleProvider = GPTSource = TranscriptSegment = None
    _SUMMARY_IMPORT_ERROR = exc


class _StatusError(Exception):
    def __init__(self, status_code, text):
        super().__init__(text)
        self.status_code = status_code


class TestTaskErrorClassification(unittest.TestCase):
    def test_connection_is_not_context_overflow(self):
        error = classify_model_exception(Exception("RemoteProtocolError: Server disconnected without sending a response"))
        self.assertEqual(error.code, TaskErrorCode.MODEL_CONNECTION_ERROR.value)

    def test_context_requires_status_and_keyword(self):
        self.assertEqual(
            classify_model_exception(_StatusError(413, "maximum token limit exceeded")).code,
            TaskErrorCode.CONTEXT_LENGTH_EXCEEDED.value,
        )
        self.assertEqual(
            classify_model_exception(_StatusError(413, "payload rejected by gateway")).code,
            TaskErrorCode.MODEL_BAD_REQUEST.value,
        )

    def test_auth_rate_limit_timeout_and_redaction(self):
        self.assertEqual(classify_model_exception(_StatusError(401, "bad key")).code, TaskErrorCode.MODEL_AUTH_FAILED.value)
        self.assertEqual(classify_model_exception(_StatusError(429, "insufficient quota")).code, TaskErrorCode.MODEL_RATE_LIMITED.value)
        self.assertEqual(classify_model_exception(TimeoutError("timed out")).code, TaskErrorCode.MODEL_TIMEOUT.value)
        self.assertNotIn("secret", redact_error_text("Authorization: Bearer secret"))

    def test_exhausted_relay_pool_is_not_misreported_as_bad_api_key(self):
        error = classify_model_exception(
            _StatusError(401, "All available accounts exhausted")
        )
        self.assertEqual(error.code, TaskErrorCode.MODEL_UPSTREAM_UNAVAILABLE.value)
        self.assertTrue(error.retryable)
        self.assertNotIn("API Key", error.message)

    def test_unsupported_model_has_a_specific_non_retryable_error(self):
        error = classify_model_exception(
            _StatusError(404, 'Model "gpt-test" is not supported by any configured account')
        )
        self.assertEqual(error.code, TaskErrorCode.MODEL_NOT_AVAILABLE.value)
        self.assertFalse(error.retryable)


@unittest.skipIf(TokenBudgetChunker is None, "optional backend runtime dependencies are unavailable")
class TestTokenChunker(unittest.TestCase):
    def test_mixed_text_estimate_and_order(self):
        segments = [
            {"start": i * 60, "end": i * 60 + 50, "text": "这是第%d段内容，包含 important facts。" % i}
            for i in range(20)
        ]

        def builder(items, images, **kwargs):
            return [{"role": "user", "content": kwargs.get("title", "") + "".join(s["text"] for s in items)
                     + " ".join(images)}]

        chunks = TokenBudgetChunker(builder, 80, hard_max_bytes=100000).chunk(segments, ["data:image/png;base64," + ("A" * 8)])
        flattened = [s["text"] for chunk in chunks for s in chunk.segments]
        self.assertEqual(flattened, [s["text"] for s in segments])
        self.assertEqual([c.chunk_index for c in chunks], list(range(1, len(chunks) + 1)))
        self.assertGreater(estimate_mixed_tokens("中文 English"), 0)

    def test_short_text_is_one_chunk(self):
        builder = lambda items, images, **kwargs: [{"role": "user", "content": "".join(s["text"] for s in items)}]
        chunks = TokenBudgetChunker(builder, 6000).chunk([{"start": 0, "end": 4, "text": "短文本"}], [])
        self.assertEqual(len(chunks), 1)


@unittest.skipIf(UniversalGPT is None, "optional backend runtime dependencies are unavailable")
class TestParallelSummary(unittest.TestCase):
    def test_model_request_uses_streaming_and_collects_deltas(self):
        calls = []

        class Completions:
            def create(self, **kwargs):
                calls.append(kwargs)

                def chunks():
                    yield SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="流式"),
                    )])
                    yield SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="输出"),
                    )])

                return chunks()

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        result = gpt._chat_completion_content(
            [{"role": "user", "content": "x"}],
            phase="summarize_chunk",
        )

        self.assertEqual(result, "流式输出")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["stream"])

    def test_empty_stream_retries_and_then_succeeds(self):
        calls = []

        class Completions:
            def create(self, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return iter([
                        SimpleNamespace(choices=[SimpleNamespace(
                            delta=SimpleNamespace(role="assistant", content=None),
                            finish_reason="stop",
                        )]),
                    ])
                return iter([
                    SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="重试成功"),
                        finish_reason="stop",
                    )]),
                ])

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        gpt._max_retry_attempts = 2
        gpt._retry_base_backoff = 0
        result = gpt._chat_completion_content(
            [{"role": "user", "content": "x"}],
            phase="summarize_chunk",
        )

        self.assertEqual(result, "重试成功")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(call["stream"] for call in calls))

    def test_empty_stream_falls_back_to_non_streaming_response(self):
        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                if kwargs["stream"]:
                    return iter(())
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content="非流式回退成功"),
                )])

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        gpt._max_retry_attempts = 2
        gpt._retry_base_backoff = 0
        result = gpt._chat_completion_content(
            [{"role": "user", "content": "x"}],
            phase="summarize_chunk",
        )

        self.assertEqual(result, "非流式回退成功")
        self.assertEqual([call["stream"] for call in calls], [True, False])

    def test_stream_accepts_dict_events_and_text_blocks(self):
        class Completions:
            @staticmethod
            def create(**_kwargs):
                return iter([
                    {
                        "choices": [{
                            "delta": {"content": [{"type": "text", "text": {"value": "兼容成功"}}]},
                            "finish_reason": "stop",
                        }],
                    },
                ])

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        result = gpt._chat_completion_content(
            [{"role": "user", "content": "x"}],
            phase="summarize_chunk",
        )
        self.assertEqual(result, "兼容成功")

    def test_reasoning_only_length_retries_with_low_effort(self):
        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    return iter([
                        SimpleNamespace(choices=[SimpleNamespace(
                            delta=SimpleNamespace(
                                role="assistant",
                                content=None,
                                reasoning_content="hidden",
                            ),
                            finish_reason="length",
                        )]),
                    ])
                return iter([
                    SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="最终正文"),
                        finish_reason="stop",
                    )]),
                ])

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        gpt._max_retry_attempts = 2
        gpt._retry_base_backoff = 0
        result = gpt._chat_completion_content(
            [{"role": "user", "content": "生成笔记"}],
            phase="summarize_chunk",
        )

        self.assertEqual(result, "最终正文")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["reasoning_effort"], "low")
        self.assertEqual(calls[1]["max_completion_tokens"], 16384)
        self.assertIn("直接生成最终 Markdown", calls[1]["messages"][-1]["content"])

    def test_unsupported_reasoning_options_are_removed(self):
        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                if "reasoning_effort" in kwargs:
                    raise Exception("Unsupported parameter: reasoning_effort")
                if "max_completion_tokens" in kwargs:
                    raise Exception("Unsupported parameter: max_completion_tokens")
                return "兼容成功"

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        result = gpt._do_create(
            [{"role": "user", "content": "x"}],
            reasoning_effort="low",
            max_completion_tokens=16384,
        )

        self.assertEqual(result, "兼容成功")
        self.assertEqual(len(calls), 3)
        self.assertNotIn("reasoning_effort", calls[-1])
        self.assertNotIn("max_completion_tokens", calls[-1])

    def test_provider_connection_requires_usable_text_without_optional_parameters(self):
        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content="OK"),
                )])

        client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
        module_path = "app.gpt.provider.OpenAI_compatible_provider.build_openai_client"
        with patch(module_path, return_value=client):
            self.assertTrue(OpenAICompatibleProvider.test_connection("key", "https://example.test/v1", "mock"))

        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]["stream"])
        self.assertNotIn("temperature", calls[0])
        self.assertNotIn("max_tokens", calls[0])
        self.assertNotIn("max_completion_tokens", calls[0])

    def test_provider_connection_rejects_empty_success_response(self):
        response = SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=""),
        )])
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **_kwargs: response,
        )))
        module_path = "app.gpt.provider.OpenAI_compatible_provider.build_openai_client"
        with patch(module_path, return_value=client):
            self.assertFalse(OpenAICompatibleProvider.test_connection("key", "https://example.test/v1", "mock"))

    def test_reasoning_exhausted_chunk_is_split_and_retried_serially(self):
        calls = []

        class Completions:
            @staticmethod
            def create(**kwargs):
                calls.append(kwargs)
                if "reasoning_effort" not in kwargs:
                    return iter([
                        SimpleNamespace(choices=[SimpleNamespace(
                            delta=SimpleNamespace(content=None, reasoning_content="hidden"),
                            finish_reason="length",
                        )]),
                    ])
                return iter([
                    SimpleNamespace(choices=[SimpleNamespace(
                        delta=SimpleNamespace(content=f"子段{len(calls)}"),
                        finish_reason="stop",
                    )]),
                ])

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        gpt._max_retry_attempts = 1
        gpt._retry_base_backoff = 0
        segments = [
            TranscriptSegment(start=i * 10, end=i * 10 + 9, text=(f"第{i}段重要内容。" * 35))
            for i in range(40)
        ]
        source = GPTSource(segment=segments, title="测试", tags=[], checkpoint_key=None)
        chunk = ChunkPayload(
            segments,
            [],
            chunk_index=2,
            chunk_total=2,
            start_seconds=0,
            end_seconds=399,
            estimated_tokens=6000,
        )

        result = gpt._summarize_chunk(source, chunk)

        self.assertIn("子段", result)
        self.assertGreater(len(calls), 2)
        self.assertNotIn("reasoning_effort", calls[0])
        self.assertTrue(all(call.get("reasoning_effort") == "low" for call in calls[1:]))

    def test_parallelism_and_order(self):
        os.environ["SUMMARY_CHUNK_TARGET_TOKENS"] = "6000"
        os.environ["SUMMARY_MAX_CONCURRENCY"] = "3"
        os.environ["SUMMARY_RETRY_ATTEMPTS"] = "1"
        active = 0
        peak = 0
        lock = threading.Lock()

        class Completions:
            def create(self, **kwargs):
                nonlocal active, peak
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.02)
                with lock:
                    active -= 1
                text = kwargs["messages"][0]["content"]
                return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="摘要:" + str(text)[-40:]))])

        gpt = UniversalGPT(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "mock")
        segments = [TranscriptSegment(start=i * 60, end=i * 60 + 50, text=("第%d段。" % i) * 250) for i in range(40)]
        source = GPTSource(segment=segments, title="测试视频", tags=[], checkpoint_key=None)
        result = gpt.summarize(source)
        self.assertTrue(result)
        self.assertLessEqual(peak, 3)


if __name__ == "__main__":
    unittest.main()
