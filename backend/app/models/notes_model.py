from dataclasses import dataclass
from typing import Optional

from app.models.audio_model import AudioDownloadResult
from app.models.transcriber_model import TranscriptResult


@dataclass
class NoteResult:
    markdown: str                  # GPT 总结的 Markdown 内容
    transcript: TranscriptResult                # Whisper 转写结果
    audio_meta: AudioDownloadResult  # 音频下载的元信息（title、duration、封面等）
    # 与状态文件同时保存，便于历史笔记在前端刷新后仍能显示生成用时。
    duration_seconds: Optional[float] = None
