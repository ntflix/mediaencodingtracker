"""SQLAlchemy ORM models and domain enums."""

from datetime import datetime, UTC
from enum import StrEnum

from sqlalchemy import Boolean, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Domain enums
# ---------------------------------------------------------------------------


class VideoCodec(StrEnum):
    H264 = "h264"
    HEVC = "hevc"  # H.265
    VP9 = "vp9"
    VP8 = "vp8"
    AV1 = "av1"
    MPEG4 = "mpeg4"  # XviD / DivX
    MPEG2 = "mpeg2video"
    VC1 = "vc1"  # WMV / VC-1
    UNKNOWN = "unknown"


class ConversionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Quality(StrEnum):
    LOW = "low"  # CRF 28 – smallest file
    MEDIUM = "medium"  # CRF 23 – ffmpeg default
    HIGH = "high"  # CRF 18 – high quality
    VERY_HIGH = "very_high"  # CRF 15 – near-lossless


# CRF values for libx264 corresponding to Quality levels.
CRF_MAP: dict[Quality, int] = {
    Quality.LOW: 28,
    Quality.MEDIUM: 23,
    Quality.HIGH: 18,
    Quality.VERY_HIGH: 15,
}


# ---------------------------------------------------------------------------
# ORM base & tables
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    pass


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Path relative to media_root so the DB stays portable.
    path: Mapped[str] = mapped_column(String, unique=True, index=True)
    filename: Mapped[str] = mapped_column(String, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    video_codec: Mapped[str] = mapped_column(String, index=True)
    audio_codec: Mapped[str | None] = mapped_column(String, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    container_format: Mapped[str | None] = mapped_column(String, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    last_seen_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    # True when the file has been removed from disk since last scan.
    is_missing: Mapped[bool] = mapped_column(Boolean, default=False)

    jobs: Mapped[list["ConversionJob"]] = relationship(
        back_populates="media_file",
        cascade="all, delete-orphan",
    )


class ConversionJob(Base):
    __tablename__ = "conversion_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    media_file_id: Mapped[int] = mapped_column(ForeignKey("media_files.id"))
    status: Mapped[str] = mapped_column(String, default=ConversionStatus.PENDING)
    quality: Mapped[str] = mapped_column(String, default=Quality.MEDIUM)
    delete_original: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    output_path: Mapped[str | None] = mapped_column(String, nullable=True)
    # 0.0–1.0 progress reported by ffmpeg stderr parsing.
    progress: Mapped[float] = mapped_column(Float, default=0.0)

    media_file: Mapped[MediaFile] = relationship(back_populates="jobs")


class UserSettings(Base):
    """Singleton row (always id = 1) for user-configurable preferences."""

    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    scan_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    scan_cron: Mapped[str] = mapped_column(String, default="0 */6 * * *")
    auto_convert: Mapped[bool] = mapped_column(Boolean, default=False)
    convert_cron: Mapped[str] = mapped_column(String, default="0 2 * * *")
    default_quality: Mapped[str] = mapped_column(String, default=Quality.MEDIUM)
    delete_original: Mapped[bool] = mapped_column(Boolean, default=False)
    # JSON list of VideoCodec values to auto-convert.
    target_codecs: Mapped[list[str]] = mapped_column(
        JSON,
        default=lambda: [
            VideoCodec.HEVC,
            VideoCodec.VP9,
            VideoCodec.AV1,
            VideoCodec.MPEG4,
        ],
    )
