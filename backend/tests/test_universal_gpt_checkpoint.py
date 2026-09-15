import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _install_stubs():
    app_mod = types.ModuleType("app")
    gpt_pkg = types.ModuleType("app.gpt")
    models_pkg = types.ModuleType("app.models")

    base_mod = types.ModuleType("app.gpt.base")

    class _GPT:
        pass

    base_mod.GPT = _GPT

    prompt_builder_mod = types.ModuleType("app.gpt.prompt_builder")

    def _generate_base_prompt(**_kwargs):
        return "prompt"

    prompt_builder_mod.generate_base_prompt = _generate_base_prompt

    prompt_mod = types.ModuleType("app.gpt.prompt")
    prompt_mod.BASE_PROMPT = ""
    prompt_mod.AI_SUM = ""
    prompt_mod.SCREENSHOT = ""
    prompt_mod.LINK = ""
    prompt_mod.MERGE_PROMPT = "merge"

    utils_mod = types.ModuleType("app.gpt.utils")

    def _fix_markdown(text):
        return text

    utils_mod.fix_markdown = _fix_markdown

    request_chunker_mod = types.ModuleType("app.gpt.request_chunker")

    class _RequestChunker:
        def __init__(self, *_args, **_kwargs):
            pass

        def group_texts_by_budget(self, texts, _builder, **_kwargs):
            return [texts]

    request_chunker_mod.RequestChunker = _RequestChunker

    gpt_model_mod = types.ModuleType("app.models.gpt_model")

    class _GPTSource:
        pass

    gpt_model_mod.GPTSource = _GPTSource

    transcriber_model_mod = types.ModuleType("app.models.transcriber_model")

    class _TranscriptSegment:
        def __init__(self, **kwargs):
            self.start = kwargs.get("start", 0)
            self.end = kwargs.get("end", 0)
            self.text = kwargs.get("text", "")

    transcriber_model_mod.TranscriptSegment = _TranscriptSegment

    sys.modules.setdefault("app", app_mod)
    sys.modules.setdefault("app.gpt", gpt_pkg)
    sys.modules.setdefault("app.models", models_pkg)
    sys.modules["app.gpt.base"] = base_mod
    sys.modules["app.gpt.prompt_builder"] = prompt_builder_mod
    sys.modules["app.gpt.prompt"] = prompt_mod
    sys.modules["app.gpt.utils"] = utils_mod
    sys.modules["app.gpt.request_chunker"] = request_chunker_mod
    sys.modules["app.models.gpt_model"] = gpt_model_mod
    sys.modules["app.models.transcriber_model"] = transcriber_model_mod


def _load_universal_gpt_class():
    try:
        from app.gpt.universal_gpt import UniversalGPT as installed_class
        return installed_class
    except ModuleNotFoundError:
        pass
    _install_stubs()
    root = pathlib.Path(__file__).resolve().parents[1]
    module_path = root / "app" / "gpt" / "universal_gpt.py"
    spec = importlib.util.spec_from_file_location("universal_gpt", module_path)
    if spec is None or spec.loader is None:
        raise ImportError("universal_gpt module spec not found")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.UniversalGPT


UniversalGPT = _load_universal_gpt_class()


class _FailingCompletions:
    def create(self, **_kwargs):
        raise Exception("Error code: 524 - bad_response_status_code")


class _DummyChat:
    def __init__(self):
        self.completions = _FailingCompletions()


class _DummyModels:
    @staticmethod
    def list():
        return []


class _DummyClient:
    def __init__(self):
        self.chat = _DummyChat()
        self.models = _DummyModels()


class TestUniversalGPTCheckpoint(unittest.TestCase):
    def test_retry_count_is_additional_to_initial_request(self):
        gpt = UniversalGPT(_DummyClient(), model="mock-model")
        gpt.configure_summary(chunk_count=1, max_concurrency=6, retry_count=2)
        gpt._retry_base_backoff = 0
        calls = []

        def fail(_messages):
            calls.append(1)
            raise Exception("connection reset")

        gpt._do_create = fail
        with self.assertRaises(Exception):
            gpt._chat_completion_create([{"role": "user", "content": "x"}])
        self.assertEqual(len(calls), 3)

    def test_retryable_merge_failure_uses_explicit_stitch_mode(self):
        original = os.environ.get("SUMMARY_MERGE_FALLBACK_STITCH")
        os.environ["SUMMARY_MERGE_FALLBACK_STITCH"] = "true"
        try:
            gpt = UniversalGPT(_DummyClient(), model="mock-model")
            result = gpt._merge_partials(["part-a", "part-b"], None, None)
            self.assertEqual(result, "part-a\n\n---\n\npart-b")
            self.assertEqual(gpt.last_merge_mode, "stitched_fallback")
            self.assertEqual(gpt.last_merge_error["error_code"], "UNKNOWN_ERROR")
        finally:
            if original is None:
                os.environ.pop("SUMMARY_MERGE_FALLBACK_STITCH", None)
            else:
                os.environ["SUMMARY_MERGE_FALLBACK_STITCH"] = original

    def test_merge_524_error_persists_checkpoint(self):
        original_attempts = os.environ.get("SUMMARY_RETRY_ATTEMPTS")
        original_fallback = os.environ.get("SUMMARY_MERGE_FALLBACK_STITCH")
        os.environ["SUMMARY_RETRY_ATTEMPTS"] = "1"
        os.environ["SUMMARY_MERGE_FALLBACK_STITCH"] = "false"
        try:
            gpt = UniversalGPT(_DummyClient(), model="mock-model")
            with tempfile.TemporaryDirectory() as tmp_dir:
                gpt.checkpoint_dir = Path(tmp_dir)

                with self.assertRaises(Exception):
                    gpt._merge_partials(["part-a", "part-b"], "task-1", "sig-1")

                checkpoint_path = gpt._checkpoint_path("task-1")
                self.assertTrue(checkpoint_path.exists())
                payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                self.assertEqual(payload["phase"], "merge")
                self.assertEqual(payload["partials"], ["part-a", "part-b"])
        finally:
            if original_attempts is None:
                os.environ.pop("SUMMARY_RETRY_ATTEMPTS", None)
            else:
                os.environ["SUMMARY_RETRY_ATTEMPTS"] = original_attempts
            if original_fallback is None:
                os.environ.pop("SUMMARY_MERGE_FALLBACK_STITCH", None)
            else:
                os.environ["SUMMARY_MERGE_FALLBACK_STITCH"] = original_fallback


if __name__ == "__main__":
    unittest.main()
