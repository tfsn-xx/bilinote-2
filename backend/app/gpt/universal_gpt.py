from app.gpt.base import GPT
from app.gpt.prompt_builder import generate_base_prompt
from app.models.gpt_model import GPTSource
import os
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from app.gpt.prompt import BASE_PROMPT, AI_SUM, SCREENSHOT, LINK, MERGE_PROMPT
from app.gpt.utils import fix_markdown
try:
    from app.gpt.request_chunker import TokenBudgetChunker, TokenEstimator, ChunkPayload
except ImportError:  # compatibility with isolated legacy unit-test loaders
    from app.gpt.request_chunker import RequestChunker as TokenBudgetChunker
    class TokenEstimator:
        def __init__(self, *args, **kwargs):
            pass

        def messages(self, messages):
            return len(json.dumps(messages, ensure_ascii=False))

    class ChunkPayload:
        def __init__(self, segments, image_urls, chunk_index=1, chunk_total=1,
                     start_seconds=None, end_seconds=None, estimated_tokens=0):
            self.segments = segments
            self.image_urls = image_urls
            self.chunk_index = chunk_index
            self.chunk_total = chunk_total
            self.start_seconds = start_seconds
            self.end_seconds = end_seconds
            self.estimated_tokens = estimated_tokens
from app.models.transcriber_model import TranscriptSegment
from datetime import timedelta
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

try:
    from app.utils.task_error import (
        TaskErrorCode,
        TaskGenerationError,
        classify_model_exception,
    )
except ImportError:  # isolated legacy tests provide only app.gpt stubs
    class TaskErrorCode:
        CONTEXT_LENGTH_EXCEEDED = "CONTEXT_LENGTH_EXCEEDED"
        MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
        MODEL_REASONING_EXHAUSTED = "MODEL_REASONING_EXHAUSTED"
        MERGE_FAILED = "MERGE_FAILED"
        CANCELLED = "CANCELLED"
        UNKNOWN_ERROR = "UNKNOWN_ERROR"

    class TaskGenerationError(Exception):
        def __init__(self, code, phase, message, retryable=False, chunk_index=None,
                     chunk_total=None, attempt=None, upstream_summary=None):
            super().__init__(message)
            self.code = getattr(code, "value", code)
            self.phase = phase
            self.message = message
            self.retryable = retryable
            self.chunk_index = chunk_index
            self.chunk_total = chunk_total
            self.attempt = attempt
            self.upstream_summary = upstream_summary

        def as_status(self):
            return {
                key: value for key, value in {
                    "phase": self.phase, "error_code": self.code, "message": self.message,
                    "retryable": self.retryable, "chunk_index": self.chunk_index,
                    "chunk_total": self.chunk_total, "attempt": self.attempt,
                    "upstream_summary": self.upstream_summary,
                }.items() if value is not None
            }

    def classify_model_exception(exc, *, phase="summarize_chunk", attempt=None):
        return TaskGenerationError(TaskErrorCode.UNKNOWN_ERROR, phase, str(exc), True, attempt=attempt)


