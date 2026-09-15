import json
import logging
import os
import threading
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional, Tuple, Union, Any

from fastapi import HTTPException
from pydantic import HttpUrl
from dotenv import load_dotenv

from app.downloaders.base import Downloader
from app.downloaders.bilibili_downloader import BilibiliDownloader
from app.downloaders.douyin_downloader import DouyinDownloader
from app.downloaders.local_downloader import LocalDownloader
from app.downloaders.youtube_downloader import YoutubeDownloader
from app.db.video_task_dao import delete_task_by_video, insert_video_task
from app.enmus.exception import NoteErrorEnum, ProviderErrorEnum
from app.enmus.task_status_enums import TaskStatus
from app.enmus.note_enums import DownloadQuality
from app.exceptions.note import NoteError
from app.exceptions.provider import ProviderError
from app.gpt.base import GPT
from app.gpt.gpt_factory import GPTFactory
from app.models.audio_model import AudioDownloadResult
from app.models.gpt_model import GPTSource
from app.models.model_config import ModelConfig
from app.models.notes_model import AudioDownloadResult, NoteResult
from app.models.transcriber_model import TranscriptResult, TranscriptSegment
from app.services.constant import SUPPORT_PLATFORM_MAP
from app.services.provider import ProviderService
from app.transcriber.base import Transcriber
from app.transcriber.transcriber_provider import get_transcriber, _transcribers
from app.utils.note_helper import replace_content_markers, prepend_source_link
from app.utils.screenshot_marker import extract_screenshot_timestamps
from app.utils.status_code import StatusCode
from app.utils.video_helper import generate_screenshot
from app.utils.video_reader import VideoReader
from app.utils.task_error import (
    TaskErrorCode,
    TaskGenerationError,
    classify_pipeline_exception,
)

# ------------------ 环境变量与全局配置 ------------------

# 从 .env 文件中加载环境变量
load_dotenv()

# 后端 API 地址与端口（若有需要可以在代码其他部分使用 BACKEND_BASE_URL）
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost")
BACKEND_PORT = os.getenv("BACKEND_PORT", "8483")
BACKEND_BASE_URL = f"{API_BASE_URL}:{BACKEND_PORT}"

# 输出目录（用于缓存音频、转写、Markdown 文件，以及存储截图）
NOTE_OUTPUT_DIR = Path(os.getenv("NOTE_OUTPUT_DIR", "note_results"))
NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_OUTPUT_DIR = os.getenv("OUT_DIR", "./static/screenshots")
# 图片基础 URL（用于生成 Markdown 中的图片链接，需前端静态目录对应）
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "/static/screenshots")

# 日志配置
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


_STATUS_MESSAGES = {
    TaskStatus.PENDING: "任务排队中",
    TaskStatus.PARSING: "正在解析视频链接",
    TaskStatus.DOWNLOADING: "正在准备视频和音频",
    TaskStatus.TRANSCRIBING: "正在获取字幕或转写音频",
    TaskStatus.SUMMARIZING: "正在调用模型总结内容",
    TaskStatus.FORMATTING: "正在整理笔记格式",
    TaskStatus.SAVING: "正在保存笔记",
    TaskStatus.SUCCESS: "生成完成",
    TaskStatus.FAILED: "生成失败",
}
_TERMINAL_STATUSES = {TaskStatus.SUCCESS.value, TaskStatus.FAILED.value}
_TASK_CANCEL_EVENTS: dict[str, threading.Event] = {}
_TASK_CANCEL_LOCK = threading.Lock()


def request_task_cancel(task_id: str) -> bool:
    with _TASK_CANCEL_LOCK:
        _TASK_CANCEL_EVENTS.setdefault(task_id, threading.Event()).set()
    return True


def _task_cancelled(task_id: Optional[str]) -> bool:
    with _TASK_CANCEL_LOCK:
        event = _TASK_CANCEL_EVENTS.get(task_id) if task_id else None
        return bool(event and event.is_set())


def _clear_task_cancel(task_id: Optional[str]) -> None:
    if task_id:
        with _TASK_CANCEL_LOCK:
            _TASK_CANCEL_EVENTS.pop(task_id, None)


def reset_task_cancel(task_id: Optional[str]) -> None:
    _clear_task_cancel(task_id)


