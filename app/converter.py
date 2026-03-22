"""ffmpeg-based video converter with async progress reporting."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches lines like: time=00:01:23.45
_TIME_RE = re.compile(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})")
_V4L2_INIT_ERROR_RE = re.compile(
    r"(h264_v4l2m2m.*(could not find a valid device|can't configure encoder))"
    r"|(encoder requires yuv420p pixel format)"
    r"|(error while opening encoder for output stream)",
    re.IGNORECASE,
)
_V4L2_STREAMON_ERROR_RE = re.compile(
    r"(vidioc_streamon failed on output context)|(error submitting video frame to the encoder)",
    re.IGNORECASE,
)
_RESOLUTION_LADDER = [4320, 2160, 1440, 1080, 720, 480]

# Callable that receives a progress float in [0, 1].
type ProgressCallback = Callable[[float], Awaitable[None]]
type LogCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ConversionResult:
    success: bool
    output_path: Path | None
    error: str | None


@dataclass(frozen=True, slots=True)
class DestinationCodecProfile:
    codec: str
    video_encoder: str
    extension: str
    audio_encoder: str


_CODEC_PROFILES: dict[str, DestinationCodecProfile] = {
    "h264": DestinationCodecProfile(
        codec="h264", video_encoder="libx264", extension="mp4", audio_encoder="aac"
    ),
    "hevc": DestinationCodecProfile(
        codec="hevc", video_encoder="libx265", extension="mp4", audio_encoder="aac"
    ),
    "av1": DestinationCodecProfile(
        codec="av1", video_encoder="libaom-av1", extension="mp4", audio_encoder="aac"
    ),
    "vp9": DestinationCodecProfile(
        codec="vp9",
        video_encoder="libvpx-vp9",
        extension="webm",
        audio_encoder="libopus",
    ),
    "vp8": DestinationCodecProfile(
        codec="vp8", video_encoder="libvpx", extension="webm", audio_encoder="libopus"
    ),
    "mpeg4": DestinationCodecProfile(
        codec="mpeg4", video_encoder="mpeg4", extension="mp4", audio_encoder="aac"
    ),
}

_V4L2_DEVICE_PATHS = [Path("/dev/video10"), Path("/dev/video11"), Path("/dev/video12")]


def _output_path(input_path: Path, profile: DestinationCodecProfile) -> Path:
    """Return ``<stem>.<codec>.<ext>`` in the same directory as the source."""
    return input_path.parent / f"{input_path.stem}.{profile.codec}.{profile.extension}"


def _time_to_seconds(h: str, m: str, s: str, cs: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(cs) / 100


def _v4l2_device_status() -> tuple[bool, str]:
    """Return (ready, details) for required V4L2 encoder device nodes."""
    missing = [str(path) for path in _V4L2_DEVICE_PATHS if not path.exists()]
    if missing:
        return False, f"Missing V4L2 devices: {', '.join(missing)}"

    inaccessible: list[str] = []
    for path in _V4L2_DEVICE_PATHS:
        if not os.access(path, os.R_OK | os.W_OK):
            inaccessible.append(str(path))

    if inaccessible:
        return False, (
            "V4L2 devices exist but are not read/write accessible: "
            + ", ".join(inaccessible)
        )

    return True, "V4L2 encoder devices are present and accessible"


def is_v4l2_h264_available() -> bool:
    """Return True when V4L2 H.264 devices are available in the container."""
    ready, _ = _v4l2_device_status()
    return ready


def _resolution_retry_steps(
    source_height: int | None,
    min_target_resolution: int,
) -> list[int]:
    if source_height is None or source_height <= 0:
        return []
    return [
        h
        for h in _RESOLUTION_LADDER
        if h < source_height and h >= min_target_resolution
    ]


async def convert_file(
    input_path: Path,
    crf: int,
    destination_codec: str,
    ffmpeg_bin: str,
    on_progress: ProgressCallback,
    cancel_event: asyncio.Event,
    duration_seconds: float | None = None,
    on_log: LogCallback | None = None,
    lower_target_resolution_on_v4l2_fail: bool = True,
    min_target_resolution: int = 480,
    source_height: int | None = None,
) -> ConversionResult:
    """Convert *input_path* to the configured codec profile with the given CRF.

    Progress is reported via *on_progress* (value between 0 and 1).
    Setting *cancel_event* before or during conversion will terminate ffmpeg
    and clean up the partial output file.

    On RPi4b, hardware-accelerated encoding is not used (no stable V4L2
    H.264 encoder in Docker without device passthrough).  The ``slow``
    preset is a good balance of quality and CPU usage; change to ``medium``
    or ``fast`` in :data:`app.models.CRF_MAP` for quicker encodes at the
    cost of file size.
    """
    profile = _CODEC_PROFILES.get(destination_codec.strip().lower())
    if profile is None:
        supported = ", ".join(sorted(_CODEC_PROFILES))
        return ConversionResult(
            success=False,
            output_path=None,
            error=f"Unsupported destination codec '{destination_codec}'. Supported: {supported}",
        )

    out = _output_path(input_path, profile)
    out.unlink(missing_ok=True)

    v4l2_ready, v4l2_detail = _v4l2_device_status()
    use_v4l2_h264 = profile.codec == "h264" and v4l2_ready

    video_args: list[str]
    if use_v4l2_h264:
        # V4L2 M2M uses bitrate-based control rather than CRF.
        video_args = [
            "-c:v",
            "h264_v4l2m2m",
            "-pix_fmt",
            "yuv420p",
            "-b:v",
            "4M",
        ]
        logger.info("Using V4L2 H.264 hardware encoder (h264_v4l2m2m)")
        if on_log is not None:
            await on_log(
                "Using V4L2 H.264 encoder (h264_v4l2m2m, pix_fmt=yuv420p): "
                + v4l2_detail
            )
    else:
        video_args = [
            "-c:v",
            profile.video_encoder,
            "-crf",
            str(crf),
            "-preset",
            "slow",
        ]
        if profile.codec == "h264" and on_log is not None:
            await on_log(f"Using software H.264 encoder (libx264). {v4l2_detail}")

    async def _run_once(current_video_args: list[str]) -> ConversionResult:
        cmd: list[str] = [
            ffmpeg_bin,
            "-i",
            str(input_path),
            *current_video_args,
            "-c:a",
            profile.audio_encoder,
        ]

        if profile.audio_encoder == "aac":
            cmd.extend(["-b:a", "128k"])

        cmd.extend(
            [
                "-movflags",
                "+faststart",
                "-y",
                str(out),
            ]
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return ConversionResult(success=False, output_path=None, error=str(exc))

        # Track last reported progress to avoid spamming DB writes.
        last_reported: float = -1.0
        error_tail: list[str] = []

        async def _read_stderr() -> None:
            nonlocal last_reported
            assert proc.stderr is not None
            async for raw_line in proc.stderr:
                line = raw_line.decode("utf-8", errors="replace").strip()
                error_tail.append(line)
                if len(error_tail) > 40:
                    error_tail.pop(0)

                if on_log is not None:
                    await on_log(line)

                if duration_seconds and duration_seconds > 0:
                    if m := _TIME_RE.search(line):
                        current = _time_to_seconds(*m.groups())
                        progress = min(current / duration_seconds, 1.0)
                        # Report every 2 % to reduce DB writes.
                        if progress - last_reported >= 0.02:
                            last_reported = progress
                            await on_progress(progress)

        async def _watch_cancel() -> None:
            await cancel_event.wait()
            proc.terminate()

        stderr_task = asyncio.create_task(_read_stderr())
        cancel_task = asyncio.create_task(_watch_cancel())

        await proc.wait()
        cancel_task.cancel()
        await stderr_task

        if cancel_event.is_set():
            out.unlink(missing_ok=True)
            return ConversionResult(success=False, output_path=None, error="Cancelled")

        if proc.returncode != 0:
            out.unlink(missing_ok=True)
            error_text = "\n".join(error_tail)
            return ConversionResult(success=False, output_path=None, error=error_text)

        await on_progress(1.0)
        return ConversionResult(success=True, output_path=out, error=None)

    first_result = await _run_once(video_args)
    if first_result.success or not use_v4l2_h264:
        return first_result

    if cancel_event.is_set():
        return first_result

    error_text = first_result.error or ""

    if (
        lower_target_resolution_on_v4l2_fail
        and _V4L2_STREAMON_ERROR_RE.search(error_text)
        and source_height is not None
    ):
        steps = _resolution_retry_steps(source_height, min_target_resolution)
        for target_height in steps:
            if on_log is not None:
                await on_log(
                    "V4L2 stream start failed; retrying with downscaled target "
                    f"height {target_height}p"
                )
            scaled_args = [
                "-vf",
                f"scale=-2:{target_height}",
                "-c:v",
                "h264_v4l2m2m",
                "-pix_fmt",
                "yuv420p",
                "-b:v",
                "4M",
            ]
            retry = await _run_once(scaled_args)
            if retry.success or cancel_event.is_set():
                return retry
            error_text = retry.error or error_text

    if not _V4L2_INIT_ERROR_RE.search(error_text):
        return ConversionResult(success=False, output_path=None, error=error_text)

    if on_log is not None:
        await on_log(
            "V4L2 encoder failed to initialize at runtime; "
            "falling back to software libx264. "
            "Check device passthrough (/dev/video10-12), permissions, and container runtime."
        )

    software_args = [
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        "slow",
    ]
    return await _run_once(software_args)