class UniversalGPT(GPT):
    def __init__(self, client, model: str, temperature: float = 0.7):
        self.client = client
        self.model = model
        self.temperature = temperature
        self.screenshot = False
        self.link = False
        self.max_request_bytes = int(os.getenv("OPENAI_MAX_REQUEST_BYTES", str(45 * 1024 * 1024)))
        self.checkpoint_dir = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_target_tokens = max(256, int(os.getenv("SUMMARY_CHUNK_TARGET_TOKENS", "6000")))
        self.merge_target_tokens = max(256, int(os.getenv("SUMMARY_MERGE_TARGET_TOKENS", "6000")))
        self.max_concurrency = max(1, int(os.getenv("SUMMARY_MAX_CONCURRENCY", "3")))
        self._max_retry_attempts = max(1, int(os.getenv("SUMMARY_RETRY_ATTEMPTS", os.getenv("OPENAI_RETRY_ATTEMPTS", "2"))))
        self._retry_base_backoff = float(os.getenv("SUMMARY_RETRY_BACKOFF_SECONDS", os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "1.5")))
        self.image_token_estimate = max(64, int(os.getenv("SUMMARY_IMAGE_TOKEN_ESTIMATE", "768")))
        streaming_value = os.getenv("SUMMARY_STREAMING", os.getenv("SUMMARY_MERGE_STREAMING", "true"))
        self.summary_streaming = streaming_value.lower() not in ("0", "false", "no")
        self.partial_stream_fallback = os.getenv("SUMMARY_PARTIAL_STREAM_FALLBACK", "true").lower() not in (
            "0", "false", "no"
        )
        self.partial_stream_min_chars = max(1, int(os.getenv("SUMMARY_PARTIAL_STREAM_MIN_CHARS", "200")))
        self.reasoning_fallback_effort = os.getenv("SUMMARY_REASONING_FALLBACK_EFFORT", "low").strip() or "low"
        self.reasoning_fallback_max_tokens = max(
            1024, int(os.getenv("SUMMARY_REASONING_FALLBACK_MAX_TOKENS", "16384"))
        )
        self.merge_fallback_stitch = os.getenv("SUMMARY_MERGE_FALLBACK_STITCH", "false").lower() not in ("0", "false", "no")
        self.summary_chunk_count = 1
        self.last_merge_mode = "not_applicable"
        self.last_merge_error: dict | None = None

    def configure_summary(self, *, chunk_count: int = 1, max_concurrency: int = 6, retry_count: int = 2) -> None:
        self.summary_chunk_count = min(50, max(1, int(chunk_count)))
        self.max_concurrency = min(32, max(1, int(max_concurrency)))
        # retry_count is the number of retries after the initial request.
        self._max_retry_attempts = min(11, max(1, int(retry_count) + 1))

    def _format_time(self, seconds: float) -> str:
        return str(timedelta(seconds=int(seconds)))[2:]

    def _build_segment_text(self, segments: List[TranscriptSegment]) -> str:
        return "\n".join(
            f"{self._format_time(seg.start)} - {seg.text.strip()}"
            for seg in segments
        )

    def ensure_segments_type(self, segments) -> List[TranscriptSegment]:
        return [TranscriptSegment(**seg) if isinstance(seg, dict) else seg for seg in segments]

    def create_messages(self, segments: List[TranscriptSegment], **kwargs):

        content_text = generate_base_prompt(
            title=kwargs.get('title'),
            segment_text=self._build_segment_text(segments),
            tags=kwargs.get('tags'),
            _format=kwargs.get('_format'),
            style=kwargs.get('style'),
            extras=kwargs.get('extras'),
        )

        video_img_urls = kwargs.get('video_img_urls', [])
        chunk_index = kwargs.get("chunk_index")
        chunk_total = kwargs.get("chunk_total")
        start_seconds = kwargs.get("start_seconds")
        end_seconds = kwargs.get("end_seconds")
        if chunk_index and chunk_total:
            time_range = f"{self._format_time(start_seconds or 0)} - {self._format_time(end_seconds or 0)}"
            chunk_instruction = (
                f"\\n\\n你正在处理视频的第 {chunk_index}/{chunk_total} 段，原始时间范围：{time_range}。"
                "只总结这一段，不要臆测其他分段内容；保留本段重要事实、人物、观点、公式、"
                "视觉信息和原片时间标记。\\n"
            )
            if isinstance(content_text, str):
                content_text = chunk_instruction + content_text

        content: list[dict] | str
        if video_img_urls:
            # 有截图时走 OpenAI 多模态 content 数组（text + image_url）
            content = [{"type": "text", "text": content_text}]
            for url in video_img_urls:
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": url,
                        "detail": "auto"
                    }
                })
        else:
            # 纯文本场景退回 string content：DeepSeek deepseek-chat 等非多模态模型
            # 不识别 [{"type":"text",...}] 数组形态，会返回 invalid_request_error
            # （issue #282）。OpenAI 规范本身也允许 content 为 string。
            content = content_text

        messages = [{
            "role": "user",
            "content": content
        }]

        return messages

    def list_models(self):
        return self.client.models.list()

    def _estimate_messages_bytes(self, messages: list) -> int:
        import json
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    def _build_merge_messages(self, partials: list) -> list:
        merge_text = MERGE_PROMPT + "\n\n" + "\n\n---\n\n".join(partials)
        # 合并阶段没有图片，直接用 string content 兼容非多模态模型（issue #282）
        return [{
            "role": "user",
            "content": merge_text
        }]

    def _checkpoint_path(self, checkpoint_key: str) -> Path:
        safe_key = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in checkpoint_key)
        return self.checkpoint_dir / f"{safe_key}.gpt.checkpoint.json"

    def _build_source_signature(self, source: GPTSource) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "max_request_bytes": self.max_request_bytes,
            "title": source.title,
            "tags": source.tags,
            "format": source._format,
            "style": source.style,
            "extras": source.extras,
            "video_img_urls": source.video_img_urls or [],
            "chunk_target_tokens": self.chunk_target_tokens,
            "merge_target_tokens": self.merge_target_tokens,
            "summary_chunk_count": self.summary_chunk_count,
            "summary_max_concurrency": self.max_concurrency,
            "summary_retry_attempts": self._max_retry_attempts,
            "chunking_version": 3,
            "segments": [
                {
                    "start": getattr(seg, "start", None),
                    "end": getattr(seg, "end", None),
                    "text": getattr(seg, "text", "")
                }
                for seg in source.segment
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_checkpoint(self, checkpoint_key: str, source_signature: str) -> dict | None:
        path = self._checkpoint_path(checkpoint_key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("source_signature") != source_signature:
                path.unlink(missing_ok=True)
                return None
            return data
        except Exception:
            path.unlink(missing_ok=True)
            return None

    def _save_checkpoint(self, checkpoint_key: str, source_signature: str, partials: list, phase: str, **extra) -> None:
        path = self._checkpoint_path(checkpoint_key)
        data = {
            "version": 2,
            "source_signature": source_signature,
            "phase": phase,
            "partials": partials,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        data.update(extra)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _clear_checkpoint(self, checkpoint_key: str) -> None:
        self._checkpoint_path(checkpoint_key).unlink(missing_ok=True)

    @staticmethod
    def _is_insufficient_quota_error(exc: Exception) -> bool:
        raw = str(exc)
        return (
            "insufficient_user_quota" in raw
            or "预扣费额度失败" in raw
            or "insufficient quota" in raw.lower()
        )

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        return classify_model_exception(exc).retryable

    @staticmethod
    def _is_temperature_unsupported_error(exc: Exception) -> bool:
        """OpenAI o1/o3/gpt-5 系列等新模型不接受自定义 temperature，
        只允许默认值 1，传 0.7 会报 `'temperature' does not support 0.7 ...`。"""
        raw = str(exc).lower()
        return "temperature" in raw and (
            "does not support" in raw
            or "unsupported_value" in raw
            or "only the default" in raw
        )

    @staticmethod
    def _is_option_unsupported_error(exc: Exception, option: str) -> bool:
        raw = str(exc).lower()
        return option.lower() in raw and any(marker in raw for marker in (
            "unsupported", "not support", "unknown parameter", "unrecognized",
            "not permitted", "extra inputs", "invalid parameter",
        ))

    def _do_create(self, messages: list, **request_options):
        """单次调用。如果模型拒绝自定义 temperature，就地去掉该参数再试一次
        （不消耗外层的重试次数预算），仍失败则把异常抛给外层重试逻辑。"""
        options = dict(request_options)
        include_temperature = True
        for _ in range(5):
            kwargs = {"model": self.model, "messages": messages, **options}
            if include_temperature:
                kwargs["temperature"] = self.temperature
            try:
                return self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                if include_temperature and self._is_temperature_unsupported_error(exc):
                    include_temperature = False
                    print(f"[universal_gpt] 模型 {self.model} 不支持自定义 temperature，改用默认值重试")
                    continue
                removed_option = None
                for option in ("reasoning_effort", "max_completion_tokens"):
                    if option in options and self._is_option_unsupported_error(exc, option):
                        removed_option = option
                        options.pop(option, None)
                        print(f"[universal_gpt] 中转站不支持 {option}，移除该兼容参数后重试")
                        break
                if removed_option:
                    continue
                raise
        raise RuntimeError("chat completion option negotiation exhausted")

    def _chat_completion_create(self, messages: list, *, phase: str = "summarize_chunk"):
        """Create a non-streaming completion with the configured retry budget."""
        last_exc = None
        for attempt in range(1, self._max_retry_attempts + 1):
            try:
                return self._do_create(messages)
            except Exception as exc:
                last_exc = exc
                classified = classify_model_exception(exc, phase=phase, attempt=attempt)
                if attempt >= self._max_retry_attempts or not classified.retryable:
                    raise classified from exc
                sleep_seconds = self._retry_base_backoff * (2 ** (attempt - 1))
                time.sleep(sleep_seconds)
        if last_exc is not None:
            raise classify_model_exception(last_exc, phase=phase, attempt=self._max_retry_attempts) from last_exc
        raise RuntimeError("chat completion failed without exception")

    @staticmethod
    def _append_with_overlap(existing: str, continuation: str) -> str:
        if not existing:
            return continuation
        if not continuation:
            return existing
        max_overlap = min(len(existing), len(continuation), 800)
        for size in range(max_overlap, 19, -1):
            if existing[-size:] == continuation[:size]:
                return existing + continuation[size:]
        separator = ""
        if existing[-1:].isascii() and continuation[:1].isascii():
            if existing[-1:].isalnum() and continuation[:1].isalnum():
                separator = " "
        return existing + separator + continuation

    @staticmethod
    def _continuation_messages(messages: list, partial: str) -> list:
        return list(messages) + [
            {"role": "assistant", "content": partial},
            {
                "role": "user",
                "content": (
                    "上一次流式传输中断。请只从上面回答的末尾继续，"
                    "不要重写、不要重复已经输出的内容；尽快完成剩余 Markdown。"
                ),
            },
        ]

    @staticmethod
    def _direct_answer_messages(messages: list) -> list:
        return list(messages) + [{
            "role": "user",
            "content": (
                "上一次生成把全部输出额度耗在了内部推理，没有返回正文。"
                "这次不要展开或输出思考过程，直接生成最终 Markdown 笔记；"
                "优先完成全部必要章节和事实，避免冗长分析。"
            ),
        }]

    @staticmethod
    def _field(value: object, name: str, default=None):
        if isinstance(value, dict):
            return value.get(name, default)
        return getattr(value, name, default)

    @classmethod
    def _content_parts(cls, content: object) -> list[str]:
        if isinstance(content, str):
            return [content]
        if not isinstance(content, list):
            return []
        parts: list[str] = []
        for block in content:
            text = cls._field(block, "text")
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(text, dict) and isinstance(text.get("value"), str):
                parts.append(text["value"])
        return parts

    def _chat_completion_stream_content(
        self,
        messages: list,
        *,
        phase: str,
        reasoning_fallback: bool = False,
    ) -> str:
        """Consume a streamed completion and retain useful partial output on disconnect."""
        last_exc = None
        accumulated = ""
        request_messages = self._direct_answer_messages(messages) if reasoning_fallback else messages
        request_options = {"stream": True}
        if reasoning_fallback:
            request_options.update({
                "reasoning_effort": self.reasoning_fallback_effort,
                "max_completion_tokens": self.reasoning_fallback_max_tokens,
            })
        for attempt in range(1, self._max_retry_attempts + 1):
            attempt_parts: list[str] = []
            event_count = 0
            choice_event_count = 0
            delta_fields: set[str] = set()
            finish_reasons: set[str] = set()
            content_types: set[str] = set()
            try:
                response = self._do_create(request_messages, **request_options)
                if hasattr(response, "choices"):
                    return self._response_content(response, phase=phase)
                for event in response:
                    event_count += 1
                    choices = self._field(event, "choices") or []
                    if not choices:
                        continue
                    choice_event_count += 1
                    for choice in choices:
                        finish_reason = self._field(choice, "finish_reason")
                        if finish_reason:
                            finish_reasons.add(str(finish_reason))
                        delta = self._field(choice, "delta")
                        if delta is not None:
                            if isinstance(delta, dict):
                                delta_fields.update(str(key) for key, value in delta.items() if value is not None)
                            else:
                                model_fields_set = getattr(delta, "model_fields_set", None)
                                if model_fields_set:
                                    delta_fields.update(str(field) for field in model_fields_set)
                                elif hasattr(delta, "model_dump"):
                                    delta_fields.update(str(field) for field in delta.model_dump(exclude_none=True).keys())
                                elif hasattr(delta, "__dict__"):
                                    delta_fields.update(
                                        str(field) for field, value in vars(delta).items() if value is not None
                                    )
                        content = self._field(delta, "content") if delta is not None else None
                        if content is None:
                            message = self._field(choice, "message")
                            content = self._field(message, "content") if message is not None else None
                        if content is None:
                            content = self._field(choice, "text")
                        extracted = self._content_parts(content)
                        attempt_parts.extend(extracted)
                        if content is not None and not extracted:
                            content_types.add(type(content).__name__)
                content = "".join(attempt_parts).strip()
                if not content:
                    stream_summary = (
                        f"events={event_count}, choices={choice_event_count}, "
                        f"delta_fields={','.join(sorted(delta_fields)) or 'none'}, "
                        f"finish_reasons={','.join(sorted(finish_reasons)) or 'none'}, "
                        f"content_types={','.join(sorted(content_types)) or 'none'}"
                    )
                    print(f"[universal_gpt] empty stream phase={phase} attempt={attempt} {stream_summary}")
                    reasoning_exhausted = "reasoning_content" in delta_fields and "length" in finish_reasons
                    if reasoning_exhausted:
                        raise TaskGenerationError(
                            TaskErrorCode.MODEL_REASONING_EXHAUSTED,
                            phase,
                            "AI 模型把输出额度耗在了推理阶段，未返回笔记正文。",
                            retryable=True,
                            attempt=attempt,
                            upstream_summary=stream_summary,
                        )
                    raise TaskGenerationError(
                        TaskErrorCode.MODEL_RESPONSE_INVALID,
                        phase,
                        "AI 模型返回了空的流式结果，系统自动重试后仍未获得总结内容。",
                        retryable=True,
                        attempt=attempt,
                        upstream_summary=stream_summary,
                    )
                return self._append_with_overlap(accumulated, content).strip()
            except Exception as exc:
                last_exc = exc
                partial = "".join(attempt_parts).strip()
                if partial:
                    accumulated = self._append_with_overlap(accumulated, partial).strip()
                classified = exc if isinstance(exc, TaskGenerationError) else classify_model_exception(
                    exc, phase=phase, attempt=attempt
                )
                classified.attempt = attempt
                if (
                    attempt < self._max_retry_attempts
                    and classified.code == TaskErrorCode.MODEL_RESPONSE_INVALID.value
                    and request_options.get("stream") is True
                    and "events=0," in (classified.upstream_summary or "")
                ):
                    request_options["stream"] = False
                    print(
                        "[universal_gpt] 中转站返回零事件空流，下一次改用非流式兼容请求，"
                        f"phase={phase} attempt={attempt}"
                    )
                if attempt >= self._max_retry_attempts or not classified.retryable:
                    if (
                        len(accumulated) >= self.partial_stream_min_chars
                        and self.partial_stream_fallback
                        and classified.retryable
                    ):
                        print(
                            "[universal_gpt] 流式连接最终中断，保留已接收内容，"
                            f"phase={phase} chars={len(accumulated)}"
                        )
                        return accumulated
                    raise classified from exc
                if classified.code == TaskErrorCode.MODEL_REASONING_EXHAUSTED.value:
                    request_messages = self._direct_answer_messages(messages)
                    request_options.update({
                        "reasoning_effort": self.reasoning_fallback_effort,
                        "max_completion_tokens": self.reasoning_fallback_max_tokens,
                    })
                    print(
                        "[universal_gpt] 推理耗尽输出额度，下一次改用低推理并直接输出正文，"
                        f"phase={phase} attempt={attempt}"
                    )
                elif accumulated:
                    request_messages = self._continuation_messages(messages, accumulated)
                    print(
                        "[universal_gpt] 流式连接中断，将从已接收内容续写，"
                        f"phase={phase} attempt={attempt} chars={len(accumulated)}"
                    )
                sleep_seconds = self._retry_base_backoff * (2 ** (attempt - 1))
                time.sleep(sleep_seconds)
        raise classify_model_exception(last_exc, phase=phase, attempt=self._max_retry_attempts) from last_exc

    def _chat_completion_content(
        self,
        messages: list,
        *,
        phase: str,
        reasoning_fallback: bool = False,
    ) -> str:
        if self.summary_streaming:
            return self._chat_completion_stream_content(
                messages,
                phase=phase,
                reasoning_fallback=reasoning_fallback,
            )
        return self._response_content(
            self._chat_completion_create(messages, phase=phase),
            phase=phase,
        )

    @staticmethod
    def _response_content(response: object, *, phase: str = "summarize_chunk") -> str:
        if isinstance(response, str):
            content = response.strip()
            if content:
                return content
            raise TaskGenerationError(
                TaskErrorCode.MODEL_RESPONSE_INVALID,
                phase,
                "AI 模型返回结果为空，无法生成笔记。",
                retryable=False,
            )
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise TaskGenerationError(
                TaskErrorCode.MODEL_RESPONSE_INVALID,
                phase,
                "AI 模型返回结果格式错误，未找到可用的总结内容。",
                retryable=False,
                upstream_summary=str(exc),
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise TaskGenerationError(
                TaskErrorCode.MODEL_RESPONSE_INVALID,
                phase,
                "AI 模型返回结果为空，无法生成笔记。",
                retryable=False,
            )
        return content.strip()

    @staticmethod
    def _check_cancel(source: GPTSource) -> None:
        callback = source.cancel_check
        if callback and callback():
            raise TaskGenerationError(TaskErrorCode.CANCELLED, "summarize_chunk", "任务已由用户取消。", False)

    def _notify_progress(self, source: GPTSource, **payload) -> None:
        callback = source.progress_callback
        if callback:
            callback(**payload)

    def _summarize_chunk(
        self,
        source: GPTSource,
        chunk: ChunkPayload,
        *,
        allow_shrink: bool = True,
        reasoning_fallback: bool = False,
    ) -> str:
        self._check_cancel(source)
        kwargs = {
            "title": source.title,
            "tags": source.tags,
            "video_img_urls": chunk.image_urls,
            "_format": source._format,
            "style": source.style,
            "extras": source.extras,
            "chunk_index": chunk.chunk_index,
            "chunk_total": chunk.chunk_total,
            "start_seconds": chunk.start_seconds,
            "end_seconds": chunk.end_seconds,
        }
        try:
            return self._chat_completion_content(
                self.create_messages(chunk.segments, **kwargs),
                phase="summarize_chunk",
                reasoning_fallback=reasoning_fallback,
            )
        except TaskGenerationError as exc:
            shrink_codes = {
                TaskErrorCode.CONTEXT_LENGTH_EXCEEDED.value,
                TaskErrorCode.MODEL_REASONING_EXHAUSTED.value,
            }
            if exc.code in shrink_codes and allow_shrink:
                if exc.code == TaskErrorCode.MODEL_REASONING_EXHAUSTED.value:
                    target_tokens = max(1024, int(max(chunk.estimated_tokens, 2048) * 0.55))
                else:
                    target_tokens = max(256, int(self.chunk_target_tokens * 0.6))
                smaller = TokenBudgetChunker(
                    lambda segments, images, **kw: self.create_messages(segments, **kw),
                    target_tokens,
                    self.model,
                    self.max_request_bytes,
                    self.image_token_estimate,
                )
                try:
                    subchunks = smaller.chunk(chunk.segments, chunk.image_urls, **{
                        "title": source.title, "tags": source.tags, "_format": source._format,
                        "style": source.style, "extras": source.extras,
                    })
                except ValueError:
                    raise
                if len(subchunks) > 1:
                    if exc.code == TaskErrorCode.MODEL_REASONING_EXHAUSTED.value:
                        print(
                            "[universal_gpt] 低推理重试仍未返回正文，缩小当前分段后串行处理，"
                            f"chunk={chunk.chunk_index}/{chunk.chunk_total} subchunks={len(subchunks)}"
                        )
                    texts = []
                    for subchunk in subchunks:
                        texts.append(self._summarize_chunk(
                            source,
                            subchunk,
                            allow_shrink=False,
                            reasoning_fallback=exc.code == TaskErrorCode.MODEL_REASONING_EXHAUSTED.value,
                        ))
                    return "\\n\\n".join(texts)
            exc.chunk_index = chunk.chunk_index
            exc.chunk_total = chunk.chunk_total
            raise

    def _group_partials(self, partials: list[str]) -> list[list[str]]:
        estimator = TokenEstimator(self.model, self.image_token_estimate)
        groups: list[list[str]] = []
        current: list[str] = []
        for partial in partials:
            candidate = current + [partial]
            if estimator.messages(self._build_merge_messages(candidate)) <= self.merge_target_tokens:
                current = candidate
            else:
                if not current:
                    raise TaskGenerationError(
                        TaskErrorCode.MERGE_FAILED, "merging",
                        "分段汇总结果仍超过模型上下文预算，无法继续汇总。",
                        retryable=False,
                    )
                groups.append(current)
                current = [partial]
        if current:
            groups.append(current)
        return groups

    @staticmethod
    def _stitch_partials(partials: list[str]) -> str:
        return "\n\n---\n\n".join(part.strip() for part in partials if part and part.strip())

    def _merge_partials(self, partials: list, checkpoint_key: str | None, source_signature: str | None) -> str:
        def build_messages(texts, *_args, **_kwargs):
            return self._build_merge_messages(texts)

        current_partials = list(partials)
        used_stitch = False
        while len(current_partials) > 1:
            groups = self._group_partials(current_partials)
            if len(groups) == len(current_partials) and self.merge_fallback_stitch:
                self.last_merge_mode = "stitched_fallback"
                used_stitch = True
                self.last_merge_error = {
                    "error_code": TaskErrorCode.MERGE_FAILED.value,
                    "message": "分段结果无法放入安全的合并请求，已按原顺序拼接。",
                }
                active_source = getattr(self, "_active_source", None)
                if active_source is not None:
                    self._notify_progress(
                        active_source,
                        phase="merging",
                        merge_mode=self.last_merge_mode,
                        merge_warning=self.last_merge_error["message"],
                    )
                return self._stitch_partials(current_partials)
            new_partials = []
            for group_idx, group in enumerate(groups):
                messages = build_messages(group)
                try:
                    merged_text = self._chat_completion_content(messages, phase="merging")
                except Exception as exc:
                    classified = exc if isinstance(exc, TaskGenerationError) else classify_model_exception(exc, phase="merging")
                    can_stitch = classified.retryable or classified.code == TaskErrorCode.MODEL_RESPONSE_INVALID.value
                    if self.merge_fallback_stitch and can_stitch:
                        self.last_merge_mode = "stitched_fallback"
                        used_stitch = True
                        self.last_merge_error = {
                            "error_code": classified.code,
                            "attempt": classified.attempt,
                            "upstream_summary": classified.upstream_summary,
                        }
                        merged_text = self._stitch_partials(group)
                        if checkpoint_key and source_signature:
                            self._save_checkpoint(checkpoint_key, source_signature, new_partials + [merged_text] + [item for rest in groups[group_idx + 1:] for item in rest], "merge")
                        active_source = getattr(self, "_active_source", None)
                        if active_source is not None:
                            self._notify_progress(
                                active_source,
                                phase="merging",
                                merge_mode=self.last_merge_mode,
                                merge_warning="AI 汇总失败，已按分段顺序拼接。",
                                merge_error_code=classified.code,
                                merge_attempt=classified.attempt,
                                merge_upstream_summary=classified.upstream_summary,
                            )
                    else:
                        if checkpoint_key and source_signature:
                            self._save_checkpoint(checkpoint_key, source_signature, current_partials, "merge")
                        classified.phase = "merging"
                        classified.code = TaskErrorCode.MERGE_FAILED.value if classified.code == TaskErrorCode.UNKNOWN_ERROR.value else classified.code
                        raise classified

                new_partials.append(merged_text)

                if checkpoint_key and source_signature:
                    remaining_partials = []
                    for remaining_group in groups[group_idx + 1:]:
                        remaining_partials.extend(remaining_group)
                    resumable_partials = new_partials + remaining_partials
                    self._save_checkpoint(checkpoint_key, source_signature, resumable_partials, "merge")

            current_partials = new_partials

        if not used_stitch:
            self.last_merge_mode = "ai_merged"
        return current_partials[0]

    def summarize(self, source: GPTSource) -> str:
        self._active_source = source
        self.last_merge_mode = "not_applicable"
        self.last_merge_error = None
        self.screenshot = source.screenshot
        self.link = source.link
        source.segment = self.ensure_segments_type(source.segment)
        checkpoint_key = source.checkpoint_key
        source_signature = self._build_source_signature(source) if checkpoint_key else None

        def message_builder(segments, image_urls, **kwargs):
            return self.create_messages(segments, video_img_urls=image_urls, **kwargs)

        chunker = TokenBudgetChunker(
            message_builder,
            self.chunk_target_tokens,
            self.model,
            self.max_request_bytes,
            self.image_token_estimate,
        )

        try:
            chunks = chunker.chunk(
                source.segment,
                source.video_img_urls or [],
                title=source.title,
                tags=source.tags,
                _format=source._format,
                style=source.style,
                extras=source.extras,
                target_chunk_count=self.summary_chunk_count,
            )
        except ValueError as exc:
            raise TaskGenerationError(
                TaskErrorCode.SUMMARY_PREPARATION_FAILED,
                "summarize_prepare",
                "转写内容无法按安全的句子边界切分到模型预算内。",
                retryable=False,
                upstream_summary=str(exc),
            ) from exc

        partials: list[str | None] = [None] * len(chunks)
        merge_resume_partials: list[str] | None = None
        if checkpoint_key and source_signature:
            checkpoint = self._load_checkpoint(checkpoint_key, source_signature)
            if checkpoint and isinstance(checkpoint.get("partials"), list):
                old_partials = checkpoint["partials"]
                if checkpoint.get("phase") == "merge":
                    saved = [value for value in old_partials if isinstance(value, str) and value.strip()]
                    if saved:
                        merge_resume_partials = saved
                else:
                    for index, value in enumerate(old_partials[:len(chunks)]):
                        if isinstance(value, str) and value.strip():
                            partials[index] = value

        pending = [] if merge_resume_partials is not None else [index for index, value in enumerate(partials) if value is None]
        lock = Lock()
        completed = len(chunks) - len(pending)
        chunk_layout = [{
            "index": c.chunk_index,
            "total": c.chunk_total,
            "start_seconds": c.start_seconds,
            "end_seconds": c.end_seconds,
            "estimated_tokens": c.estimated_tokens,
        } for c in chunks]
        self._notify_progress(source, phase="summarizing_chunks", completed=completed, total=len(chunks))

        def run_one(index: int):
            return index, self._summarize_chunk(source, chunks[index])

        if pending:
            with ThreadPoolExecutor(max_workers=min(self.max_concurrency, len(pending))) as executor:
                futures = {executor.submit(run_one, index): index for index in pending}
                first_error = None
                for future in as_completed(futures):
                    try:
                        index, text = future.result()
                        with lock:
                            partials[index] = text
                            completed += 1
                            if checkpoint_key and source_signature:
                                self._save_checkpoint(
                                    checkpoint_key, source_signature, partials, "summarize_chunks",
                                    chunk_layout=chunk_layout,
                                )
                        self._notify_progress(source, phase="summarizing_chunks", completed=completed, total=len(chunks))
                    except Exception as exc:
                        if first_error is None:
                            first_error = exc
                if first_error is not None:
                    if checkpoint_key and source_signature:
                        error_status = first_error.as_status() if isinstance(first_error, TaskGenerationError) else {
                            "error_code": TaskErrorCode.UNKNOWN_ERROR.value,
                            "message": str(first_error),
                        }
                        self._save_checkpoint(
                            checkpoint_key,
                            source_signature,
                            partials,
                            "summarize_chunks",
                            chunk_layout=chunk_layout,
                            last_error=error_status,
                        )
                    if isinstance(first_error, TaskGenerationError):
                        raise first_error
                    raise TaskGenerationError(
                        TaskErrorCode.UNKNOWN_ERROR,
                        "summarize_chunk",
                        "分段总结失败。",
                        True,
                        upstream_summary=str(first_error),
                    ) from first_error

        completed_partials = merge_resume_partials or [value for value in partials if isinstance(value, str)]
        if merge_resume_partials is None and len(completed_partials) != len(chunks):
            raise TaskGenerationError(TaskErrorCode.MODEL_RESPONSE_INVALID, "summarize_chunk", "分段总结结果不完整，未生成最终笔记。", False)

        if len(completed_partials) == 1:
            if checkpoint_key:
                self._clear_checkpoint(checkpoint_key)
            return completed_partials[0]
        self._notify_progress(source, phase="merging", completed=0, total=len(completed_partials))
        merged = self._merge_partials(completed_partials, checkpoint_key, source_signature)
        self._notify_progress(source, phase="merging", completed=1, total=1)
        if checkpoint_key:
            self._clear_checkpoint(checkpoint_key)
        return merged
