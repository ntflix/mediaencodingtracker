"""ffprobe-based media file scanner."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.models import VideoCodec

# Callable: on_progress(probed_so_far, total) → awaitable
type ScanProgressCallback = Callable[[int, int], Awaitable[None]]

logger = logging.getLogger(__name__)

# Extensions considered as potential video containers.
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".m4v",
        ".webm",
        ".ts",
        ".m2ts",
        ".mpg",
        ".mpeg",
        ".3gp",
        ".ogv",
        ".divx",
        ".vob",
        ".asf",
    }
)

# Probe up to this many files concurrently (keeps I/O sane on slow SMB shares).
_PROBE_CONCURRENCY = 4


@dataclass(frozen=True, slots=True)
class MediaInfo:
    path: Path
    size_bytes: int
    video_codec: str
    audio_codec: str | None
    width: int | None
    height: int | None
    duration_seconds: float | None
    container_format: str | None


def _parse_codec(raw: str) -> str:
    """Map ffprobe codec_name to a VideoCodec enum value."""
    match raw.lower():
        case "h264" | "avc" | "avc1":
            return VideoCodec.H264
        case "hevc" | "h265":
            return VideoCodec.HEVC
        case "vp9":
            return VideoCodec.VP9
        case "vp8":
            return VideoCodec.VP8
        case "av1" | "libaom-av1":
            return VideoCodec.AV1
        case "mpeg4" | "xvid" | "divx" | "dx50" | "mp4v":
            return VideoCodec.MPEG4
        case "mpeg2video" | "mpeg2":
            return VideoCodec.MPEG2
        case "vc1" | "wmv3" | "wmv2" | "wmv1":
            return VideoCodec.VC1
        case _:
            return VideoCodec.UNKNOWN


async def probe_file(path: Path) -> MediaInfo | None:
    """Run ffprobe on *path* and return a :class:`MediaInfo`, or ``None`` on failure."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (asyncio.TimeoutError, OSError) as exc:
        logger.warning("ffprobe failed for %s: %s", path, exc)
        return None

    if proc.returncode != 0:
        return None

    try:
        data: dict[str, object] = json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning("ffprobe returned invalid JSON for %s", path)
        return None

    streams: list[dict[str, object]] = data.get("streams", [])  # type: ignore[assignment]
    fmt: dict[str, object] = data.get("format", {})  # type: ignore[assignment]

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video is None:
        return None  # Not a video file

    duration: float | None = None
    if raw_dur := fmt.get("duration"):
        try:
            duration = float(str(raw_dur))
        except (ValueError, TypeError):
            pass

    return MediaInfo(
        path=path,
        size_bytes=path.stat().st_size,
        video_codec=_parse_codec(str(video.get("codec_name", "unknown"))),
        audio_codec=(
            str(audio["codec_name"]) if audio and "codec_name" in audio else None
        ),
        width=int(str(video["width"])) if "width" in video else None,
        height=int(str(video["height"])) if "height" in video else None,
        duration_seconds=duration,
        container_format=(
            str(fmt.get("format_name")) if fmt.get("format_name") else None
        ),
    )


async def scan_directory(
    root: Path,
    on_progress: ScanProgressCallback | None = None,
) -> list[MediaInfo]:
    """Recursively scan *root* for video files and probe each one.

    Files are probed with limited concurrency so we don't hammer a slow SMB
    share or consume all CPU on a Raspberry Pi.
    """
    if not root.exists():
        logger.error("Media root %s does not exist", root)
        return []

    paths = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    logger.info("Probing %d candidate video files under %s", len(paths), root)

    semaphore = asyncio.Semaphore(_PROBE_CONCURRENCY)
    probed_count = 0

    async def probe_bounded(path: Path) -> MediaInfo | None:
        nonlocal probed_count
        async with semaphore:
            result = await probe_file(path)
            probed_count += 1
            if on_progress is not None:
                await on_progress(probed_count, len(paths))
            return result

    results = await asyncio.gather(*[probe_bounded(p) for p in paths])
    return [r for r in results if r is not None]