class NoteGenerator:
    """
    NoteGenerator 用于执行视频/音频下载、转写、GPT 生成笔记、插入截图/链接、
    以及将任务信息写入状态文件与数据库等功能。
    """

    def __init__(self):
        from app.services.transcriber_config_manager import TranscriberConfigManager
        config_manager = TranscriberConfigManager()
        self.model_size: str = config_manager.get_whisper_model_size()
        self.device: Optional[str] = None
        self.transcriber_type: str = config_manager.get_transcriber_type()
        # 平台字幕优先：只有字幕不可用、确实需要音频转写时才初始化
        # Whisper。这样有字幕的任务不会加载本地模型，也不会误导日志。
        self.transcriber: Optional[Transcriber] = None
        self.video_path: Optional[Path] = None
        self.video_img_urls=[]
        logger.info("NoteGenerator 初始化完成")


    # ---------------- 公有方法 ----------------

    def generate(
        self,
        video_url: Union[str, HttpUrl],
        platform: str,
        quality: DownloadQuality = DownloadQuality.medium,
        task_id: Optional[str] = None,
        model_name: Optional[str] = None,
        provider_id: Optional[str] = None,
        link: bool = False,
        screenshot: bool = False,
        _format: Optional[List[str]] = None,
        style: Optional[str] = None,
        extras: Optional[str] = None,
        output_path: Optional[str] = None,
        video_understanding: bool = False,
        video_interval: int = 0,
        grid_size: Optional[List[int]] = None,
        summary_chunk_count: int = 1,
        summary_max_concurrency: int = 6,
        summary_retry_count: int = 2,
    ) -> NoteResult | None:
        """
        主流程：按步骤依次下载、转写、GPT 总结、截图/链接处理、存库、返回 NoteResult。

        :param video_url: 视频或音频链接
        :param platform: 平台名称，对应 SUPPORT_PLATFORM_MAP 中的键
        :param quality: 下载音频的质量枚举
        :param task_id: 用于标识本次任务的唯一 ID，亦用于状态文件和缓存文件命名
        :param model_name: GPT 模型名称
        :param provider_id: 模型供应商 ID
        :param link: 是否在笔记中插入视频片段链接
        :param screenshot: 是否在笔记中替换 Screenshot 标记为图片
        :param _format: 包含 'link' 或 'screenshot' 等字符串的列表，决定后续处理
        :param style: GPT 生成笔记的风格
        :param extras: 额外参数，传递给 GPT
        :param output_path: 下载输出目录（可选）
        :param video_understanding: 是否需要视频拼图理解（生成缩略图）
        :param video_interval: 视频帧截取间隔（秒），仅在 video_understanding 为 True 时生效
        :param grid_size: 生成缩略图时的网格大小，如 [3, 3]
        :return: NoteResult 对象，包含 markdown 文本、转写结果和音频元信息
        """
        if grid_size is None:
            grid_size = []

        try:
            logger.info(f"开始生成笔记 (task_id={task_id})")
            self._update_status(task_id, TaskStatus.PARSING, phase="fetching", message="正在获取视频信息")
            self._check_cancel(task_id, "fetching")

            # 获取下载器与 GPT 实例

            try:
                downloader = self._get_downloader(platform)
            except Exception as exc:
                raise TaskGenerationError(
                    TaskErrorCode.SOURCE_FETCH_FAILED,
                    "video_fetch",
                    "视频信息获取失败，请检查平台和原片链接。",
                    retryable=True,
                    upstream_summary=str(exc),
                ) from exc
            try:
                gpt = self._get_gpt(model_name, provider_id)
                if hasattr(gpt, "configure_summary"):
                    gpt.configure_summary(
                        chunk_count=summary_chunk_count,
                        max_concurrency=summary_max_concurrency,
                        retry_count=summary_retry_count,
                    )
            except Exception as exc:
                raise TaskGenerationError(
                    TaskErrorCode.SUMMARY_PREPARATION_FAILED,
                    "summarize_prepare",
                    "总结请求准备失败，请检查模型、供应商和 API Key 配置。",
                    retryable=False,
                    upstream_summary=str(exc),
                ) from exc

            # 缓存文件路径
            audio_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_audio.json"
            transcript_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_transcript.json"
            markdown_cache_file = NOTE_OUTPUT_DIR / f"{task_id}_markdown.md"
            # 1. 获取字幕/转写：优先缓存 → 平台字幕 → 音频转写
            transcript = None

            # 尝试读取缓存
            if transcript_cache_file.exists():
                logger.info(f"检测到转写缓存 ({transcript_cache_file})，尝试读取")
                try:
                    data = json.loads(transcript_cache_file.read_text(encoding="utf-8"))
                    segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
                    transcript = TranscriptResult(
                        language=data.get("language"),
                        full_text=data["full_text"],
                        segments=segments,
                    )
                    logger.info(f"已从缓存加载转写结果，共 {len(segments)} 段")
                except Exception as e:
                    logger.warning(f"加载转写缓存失败: {e}")

            # 缓存没有，尝试获取平台字幕
            if transcript is None:
                logger.info("尝试获取平台字幕（优先于音频下载）...")
                try:
                    transcript = downloader.download_subtitles(video_url)
                    if transcript and transcript.segments:
                        logger.info(f"成功获取平台字幕，共 {len(transcript.segments)} 段")
                        transcript_cache_file.write_text(
                            json.dumps(asdict(transcript), ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    else:
                        transcript = None
                        if getattr(downloader, "subtitle_failure_reason", None) == "auth_required":
                            logger.warning("B站登录态无效且客户端预取字幕不可用，将下载音频并自动使用 Whisper 转写")
                        else:
                            logger.info("平台无可用字幕，将下载音频后转写")
                except Exception as e:
                    logger.warning(f"获取平台字幕失败: {e}，将下载音频后转写")
                    transcript = None

            # 2. 下载音频/视频
            # 有字幕时只提取元信息，不下载音视频文件（除非需要截图/视频理解）
            has_transcript = transcript is not None
            need_full_download = not has_transcript or screenshot or video_understanding
            audio_meta = self._download_media(
                downloader=downloader,
                video_url=video_url,
                quality=quality,
                audio_cache_file=audio_cache_file,
                status_phase=TaskStatus.DOWNLOADING,
                platform=platform,
                output_path=output_path,
                screenshot=screenshot,
                video_understanding=video_understanding,
                video_interval=video_interval,
                grid_size=grid_size,
                skip_download=not need_full_download,
            )

            # 3. 如果前面没拿到字幕，走转写流程
            if transcript is None:
                self._check_cancel(task_id, "transcribing")
                transcript = self._get_transcript(
                    downloader=downloader,
                    video_url=video_url,
                    audio_file=audio_meta.file_path,
                    transcript_cache_file=transcript_cache_file,
                    status_phase=TaskStatus.TRANSCRIBING,
                    task_id=task_id,
                )
            else:
                self._update_status(
                    task_id,
                    TaskStatus.DOWNLOADING,
                    phase="transcribing",
                    message=f"已获取平台字幕，共 {len(transcript.segments)} 段，准备生成总结",
                )

            # 无论来自缓存、平台字幕还是音频转写，进入模型前都必须确认
            # 结果同时包含可用分段和非空全文，避免空缓存一路落到汇总阶段
            # 后才变成难以诊断的 UNKNOWN_ERROR。
            if (
                not transcript
                or not transcript.segments
                or not (getattr(transcript, "full_text", "") or "").strip()
            ):
                raise TaskGenerationError(
                    TaskErrorCode.TRANSCRIPTION_EMPTY,
                    "transcribing",
                    "转写结果为空或格式错误，无法生成总结。",
                    retryable=False,
                )

            # 3. GPT 总结
            self._check_cancel(task_id, "summarizing_chunks")
            markdown = self._summarize_text(
                task_id=task_id,
                audio_meta=audio_meta,
                transcript=transcript,
                gpt=gpt,
                markdown_cache_file=markdown_cache_file,
                link=link,
                screenshot=screenshot,
                formats=_format or [],
                style=style,
                extras=extras,
                video_img_urls=self.video_img_urls,
            )

            # 4. 截图 & 链接替换
            if _format:
                self._update_status(task_id, TaskStatus.FORMATTING, phase="rendering")
                markdown = self._post_process_markdown(
                    markdown=markdown,
                    video_path=self.video_path,
                    formats=_format,
                    audio_meta=audio_meta,
                    platform=platform,
                )

            markdown = prepend_source_link(markdown, str(video_url))

            # 5. 保存记录到数据库
            self._update_status(task_id, TaskStatus.SAVING, phase="rendering")
            try:
                self._save_metadata(video_id=audio_meta.video_id, platform=platform, task_id=task_id)
            except Exception as exc:
                raise TaskGenerationError(
                    TaskErrorCode.PERSISTENCE_FAILED,
                    "rendering",
                    "结果保存失败：笔记内容已生成，但保存记录时发生错误。",
                    retryable=True,
                    upstream_summary=str(exc),
                ) from exc

            # 6. 完成
            self._update_status(
                task_id,
                TaskStatus.SUCCESS,
                message=(
                    "生成完成，但 AI 汇总失败后已按分段顺序拼接"
                    if getattr(gpt, "last_merge_mode", "not_applicable") == "stitched_fallback"
                    else "生成完成"
                ),
                phase="completed",
                merge_mode=getattr(gpt, "last_merge_mode", "not_applicable"),
                merge_error=getattr(gpt, "last_merge_error", None),
            )
            logger.info(f"笔记生成成功 (task_id={task_id})")
            return NoteResult(
                markdown=markdown,
                transcript=transcript,
                audio_meta=audio_meta,
                duration_seconds=self._read_status_duration(task_id),
            )

        except Exception as exc:
            logger.error(f"生成笔记流程异常 (task_id={task_id})：{exc}", exc_info=True)
            error = self._classify_generation_error(exc)
            self._update_status(task_id, TaskStatus.FAILED, error=error)
            return None

    @staticmethod
    def _classify_generation_error(exc: Exception) -> TaskGenerationError:
        if isinstance(exc, TaskGenerationError):
            return exc
        return classify_pipeline_exception(exc, phase="fetching")

    def _check_cancel(self, task_id: Optional[str], phase: str) -> None:
        if _task_cancelled(task_id):
            raise TaskGenerationError(TaskErrorCode.CANCELLED, phase, "任务已由用户取消。", retryable=False)

    @staticmethod
    def delete_note(video_id: str, platform: str) -> int:
        """
        删除数据库中对应 video_id 与 platform 的任务记录

        :param video_id: 视频 ID
        :param platform: 平台标识
        :return: 删除的记录数
        """
        logger.info(f"删除笔记记录 (video_id={video_id}, platform={platform})")
        return delete_task_by_video(video_id, platform)

    # ---------------- 私有方法 ----------------

    def _init_transcriber(self) -> Transcriber:
        """
        根据环境变量 TRANSCRIBER_TYPE 动态获取并实例化转写器
        """
        if self.transcriber_type not in _transcribers:
            logger.error(f"未找到支持的转写器：{self.transcriber_type}")
            raise Exception(f"不支持的转写器：{self.transcriber_type}")

        logger.info(f"使用转写器：{self.transcriber_type}")
        return get_transcriber(transcriber_type=self.transcriber_type, model_size=self.model_size, device="cpu")

    def _get_or_init_transcriber(self) -> Transcriber:
        if self.transcriber is None:
            self.transcriber = self._init_transcriber()
        return self.transcriber

    def _get_gpt(self, model_name: Optional[str], provider_id: Optional[str]) -> GPT:
        """
        根据 provider_id 获取对应的 GPT 实例
        :param model_name: GPT 模型名称
        :param provider_id: 供应商 ID
        :return: GPT 实例
        """
        provider = ProviderService.get_provider_by_id(provider_id)
        if not provider:
            logger.error(f"[get_gpt] 未找到模型供应商: provider_id={provider_id}")
            raise ProviderError(code=ProviderErrorEnum.NOT_FOUND,message=ProviderErrorEnum.NOT_FOUND.message)
        logger.info(f"创建 GPT 实例 {provider_id}")
        config = ModelConfig(
            api_key=provider["api_key"],
            base_url=provider["base_url"],
            model_name=model_name,
            provider=provider["type"],
            name=provider["name"],
        )
        return GPTFactory().from_config(config)

    def _get_downloader(self, platform: str) -> Downloader:
        """
        根据平台名称获取对应的下载器实例

        :param platform: 平台标识，需在 SUPPORT_PLATFORM_MAP 中
        :return: 对应的 Downloader 子类实例
        """
        downloader_cls = SUPPORT_PLATFORM_MAP.get(platform)
        logger.debug(f"实例化下载器 -  {platform}")
        instance = None
        if not downloader_cls:
            logger.error(f"不支持的平台：{platform}")
            raise NoteError(code=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.code,
                            message=NoteErrorEnum.PLATFORM_NOT_SUPPORTED.message)
        try:
            instance = downloader_cls
        except Exception as e:
            logger.error(f"实例化下载器失败：{e}")


        logger.info(f"使用下载器：{downloader_cls.__class__}")
        return instance

    def _update_status(
        self,
        task_id: Optional[str],
        status: Union[str, TaskStatus],
        message: Optional[str] = None,
        *,
        phase: Optional[str] = None,
        error: Optional[TaskGenerationError] = None,
        **progress,
    ):
        """
        创建或更新 {task_id}.status.json，记录当前任务状态

        :param task_id: 任务唯一 ID
        :param status: TaskStatus 枚举或自定义状态字符串
        :param message: 可选消息，用于记录失败原因等
        """
        if not task_id:
            return

        NOTE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        status_file = NOTE_OUTPUT_DIR / f"{task_id}.status.json"
        normalized_status = status.value if isinstance(status, TaskStatus) else str(status)
        now = datetime.now(timezone.utc)

        previous = {}
        if status_file.exists():
            try:
                previous = json.loads(status_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}

        previous_status = previous.get("status")
        # A retry reuses the task ID. Start a fresh attempt when a terminal task
        # moves back to PENDING; otherwise preserve the original start time.
        if normalized_status == TaskStatus.PENDING.value and previous_status in _TERMINAL_STATUSES:
            started_at = now.isoformat()
        else:
            started_at = previous.get("started_at") or now.isoformat()

        phase_started_at = previous.get("phase_started_at")
        previous_phase = previous.get("phase")
        effective_phase = phase or (error.phase if error else None) or previous_phase
        if previous_status != normalized_status or previous_phase != effective_phase or not phase_started_at:
            phase_started_at = now.isoformat()

        started_dt = None
        try:
            started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            started_at = now.isoformat()
            started_dt = now

        elapsed_seconds = max(0.0, (now - started_dt).total_seconds())
        phase_map = {
            TaskStatus.PENDING.value: "fetching",
            TaskStatus.PARSING.value: "fetching",
            TaskStatus.DOWNLOADING.value: "fetching",
            TaskStatus.TRANSCRIBING.value: "transcribing",
            TaskStatus.SUMMARIZING.value: "summarizing_chunks",
            TaskStatus.FORMATTING.value: "rendering",
            TaskStatus.SAVING.value: "rendering",
            TaskStatus.SUCCESS.value: "completed",
            TaskStatus.FAILED.value: "failed",
        }
        data = {
            "status": normalized_status,
            "message": message or (
                _STATUS_MESSAGES.get(TaskStatus(normalized_status), normalized_status)
                if normalized_status in TaskStatus._value2member_map_
                else normalized_status
            ),
            "started_at": started_at,
            "phase_started_at": phase_started_at,
            "elapsed_seconds": round(elapsed_seconds, 1),
            "phase": phase or (error.phase if error else phase_map.get(normalized_status, "fetching")),
        }
        data.update({key: value for key, value in progress.items() if value is not None})
        if error:
            data.update(error.as_status())
            data["phase"] = error.phase

        if normalized_status in _TERMINAL_STATUSES:
            data["finished_at"] = now.isoformat()
            data["duration_seconds"] = round(elapsed_seconds, 1)

        try:
            # First create a temporary file
            temp_file = status_file.with_suffix('.tmp')

            # Write to temporary file
            with temp_file.open('w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # Atomic rename operation
            temp_file.replace(status_file)

            print(f"状态文件写入成功: {status_file}")
        except Exception as e:
            logger.exception(f"写入状态文件失败 (task_id={task_id})")

    @staticmethod
    def _read_status_duration(task_id: Optional[str]) -> Optional[float]:
        """读取 SUCCESS 状态中记录的总耗时，写入结果文件供历史笔记使用。"""
        if not task_id:
            return None
        try:
            status_file = NOTE_OUTPUT_DIR / f"{task_id}.status.json"
            data = json.loads(status_file.read_text(encoding="utf-8"))
            duration = data.get("duration_seconds")
            return float(duration) if duration is not None else None
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _handle_exception(self, task_id, exc):
        logger.error(f"任务异常 (task_id={task_id})", exc_info=True)
        error = self._classify_generation_error(exc)
        self._update_status(task_id, TaskStatus.FAILED, error=error)

    def _download_media(
        self,
        downloader: Downloader,
        video_url: Union[str, HttpUrl],
        quality: DownloadQuality,
        audio_cache_file: Path,
        status_phase: TaskStatus,
        platform: str,
        output_path: Optional[str],
        screenshot: bool,
        video_understanding: bool,
        video_interval: int,
        grid_size: List[int],
        skip_download: bool = False,
    ) -> AudioDownloadResult | None:
        """
        1. 检查音频缓存；若不存在，则根据需要下载音频或视频（若需截图/可视化）。
        2. 如果需要视频，则先下载视频并生成缩略图集，再下载音频。
        3. 返回 AudioDownloadResult

        :param downloader: Downloader 实例
        :param video_url: 视频/音频链接
        :param quality: 音频下载质量
        :param audio_cache_file: 本地缓存 JSON 文件路径
        :param status_phase: 对应的状态枚举，如 TaskStatus.DOWNLOADING
        :param platform: 平台标识
        :param output_path: 下载输出目录（可为 None）
        :param screenshot: 是否需要在笔记中插入截图
        :param video_understanding: 是否需要生成缩略图
        :param video_interval: 视频截帧间隔
        :param grid_size: 缩略图网格尺寸
        :return: AudioDownloadResult 对象
        """
        task_id = audio_cache_file.stem.split("_")[0]
        self._update_status(task_id, status_phase, phase="fetching")

        # 已有缓存，尝试加载
        if audio_cache_file.exists():
            logger.info(f"检测到音频缓存 ({audio_cache_file})，直接读取")
            try:
                data = json.loads(audio_cache_file.read_text(encoding="utf-8"))
                return AudioDownloadResult(**data)
            except Exception as e:
                logger.warning(f"读取音频缓存失败，将重新下载：{e}")

        # 有字幕且不需要截图/视频理解时，只提取元信息不下载文件
        if skip_download:
            logger.info("已有字幕，仅提取视频元信息（不下载音视频）")
            try:
                audio = downloader.download(
                    video_url=video_url,
                    quality=quality,
                    output_dir=output_path,
                    need_video=False,
                    skip_download=True,
                )
                audio_cache_file.write_text(
                    json.dumps(asdict(audio), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.info(f"元信息提取完成 ({audio_cache_file})")
                return audio
            except Exception as exc:
                logger.warning(f"元信息提取失败，将尝试完整下载: {exc}")

        # 判断是否需要下载视频
        need_video = screenshot or video_understanding
        if screenshot and not grid_size:
            grid_size = [2, 2]

        frame_interval = video_interval if video_interval and video_interval > 0 else 6
        if need_video:
            try:
                logger.info("开始下载视频")
                video_path_str = downloader.download_video(video_url)
                self.video_path = Path(video_path_str)
                logger.info(f"视频下载完成：{self.video_path}")

                if grid_size:
                    self.video_img_urls = VideoReader(
                        video_path=str(self.video_path),
                        grid_size=tuple(grid_size),
                        frame_interval=frame_interval,
                        unit_width=960,
                        unit_height=540,
                        save_quality=80,
                    ).run()
                else:
                    logger.info("未指定 grid_size，跳过缩略图生成")
            except Exception as exc:
                logger.error(f"视频下载失败：{exc}")
                raise TaskGenerationError(
                    TaskErrorCode.SOURCE_FETCH_FAILED,
                    "video_fetch",
                    "视频信息或视频素材获取失败，请检查原片链接和访问权限。",
                    retryable=True,
                    upstream_summary=str(exc),
                ) from exc

        # 下载音频
        try:
            logger.info("开始下载音频")
            audio = downloader.download(
                video_url=video_url,
                quality=quality,
                output_dir=output_path,
                need_video=need_video,
            )
            audio_cache_file.write_text(json.dumps(asdict(audio), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"音频下载并缓存成功 ({audio_cache_file})")
            return audio
        except Exception as exc:
            logger.error(f"音频下载失败：{exc}")
            raw_error = str(exc).lower()
            if "ssl" in raw_error or "unexpected_eof" in raw_error or "eof occurred" in raw_error:
                user_message = "音频下载失败：B 站 TLS 连接在返回数据前被关闭，请检查网络或代理后重试。"
            else:
                user_message = "音频下载失败，请检查原片链接、Cookie 或下载器配置。"
            raise TaskGenerationError(
                TaskErrorCode.AUDIO_DOWNLOAD_FAILED,
                "audio_download",
                user_message,
                retryable=True,
                upstream_summary=str(exc),
            ) from exc


    def _get_transcript(
        self,
        downloader: Downloader,
        video_url: str,
        audio_file: str,
        transcript_cache_file: Path,
        status_phase: TaskStatus,
        task_id: Optional[str] = None,
    ) -> TranscriptResult | None:
        """
        优先获取平台字幕，没有则 fallback 到音频转写

        :param downloader: 下载器实例
        :param video_url: 视频链接
        :param audio_file: 音频文件路径（用于 fallback 转写）
        :param transcript_cache_file: 缓存文件路径
        :param status_phase: 状态枚举
        :param task_id: 任务 ID
        :return: TranscriptResult 对象
        """
        self._update_status(task_id, status_phase, phase="transcribing")

        # 已有缓存，直接返回
        if transcript_cache_file.exists():
            logger.info(f"检测到转写缓存 ({transcript_cache_file})，尝试读取")
            try:
                data = json.loads(transcript_cache_file.read_text(encoding="utf-8"))
                segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
                return TranscriptResult(language=data.get("language"), full_text=data["full_text"], segments=segments)
            except Exception as e:
                logger.warning(f"加载转写缓存失败，将重新获取：{e}")

        # 1. 先尝试获取平台字幕
        logger.info("尝试获取平台字幕...")
        fallback_message = None
        try:
            transcript = downloader.download_subtitles(video_url)
            if transcript and transcript.segments:
                logger.info(f"成功获取平台字幕，共 {len(transcript.segments)} 段")
                # 缓存结果
                transcript_cache_file.write_text(
                    json.dumps(asdict(transcript), ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                return transcript
            else:
                if getattr(downloader, "subtitle_failure_reason", None) == "auth_required":
                    fallback_message = "B站登录已失效，正在自动使用 Whisper 转写"
                    logger.warning(fallback_message)
                else:
                    fallback_message = "平台无可用字幕，正在使用音频转写"
                    logger.info(fallback_message)
        except Exception as e:
            fallback_message = "字幕获取失败，正在尝试音频转写"
            logger.warning(f"获取平台字幕失败: {e}，将使用音频转写")
            self._update_status(
                task_id,
                status_phase,
                phase="transcribing",
                message=fallback_message,
                error=TaskGenerationError(
                    TaskErrorCode.SUBTITLE_FETCH_FAILED,
                    "subtitle_fetch",
                    fallback_message + "。",
                    retryable=True,
                    upstream_summary=str(e),
                ),
            )

        # 2. Fallback 到音频转写
        return self._transcribe_audio(
            audio_file=audio_file,
            transcript_cache_file=transcript_cache_file,
            status_phase=status_phase,
            status_message=fallback_message,
        )

    def _transcribe_audio(
        self,
        audio_file: str,
        transcript_cache_file: Path,
        status_phase: TaskStatus,
        status_message: Optional[str] = None,
    ) -> TranscriptResult | None:
        """
        1. 检查转写缓存；若存在则尝试加载，否则调用转写器生成并缓存。
        2. 返回 TranscriptResult 对象

        :param audio_file: 音频文件本地路径
        :param transcript_cache_file: 转写结果缓存路径
        :param status_phase: 对应的状态枚举，如 TaskStatus.TRANSCRIBING
        :return: TranscriptResult 对象
        """
        task_id = transcript_cache_file.stem.split("_")[0]
        self._update_status(task_id, status_phase, phase="transcribing", message=status_message)

        # 已有缓存，尝试加载
        if transcript_cache_file.exists():
            logger.info(f"检测到转写缓存 ({transcript_cache_file})，尝试读取")
            try:
                data = json.loads(transcript_cache_file.read_text(encoding="utf-8"))
                segments = [TranscriptSegment(**seg) for seg in data.get("segments", [])]
                return TranscriptResult(language=data["language"], full_text=data["full_text"], segments=segments)
            except Exception as e:
                logger.warning(f"加载转写缓存失败，将重新转写：{e}")

        # 调用转写器
        try:
            logger.info("开始转写音频")
            # 只有平台字幕和有效缓存都不可用时才走到这里。model_size
            # 继续由 _init_transcriber 显式传递，避免回退到 tiny 默认值。
            transcript = self._get_or_init_transcriber().transcript(file_path=audio_file)
            if not transcript or not transcript.segments or not (transcript.full_text or "").strip():
                raise TaskGenerationError(
                    TaskErrorCode.TRANSCRIPTION_EMPTY,
                    "transcribing",
                    "转写结果为空或格式错误，无法生成总结。",
                    retryable=False,
                )
            transcript_cache_file.write_text(json.dumps(asdict(transcript), ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"转写并缓存成功 ({transcript_cache_file})")
            return transcript
        except Exception as exc:
            logger.error(f"音频转写失败：{exc}")
            if isinstance(exc, TaskGenerationError):
                raise
            raise TaskGenerationError(
                TaskErrorCode.TRANSCRIPTION_FAILED,
                "transcribing",
                "音频转写失败，请检查转写模型或音频文件。",
                retryable=True,
                upstream_summary=str(exc),
            ) from exc

    def _summarize_text(
        self,
        task_id: Optional[str],
        audio_meta: AudioDownloadResult,
        transcript: TranscriptResult,
        gpt: GPT,
        markdown_cache_file: Path,
        link: bool,
        screenshot: bool,
        formats: List[str],
        style: Optional[str],
        extras: Optional[str],
            video_img_urls: List[str],
    ) -> str | None:
        """
        调用 GPT 对转写结果进行总结，生成 Markdown 文本并缓存。

        :param audio_meta: AudioDownloadResult 元信息
        :param transcript: TranscriptResult 转写结果
        :param gpt: GPT 实例
        :param markdown_cache_file: Markdown 缓存路径
        :param link: 是否在笔记中插入链接
        :param screenshot: 是否在笔记中生成截图占位
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param style: GPT 输出风格
        :param extras: GPT 额外参数
        :return: 生成的 Markdown 字符串
        """
        self._update_status(task_id, TaskStatus.SUMMARIZING, phase="summarizing_chunks")

        source = GPTSource(
            title=audio_meta.title,
            segment=transcript.segments,
            tags=audio_meta.raw_info.get("tags", []),
            screenshot=screenshot,
            video_img_urls=video_img_urls,
            link=link,
            _format=formats,
            style=style,
            extras=extras,
            checkpoint_key=task_id,
            progress_callback=lambda **info: self._update_status(
                task_id,
                TaskStatus.SUMMARIZING,
                phase=info.get("phase", "summarizing_chunks"),
                message=(
                    f"AI 分段总结中：已完成 {info.get('completed', 0)}/{info.get('total', 0)}"
                    if info.get("phase") == "summarizing_chunks"
                    else info.get("merge_warning") or "正在汇总分段结果"
                ),
                completed_chunks=info.get("completed"),
                chunk_total=info.get("total"),
                merge_mode=info.get("merge_mode"),
                merge_error_code=info.get("merge_error_code"),
                merge_attempt=info.get("merge_attempt"),
                merge_upstream_summary=info.get("merge_upstream_summary"),
            ),
            cancel_check=lambda: _task_cancelled(task_id),
        )

        try:
            markdown = gpt.summarize(source)
            markdown_cache_file.write_text(markdown, encoding="utf-8")
            logger.info(f"GPT 总结并缓存成功 ({markdown_cache_file})")
            return markdown
        except Exception as exc:
            logger.error(f"GPT 总结失败：{exc}")
            error = self._classify_generation_error(exc)
            self._handle_exception(task_id, error)
            raise error from exc

    def _post_process_markdown(
        self,
        markdown: str,
        video_path: Optional[Path],
        formats: List[str],
        audio_meta: AudioDownloadResult,
        platform: str,
    ) -> str:
        """
        对生成的 Markdown 做后期处理：插入截图和/或插入链接。

        :param markdown: 原始 Markdown 字符串
        :param video_path: 本地视频路径（可为 None）
        :param formats: 包含 'link' 或 'screenshot' 的列表
        :param audio_meta: AudioDownloadResult 元信息，用于链接替换
        :param platform: 平台标识，用于链接替换
        :return: 处理后的 Markdown 字符串
        """
        if "screenshot" in formats and video_path:
            try:
                markdown = self._insert_screenshots(markdown, video_path)
            except Exception as exc:
                logger.warning("截图插入失败，跳过该步骤")

        if "link" in formats:
            try:
                markdown = replace_content_markers(markdown, video_id=audio_meta.video_id, platform=platform)
            except Exception as e:
                logger.warning(f"链接插入失败，跳过该步骤：{e}")

        return markdown

    def _insert_screenshots(self, markdown: str, video_path: Path) -> str | None | Any:
        """
        扫描 Markdown 文本中所有 Screenshot 标记，并替换为实际生成的截图链接。

        :param markdown: 含有 *Screenshot-mm:ss 或 Screenshot-[mm:ss] 标记的 Markdown 文本
        :param video_path: 本地视频文件路径
        :return: 替换后的 Markdown 字符串
        """
        matches: List[Tuple[str, int]] = extract_screenshot_timestamps(markdown)
        for idx, (marker, ts) in enumerate(matches):
            try:
                img_path = generate_screenshot(str(video_path), str(IMAGE_OUTPUT_DIR), ts, idx)
                filename = Path(img_path).name
                # 构建前端可访问的 URL，例如 /static/screenshots/{filename}
                img_url = f"{IMAGE_BASE_URL.rstrip('/')}/{filename}"
                markdown = markdown.replace(marker, f"![]({img_url})", 1)
            except Exception as exc:
                logger.error(f"生成截图失败 (timestamp={ts})：{exc}")
                # self._handle_exception(task_id, exc)
                return None
        return markdown

    @staticmethod
    def _extract_screenshot_timestamps(markdown: str) -> List[Tuple[str, int]]:
        """
        从 Markdown 文本中提取所有 '*Screenshot-mm:ss' 或 'Screenshot-[mm:ss]' 标记，
        返回 [(原始标记文本, 时间戳秒数), ...] 列表。

        :param markdown: 原始 Markdown 文本
        :return: 标记与对应时间戳秒数的列表
        """
        return extract_screenshot_timestamps(markdown)

    def _save_metadata(self, video_id: str, platform: str, task_id: str) -> None:
        """
        将生成的笔记任务记录插入数据库

        :param video_id: 视频 ID
        :param platform: 平台标识
        :param task_id: 任务 ID
        """
        try:
            insert_video_task(video_id=video_id, platform=platform, task_id=task_id)
            logger.info(f"已保存任务记录到数据库 (video_id={video_id}, platform={platform}, task_id={task_id})")
        except Exception as e:
            logger.error(f"保存任务记录失败：{e}")
            raise
