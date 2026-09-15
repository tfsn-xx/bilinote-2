"""Structured, safe task errors for note generation.

The model/provider error text is intentionally kept out of the public message.
Only a short, redacted summary is exposed to the browser; full exception
details remain in the backend log via ``logger.exception``.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskErrorCode(str, Enum):
    SOURCE_FETCH_FAILED = "SOURCE_FETCH_FAILED"
    SUBTITLE_FETCH_FAILED = "SUBTITLE_FETCH_FAILED"
    AUDIO_DOWNLOAD_FAILED = "AUDIO_DOWNLOAD_FAILED"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"
    TRANSCRIPTION_EMPTY = "TRANSCRIPTION_EMPTY"
    SUMMARY_PREPARATION_FAILED = "SUMMARY_PREPARATION_FAILED"
    MODEL_AUTH_FAILED = "MODEL_AUTH_FAILED"
    MODEL_BAD_REQUEST = "MODEL_BAD_REQUEST"
    MODEL_NOT_AVAILABLE = "MODEL_NOT_AVAILABLE"
    CONTEXT_LENGTH_EXCEEDED = "CONTEXT_LENGTH_EXCEEDED"
    MODEL_CONNECTION_ERROR = "MODEL_CONNECTION_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_RATE_LIMITED = "MODEL_RATE_LIMITED"
    MODEL_UPSTREAM_UNAVAILABLE = "MODEL_UPSTREAM_UNAVAILABLE"
    MODEL_REASONING_EXHAUSTED = "MODEL_REASONING_EXHAUSTED"
    MODEL_RESPONSE_INVALID = "MODEL_RESPONSE_INVALID"
    MERGE_FAILED = "MERGE_FAILED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


_CONTEXT_WORDS = (
    "context length",
    "maximum context",
    "maximum token",
    "max tokens",
    "too many tokens",
    "token limit",
    "上下文长度",
    "最大 token",
    "超出 token",
)
_UPSTREAM_UNAVAILABLE_WORDS = (
    "all available accounts exhausted",
    "all accounts exhausted",
    "no available account",
    "no available channel",
    "no available upstream",
    "upstream capacity exhausted",
    "provider overloaded",
    "上游账号池耗尽",
    "暂无可用账号",
    "无可用渠道",
)
_MODEL_UNAVAILABLE_WORDS = (
    "model_not_found",
    "model not found",
    "model is not supported",
    "is not supported by any configured account",
    "unsupported model",
    "unknown model",
    "模型不存在",
    "模型不支持",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
)
_PATH_PATTERN = re.compile(r"(?i)(?:[a-z]:\\|/)(?:[^\s'\"]+[/\\])*[^\s'\"]+")


def redact_error_text(value: Any, limit: int = 240) -> str:
    """Return a short, non-secret provider summary suitable for the UI."""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[REDACTED]", text)
    text = _PATH_PATTERN.sub("[PATH]", text)
    text = re.sub(r"(?i)sk-[a-z0-9_-]+", "[REDACTED]", text)
    return text[:limit] + ("…" if len(text) > limit else "")


@dataclass
class TaskGenerationError(Exception):
    code: TaskErrorCode | str
    phase: str
    message: str
    retryable: bool = False
    chunk_index: int | None = None
    chunk_total: int | None = None
    attempt: int | None = None
    upstream_summary: str | None = None
    cause: Exception | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        super().__init__(self.message)
        self.code = self.code.value if isinstance(self.code, TaskErrorCode) else str(self.code)
        if self.upstream_summary:
            self.upstream_summary = redact_error_text(self.upstream_summary)

    def with_chunk(self, index: int, total: int) -> "TaskGenerationError":
        self.chunk_index = index
        self.chunk_total = total
        return self

    def as_status(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "phase": self.phase,
            "error_code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        for key in ("chunk_index", "chunk_total", "attempt", "upstream_summary"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def is_context_length_error(exc: Exception) -> bool:
    status = _status_code(exc)
    text = str(exc).lower()
    return status in {400, 413} and any(word in text for word in _CONTEXT_WORDS)


def classify_model_exception(exc: Exception, *, phase: str = "summarize_chunk", attempt: int | None = None) -> TaskGenerationError:
    """Classify OpenAI-compatible SDK and transport errors without guessing."""
    if isinstance(exc, TaskGenerationError):
        if attempt is not None:
            exc.attempt = attempt
        return exc

    status = _status_code(exc)
    raw = str(exc)
    lower = raw.lower()
    name = type(exc).__name__.lower()

    upstream_unavailable = any(word in lower for word in _UPSTREAM_UNAVAILABLE_WORDS)
    model_unavailable = any(word in lower for word in _MODEL_UNAVAILABLE_WORDS)

    if upstream_unavailable or status in {502, 503, 504, 520, 522, 524}:
        code, message, retryable = (
            TaskErrorCode.MODEL_UPSTREAM_UNAVAILABLE,
            "AI 中转站当前没有可用的上游账号或服务容量，请稍后重试或切换供应商。",
            True,
        )
    elif status in {401, 403}:
        code, message, retryable = TaskErrorCode.MODEL_AUTH_FAILED, "AI 模型认证失败：请检查模型供应商的 API Key 和权限。", False
    elif status == 429 or "insufficient quota" in lower or "insufficient_user_quota" in lower or "余额不足" in raw:
        code, message, retryable = TaskErrorCode.MODEL_RATE_LIMITED, "AI 模型请求被限流或余额不足，请稍后重试或检查供应商额度。", True
    elif is_context_length_error(exc):
        code, message, retryable = TaskErrorCode.CONTEXT_LENGTH_EXCEEDED, "AI 模型上下文长度超限：请求内容过大，系统会尝试缩小分段。", False
    elif model_unavailable:
        code, message, retryable = TaskErrorCode.MODEL_NOT_AVAILABLE, "当前供应商不支持所选模型，请更换模型或供应商。", False
    elif "timeout" in lower or "timed out" in lower or "apitimeouterror" in name:
        code, message, retryable = TaskErrorCode.MODEL_TIMEOUT, "AI 模型请求超时，请稍后重试。", True
    elif "connection" in lower or "remoteprotocolerror" in name or "server disconnected" in lower or "connection reset" in lower:
        code, message, retryable = TaskErrorCode.MODEL_CONNECTION_ERROR, "AI 总结请求失败：中转站在返回结果前断开了连接。可能与请求过大、上游超时或中转站限制有关。", True
    elif status is not None and 400 <= status < 500:
        code, message, retryable = TaskErrorCode.MODEL_BAD_REQUEST, "AI 模型请求参数错误，请检查模型名称和供应商配置。", False
    else:
        code, message, retryable = TaskErrorCode.UNKNOWN_ERROR, "AI 模型调用失败，请查看错误摘要或稍后重试。", True

    return TaskGenerationError(
        code=code,
        phase=phase,
        message=message,
        retryable=retryable,
        attempt=attempt,
        upstream_summary=redact_error_text(raw),
        cause=exc,
    )


def classify_pipeline_exception(exc: Exception, *, phase: str, attempt: int | None = None) -> TaskGenerationError:
    if isinstance(exc, TaskGenerationError):
        if attempt is not None:
            exc.attempt = attempt
        return exc
    return TaskGenerationError(
        code=TaskErrorCode.UNKNOWN_ERROR,
        phase=phase,
        message="任务执行失败，请查看错误摘要或稍后重试。",
        retryable=True,
        attempt=attempt,
        upstream_summary=redact_error_text(exc),
        cause=exc,
    )
