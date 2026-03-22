"""Pydantic schemas for API request/response bodies."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models import Quality

# ---------------------------------------------------------------------------
# Media files
# ---------------------------------------------------------------------------


class MediaFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str
    filename: str
    size_bytes: int
    duration_seconds: float | None
    video_codec: str
    audio_codec: str | None
    width: int | None
    height: int | None
    container_format: str | None
    discovered_at: datetime
    last_seen_at: datetime
    is_missing: bool


class MediaFilePage(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[MediaFileOut]


# ---------------------------------------------------------------------------
# Conversion jobs
# ---------------------------------------------------------------------------


class JobCreateRequest(BaseModel):
    media_file_ids: list[int]
    quality: Quality = Quality.MEDIUM
    delete_original: bool = False


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_file_id: int
    status: str
    quality: str
    delete_original: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    output_path: str | None
    progress: float
    media_file: MediaFileOut | None = None


class JobPage(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[JobOut]


class JobLogOut(BaseModel):
    job_id: int
    log: str
    truncated: bool


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class StatsOut(BaseModel):
    total_files: int
    total_size_bytes: int
    missing_files: int
    by_codec_count: dict[str, int]  # codec → file count
    by_codec_bytes: dict[str, int]  # codec → total bytes
    jobs_pending: int
    jobs_running: int
    jobs_completed: int
    jobs_failed: int


# ---------------------------------------------------------------------------
# User settings
# ---------------------------------------------------------------------------


class UserSettingsOut(BaseModel):
    auto_scan_enabled: bool
    scan_schedule: str
    auto_convert_enabled: bool
    convert_schedule: str
    default_quality: str
    delete_original_after_convert: bool
    destination_codec: str
    source_codecs: list[str]
    ffmpeg_bin: str


class SetupCheckItem(BaseModel):
    key: str
    configured: bool
    details: str


class SetupCheckOut(BaseModel):
    ready: bool
    checks: list[SetupCheckItem]


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


class ScanResult(BaseModel):
    new_files: int
    updated_files: int
    missing_files: int
    total_scanned: int


class MessageOut(BaseModel):
    message: str
