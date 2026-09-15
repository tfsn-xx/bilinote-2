"""Token-aware request chunking with a legacy byte-based compatibility helper."""

import json
import re
from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass
class ChunkPayload:
    segments: list
    image_urls: list
    chunk_index: int = 1
    chunk_total: int = 1
    start_seconds: float | None = None
    end_seconds: float | None = None
    estimated_tokens: int = 0


def estimate_mixed_tokens(text: str) -> int:
    """Tokenizer-free estimate: CJK is ~1 token/char, ASCII ~4 chars/token."""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    ascii_chars = sum(len(item) for item in re.findall(r"[A-Za-z0-9_]+", text))
    other = max(0, len(text) - cjk - ascii_chars)
    return max(1, cjk + (ascii_chars + 3) // 4 + (other + 1) // 2)


def _tokenizer_for(model: str | None):
    try:
        import tiktoken  # type: ignore

        try:
            return tiktoken.encoding_for_model(model or "")
        except Exception:
            return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


class TokenEstimator:
    def __init__(self, model: str | None = None, image_token_estimate: int = 768):
        self.encoder = _tokenizer_for(model)
        self.image_token_estimate = max(64, int(image_token_estimate))

    def text(self, value: str) -> int:
        if not value:
            return 0
        if self.encoder is not None:
            try:
                return len(self.encoder.encode(value))
            except Exception:
                pass
        return estimate_mixed_tokens(value)

    def messages(self, messages: list) -> int:
        total = 8
        for message in messages or []:
            content = message.get("content", "") if isinstance(message, dict) else ""
            if isinstance(content, str):
                total += self.text(content)
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        total += self.text(str(item.get("text", "")))
                    elif item.get("type") == "image_url":
                        total += self.image_token_estimate
        return total


class RequestChunker:
    """Original byte-based chunker retained for existing callers/tests."""

    def __init__(self, message_builder: Callable, max_bytes: int, size_estimator: Optional[Callable] = None):
        self.message_builder = message_builder
        self.max_bytes = max_bytes
        self.size_estimator = size_estimator

    def estimate(self, messages) -> int:
        if self.size_estimator:
            return self.size_estimator(messages)
        return len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    def _messages_size(self, segments, image_urls, **kwargs) -> int:
        return self.estimate(self.message_builder(segments, image_urls, **kwargs))

    def _get_text(self, segment) -> str:
        return segment.get("text", "") if isinstance(segment, dict) else getattr(segment, "text", "")

    def _make_segment(self, segment, text: str):
        if isinstance(segment, dict):
            result = dict(segment)
            result["text"] = text
            return result
        data = dict(getattr(segment, "__dict__", {}))
        data["text"] = text
        return type(segment)(**data)

    def _split_segment_to_fit(self, segment, **kwargs):
        text = self._get_text(segment)
        if not text:
            raise ValueError("empty segment cannot be split")
        lo, hi, best = 1, len(text), None
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = self._make_segment(segment, text[:mid])
            if self._messages_size([candidate], [], **kwargs) <= self.max_bytes:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best is None:
            raise ValueError("single segment too large to fit request")
        return self._make_segment(segment, text[:best]), self._make_segment(segment, text[best:])

    def chunk(self, segments: list, image_urls: list, **kwargs) -> List[ChunkPayload]:
        segments = list(segments or [])
        image_urls = list(image_urls or [])
        if not segments and not image_urls:
            return []
        chunks: List[ChunkPayload] = []
        index = 0
        while index < len(segments):
            batch = []
            while index < len(segments):
                candidate = batch + [segments[index]]
                if self._messages_size(candidate, [], **kwargs) <= self.max_bytes:
                    batch = candidate
                    index += 1
                    continue
                if not batch:
                    head, tail = self._split_segment_to_fit(segments[index], **kwargs)
                    segments[index:index + 1] = [head, tail]
                    continue
                break
            if not batch:
                raise ValueError("unable to fit any content into chunk")
            chunks.append(ChunkPayload(batch, []))
        if not chunks and image_urls:
            chunks = [ChunkPayload([], [])]
        for image_index, image in enumerate(image_urls):
            preferred = min(max(0, len(chunks) - 1), image_index * len(chunks) // len(image_urls))
            for chunk_index in range(preferred, len(chunks)):
                chunk = chunks[chunk_index]
                if self._messages_size(chunk.segments, chunk.image_urls + [image], **kwargs) <= self.max_bytes:
                    chunk.image_urls.append(image)
                    break
            else:
                chunks.append(ChunkPayload([], [image]))
        return chunks

    def group_texts_by_budget(self, texts: List[str], build_messages: Callable, **kwargs) -> List[List[str]]:
        groups: List[List[str]] = []
        index = 0
        while index < len(texts):
            group: List[str] = []
            while index < len(texts):
                candidate = group + [texts[index]]
                try:
                    messages = build_messages(candidate, [], **kwargs)
                except TypeError:
                    messages = build_messages(candidate, **kwargs)
                if self.estimate(messages) <= self.max_bytes:
                    group = candidate
                    index += 1
                    continue
                if not group:
                    raise ValueError("single text block exceeds max_bytes")
                break
            groups.append(group)
        return groups


class TokenBudgetChunker:
    def __init__(
        self,
        message_builder: Callable,
        target_tokens: int,
        model: str | None = None,
        hard_max_bytes: int | None = None,
        image_token_estimate: int = 768,
    ):
        self.message_builder = message_builder
        self.target_tokens = max(256, int(target_tokens))
        self.model = model
        self.hard_max_bytes = hard_max_bytes
        self.estimator = TokenEstimator(model, image_token_estimate)

    @staticmethod
    def _text(segment) -> str:
        return segment.get("text", "") if isinstance(segment, dict) else getattr(segment, "text", "")

    @staticmethod
    def _start(segment) -> float:
        return float(segment.get("start", 0)) if isinstance(segment, dict) else float(getattr(segment, "start", 0))

    @staticmethod
    def _end(segment) -> float:
        if isinstance(segment, dict):
            return float(segment.get("end", TokenBudgetChunker._start(segment)))
        return float(getattr(segment, "end", TokenBudgetChunker._start(segment)))

    @staticmethod
    def _make_segment(segment, text: str, start: float | None = None, end: float | None = None):
        start = TokenBudgetChunker._start(segment) if start is None else start
        end = TokenBudgetChunker._end(segment) if end is None else end
        if isinstance(segment, dict):
            result = dict(segment)
            result.update(text=text, start=start, end=end)
            return result
        data = dict(getattr(segment, "__dict__", {}))
        data.update(text=text, start=start, end=end)
        return type(segment)(**data)

    def _split_long_segment(self, segment) -> list:
        text = self._text(segment).strip()
        if not text:
            return []
        boundaries = [match.end() for match in re.finditer(r"[。！？!?；;\n]+", text)]
        pieces = []
        cursor = 0
        max_chars = max(256, self.target_tokens // 2)
        while cursor < len(text):
            candidate_end = min(len(text), cursor + max_chars)
            safe = [point for point in boundaries if cursor < point <= candidate_end]
            if candidate_end < len(text) and not safe:
                raise ValueError("single transcript segment has no safe sentence boundary")
            end = max(safe) if safe else len(text)
            start = self._start(segment)
            duration = max(0.0, self._end(segment) - start)
            pieces.append(self._make_segment(
                segment,
                text[cursor:end].strip(),
                start + duration * cursor / len(text),
                start + duration * end / len(text),
            ))
            cursor = end
        return pieces

    def _fits(self, segments, images, **kwargs) -> tuple[bool, int]:
        messages = self.message_builder(segments, images, **kwargs)
        tokens = self.estimator.messages(messages)
        if self.hard_max_bytes is not None:
            size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
            if size > self.hard_max_bytes:
                return False, tokens
        return tokens <= self.target_tokens, tokens

    def _balanced_segments(self, segments: list, target_count: int) -> list[list]:
        """Split transcript segments into contiguous, text-weighted groups.

        The requested count is a target rather than permission to cut through a
        subtitle segment.  Token budget checks are applied after this pass and
        may add safety subchunks when a requested group is too large.
        """
        expanded = []
        for segment in list(segments or []):
            text = self._text(segment).strip()
            if not text:
                continue
            if self.estimator.text(text) > self.target_tokens:
                expanded.extend(self._split_long_segment(segment))
            else:
                expanded.append(segment)
        if not expanded:
            return []

        count = min(max(1, int(target_count)), len(expanded))
        weights = [max(1, self.estimator.text(self._text(segment))) for segment in expanded]
        groups: list[list] = []
        cursor = 0
        for group_index in range(count):
            remaining_groups = count - group_index
            remaining_items = len(expanded) - cursor
            if remaining_groups == 1:
                cut = len(expanded)
            else:
                desired = sum(weights[cursor:]) / remaining_groups
                accumulated = 0
                cut = cursor + 1
                max_cut = len(expanded) - (remaining_groups - 1)
                while cut <= max_cut:
                    accumulated += weights[cut - 1]
                    if accumulated >= desired:
                        if cut > cursor + 1:
                            previous_distance = abs(accumulated - weights[cut - 1] - desired)
                            current_distance = abs(accumulated - desired)
                            if previous_distance < current_distance:
                                cut -= 1
                        break
                    cut += 1
                cut = min(max(cut, cursor + 1), max_cut)
            groups.append(expanded[cursor:cut])
            cursor = cut
        return groups

    def _chunk_fixed_count(self, segments: list, image_urls: list, target_count: int, **kwargs) -> List[ChunkPayload]:
        groups = self._balanced_segments(segments, target_count)
        if not groups and image_urls:
            groups = [[]]
        chunks: list[ChunkPayload] = []
        # Preserve the requested layout where safe; fall back to the existing
        # token-budget splitter only for an oversized weighted group.
        for group in groups:
            fits, _ = self._fits(group, [], **kwargs)
            if fits:
                chunks.append(ChunkPayload(group, []))
                continue
            safe = TokenBudgetChunker(
                self.message_builder,
                self.target_tokens,
                self.model,
                self.hard_max_bytes,
                self.estimator.image_token_estimate,
            ).chunk(group, [], **kwargs)
            chunks.extend(safe)
        if not chunks and image_urls:
            chunks = [ChunkPayload([], [])]

        # Screenshots have no transcript metadata of their own, so distribute
        # them by their original order across the ordered chunk layout.
        for image_index, image in enumerate(list(image_urls or [])):
            preferred = min(max(0, len(chunks) - 1), image_index * len(chunks) // max(1, len(image_urls)))
            for chunk_index in range(preferred, len(chunks)):
                chunk = chunks[chunk_index]
                fits, _ = self._fits(chunk.segments, chunk.image_urls + [image], **kwargs)
                if fits:
                    chunk.image_urls.append(image)
                    break
            else:
                chunks.append(ChunkPayload([], [image]))
        return chunks

    def chunk(self, segments: list, image_urls: list, target_chunk_count: int | None = None, **kwargs) -> List[ChunkPayload]:
        if target_chunk_count is not None:
            chunks = self._chunk_fixed_count(segments, image_urls, target_chunk_count, **kwargs)
            total = len(chunks)
            for index, chunk in enumerate(chunks, start=1):
                chunk.chunk_index = index
                chunk.chunk_total = total
                if chunk.segments:
                    chunk.start_seconds = self._start(chunk.segments[0])
                    chunk.end_seconds = self._end(chunk.segments[-1])
                _, chunk.estimated_tokens = self._fits(chunk.segments, chunk.image_urls, **kwargs)
            return chunks

        expanded = []
        for segment in list(segments or []):
            if self.estimator.text(self._text(segment)) > self.target_tokens:
                expanded.extend(self._split_long_segment(segment))
            else:
                expanded.append(segment)
        chunks: list[ChunkPayload] = []
        current: list = []
        for segment in expanded:
            candidate = current + [segment]
            fits, _ = self._fits(candidate, [], **kwargs)
            if fits:
                current = candidate
                continue
            if current:
                chunks.append(ChunkPayload(current, []))
                current = [segment]
                fits, _ = self._fits(current, [], **kwargs)
            if not fits:
                raise ValueError("single transcript segment exceeds summary token budget")
        if current:
            chunks.append(ChunkPayload(current, []))
        if not chunks and image_urls:
            chunks = [ChunkPayload([], [])]
        for image_index, image in enumerate(list(image_urls or [])):
            preferred = min(max(0, len(chunks) - 1), image_index * len(chunks) // max(1, len(image_urls)))
            for chunk_index in range(preferred, len(chunks)):
                chunk = chunks[chunk_index]
                fits, _ = self._fits(chunk.segments, chunk.image_urls + [image], **kwargs)
                if fits:
                    chunk.image_urls.append(image)
                    break
            else:
                chunks.append(ChunkPayload([], [image]))
        total = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            chunk.chunk_index = index
            chunk.chunk_total = total
            if chunk.segments:
                chunk.start_seconds = self._start(chunk.segments[0])
                chunk.end_seconds = self._end(chunk.segments[-1])
            _, chunk.estimated_tokens = self._fits(chunk.segments, chunk.image_urls, **kwargs)
        return chunks
