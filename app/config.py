from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, NoDecode

from app.models import Quality, VideoCodec


class Config(BaseSettings):
    """Application configuration loaded from environment variables.

    All variables can be set in a ``.env`` file or passed directly as
    environment variables in docker-compose / the shell.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Paths
    media_root: Path = Field(
        default=Path("/media"),
        description="Root directory of the media share (bind-mounted SMB share).",
    )
    db_path: Path = Field(
        default=Path("/data/tracker.db"),
        description="Path to the SQLite database file.",
    )

    # Authentication
    admin_user: str = Field(default="admin", description="Web UI admin username.")
    admin_pass: str = Field(default="changeme", description="Web UI admin password.")

    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="info")

    # Scan / auto-convert behavior (compose-driven)
    auto_scan_enabled: bool = Field(default=True)
    scan_schedule: str = Field(default="0 */6 * * *")
    auto_convert_enabled: bool = Field(default=False)
    convert_schedule: str = Field(default="0 2 * * *")
    default_quality: Quality = Field(default=Quality.MEDIUM)
    delete_original_after_convert: bool = Field(default=False)
    destination_codec: str = Field(default=VideoCodec.H264)
    source_codecs: Annotated[list[str], NoDecode] = Field(
        default=[
            VideoCodec.HEVC,
            VideoCodec.VP9,
            VideoCodec.AV1,
            VideoCodec.MPEG4,
        ]
    )
    ffmpeg_bin: str = Field(
        default="ffmpeg",
        description="Path to ffmpeg executable inside the container.",
    )

    # Optional: path to docker-compose used for setup validation checks.
    compose_file_path: Path = Field(default=Path("/app/docker-compose.yml"))

    @field_validator("source_codecs", mode="before")
    @classmethod
    def _parse_codecs(cls, value: object) -> object:
        if isinstance(value, str):
            return [c.strip() for c in value.split(",") if c.strip()]
        return value

    @field_validator("source_codecs")
    @classmethod
    def _normalize_source_codecs(cls, value: list[str]) -> list[str]:
        return [codec.strip().lower() for codec in value if codec.strip()]

    @field_validator("destination_codec", mode="before")
    @classmethod
    def _normalize_destination_codec(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("ffmpeg_bin", mode="before")
    @classmethod
    def _normalize_ffmpeg_bin(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value


@lru_cache
def get_config() -> Config:
    return Config()
