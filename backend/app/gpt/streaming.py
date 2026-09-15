"""Helpers for consuming OpenAI-compatible streaming completions."""


def collect_text(stream: object) -> str:
    """Return the text from a streamed completion.

    A response object with ``choices[0].message.content`` is accepted only as
    a compatibility fallback for old test doubles.  Production callers must
    pass the iterator returned by ``create(..., stream=True)``.
    """
    if hasattr(stream, "choices"):
        return _response_text(stream)

    parts: list[str] = []
    for chunk in stream:
        for choice in getattr(chunk, "choices", None) or []:
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None)
            if content is None:
                message = getattr(choice, "message", None)
                content = getattr(message, "content", None)
            if isinstance(content, str):
                parts.append(content)
    return "".join(parts).strip()


def _response_text(response: object) -> str:
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise ValueError("AI 模型返回结果格式错误，未找到可用的总结内容。") from exc
    if not isinstance(content, str):
        raise ValueError("AI 模型返回结果格式错误，内容不是文本。")
    return content.strip()
