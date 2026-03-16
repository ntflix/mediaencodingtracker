"""Tests for the media file scanner (ffprobe parsing)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import VideoCodec
from app.scanner import MediaInfo, _parse_codec, probe_file, scan_directory


# ---------------------------------------------------------------------------
# _parse_codec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("h264", VideoCodec.H264),
        ("avc", VideoCodec.H264),
        ("hevc", VideoCodec.HEVC),
        ("h265", VideoCodec.HEVC),
        ("vp9", VideoCodec.VP9),
        ("vp8", VideoCodec.VP8),
        ("av1", VideoCodec.AV1),
        ("mpeg4", VideoCodec.MPEG4),
        ("xvid", VideoCodec.MPEG4),
        ("mpeg2video", VideoCodec.MPEG2),
        ("vc1", VideoCodec.VC1),
        ("wmv3", VideoCodec.VC1),
        ("totally_fake", VideoCodec.UNKNOWN),
    ],
)
def test_parse_codec(raw: str, expected: VideoCodec) -> None:
    assert _parse_codec(raw) == expected


# ---------------------------------------------------------------------------
# probe_file — mocked ffprobe
# ---------------------------------------------------------------------------


def _make_ffprobe_output(
    *,
    codec: str = "hevc",
    width: int = 1920,
    height: int = 1080,
    duration: str = "5400.0",
    audio_codec: str = "aac",
) -> bytes:
    data = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": codec,
                "width": width,
                "height": height,
            },
            {"codec_type": "audio", "codec_name": audio_codec},
        ],
        "format": {"format_name": "matroska,webm", "duration": duration},
    }
    return json.dumps(data).encode()


@pytest.mark.asyncio
async def test_probe_file_returns_media_info(tmp_path: Path) -> None:
    fake_file = tmp_path / "movie.mkv"
    fake_file.write_bytes(b"\x00" * 100)

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(_make_ffprobe_output(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        info = await probe_file(fake_file)

    assert info is not None
    assert info.video_codec == VideoCodec.HEVC
    assert info.width == 1920
    assert info.height == 1080
    assert info.duration_seconds == pytest.approx(5400.0)
    assert info.audio_codec == "aac"
    assert info.path == fake_file


@pytest.mark.asyncio
async def test_probe_file_no_video_stream_returns_none(tmp_path: Path) -> None:
    fake_file = tmp_path / "audio_only.mp4"
    fake_file.write_bytes(b"\x00" * 100)

    # ffprobe output with no video stream
    data = {"streams": [{"codec_type": "audio", "codec_name": "aac"}], "format": {}}
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(json.dumps(data).encode(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        info = await probe_file(fake_file)

    assert info is None


@pytest.mark.asyncio
async def test_probe_file_nonzero_exit_returns_none(tmp_path: Path) -> None:
    fake_file = tmp_path / "corrupt.mkv"
    fake_file.write_bytes(b"\x00")

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        info = await probe_file(fake_file)

    assert info is None


# ---------------------------------------------------------------------------
# scan_directory — mock filesystem
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_directory_finds_video_files(tmp_path: Path) -> None:
    (tmp_path / "movie.mkv").write_bytes(b"\x00" * 50)
    (tmp_path / "movie.nfo").write_bytes(b"nfo")  # should be skipped
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "another.mp4").write_bytes(b"\x00" * 50)

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(_make_ffprobe_output(), b""))

    with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        results = await scan_directory(tmp_path)

    assert len(results) == 2
    assert all(isinstance(r, MediaInfo) for r in results)


@pytest.mark.asyncio
async def test_scan_directory_nonexistent_returns_empty(tmp_path: Path) -> None:
    results = await scan_directory(tmp_path / "does_not_exist")
    assert results == []
