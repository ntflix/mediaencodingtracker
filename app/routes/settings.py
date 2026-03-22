"""Routes: compose-driven settings and setup checks."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import Config
from app.database import get_session
from app.schemas import MessageOut, SetupCheckItem, SetupCheckOut, UserSettingsOut
from app.services import enqueue_auto_convert, run_scan
from app.worker import ConversionWorker

router = APIRouter(prefix="/api/settings", tags=["settings"])

type _Auth = Annotated[str, Depends(require_auth)]
type _Session = Annotated[AsyncSession, Depends(get_session)]


def _get_worker(request: Request) -> ConversionWorker:
    return request.app.state.worker  # type: ignore[no-any-return]


def _get_config(request: Request) -> Config:
    return request.app.state.config  # type: ignore[no-any-return]


@router.get("", response_model=UserSettingsOut)
async def get_settings(
    _: _Auth,
    request: Request,
) -> UserSettingsOut:
    config = _get_config(request)
    return UserSettingsOut(
        auto_scan_enabled=config.auto_scan_enabled,
        scan_schedule=config.scan_schedule,
        auto_convert_enabled=config.auto_convert_enabled,
        convert_schedule=config.convert_schedule,
        default_quality=config.default_quality,
        delete_original_after_convert=config.delete_original_after_convert,
        destination_codec=config.destination_codec,
        source_codecs=config.source_codecs,
        ffmpeg_bin=config.ffmpeg_bin,
        lower_target_resolution_on_v4l2_fail=config.lower_target_resolution_on_v4l2_fail,
        min_target_resolution=config.min_target_resolution,
    )


@router.get("/setup-check", response_model=SetupCheckOut)
async def setup_check(
    _: _Auth,
    request: Request,
) -> SetupCheckOut:
    config = _get_config(request)
    checks = _build_setup_checks(config)
    return SetupCheckOut(
        ready=all(item.configured for item in checks),
        checks=checks,
    )


@router.post("/scan-now", response_model=MessageOut)
async def scan_now(
    request: Request,
    _: _Auth,
    session: _Session,
) -> MessageOut:
    result = await run_scan(
        session, request.app.state.config.media_root, request.app.state.app_state
    )
    return MessageOut(
        message=f"Scan complete: {result.new_files} new, {result.updated_files} updated, "
        f"{result.missing_files} missing ({result.total_scanned} total)."
    )


@router.post("/convert-now", response_model=MessageOut)
async def convert_now(
    request: Request,
    _: _Auth,
    session: _Session,
) -> MessageOut:
    worker: ConversionWorker = _get_worker(request)
    config = _get_config(request)
    count = await enqueue_auto_convert(session, worker, config)
    return MessageOut(message=f"Enqueued {count} conversion job(s).")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_setup_checks(config: Config) -> list[SetupCheckItem]:
    required_env_keys = [
        "AUTO_SCAN_ENABLED",
        "SCAN_SCHEDULE",
        "AUTO_CONVERT_ENABLED",
        "CONVERT_SCHEDULE",
        "DEFAULT_QUALITY",
        "DELETE_ORIGINAL_AFTER_CONVERT",
        "DESTINATION_CODEC",
        "SOURCE_CODECS",
        "LOWER_TARGET_RESOLUTION_ON_V4L2_FAIL",
        "MIN_TARGET_RESOLUTION",
    ]

    checks: list[SetupCheckItem] = []
    compose_env_keys = _extract_compose_env_keys(config.compose_file_path)

    for key in required_env_keys:
        configured = key in compose_env_keys
        details = (
            f"Found in compose environment at {config.compose_file_path}"
            if configured
            else f"Missing in compose environment at {config.compose_file_path}"
        )
        checks.append(SetupCheckItem(key=key, configured=configured, details=details))

    has_source_codecs = len(config.source_codecs) > 0
    checks.append(
        SetupCheckItem(
            key="SOURCE_CODECS_NON_EMPTY",
            configured=has_source_codecs,
            details=(
                f"Configured source codecs: {', '.join(config.source_codecs)}"
                if has_source_codecs
                else "SOURCE_CODECS resolved to an empty list"
            ),
        )
    )

    checks.append(
        SetupCheckItem(
            key="DESTINATION_CODEC_NON_EMPTY",
            configured=bool(config.destination_codec.strip()),
            details=f"Destination codec: {config.destination_codec}",
        )
    )

    return checks


def _extract_compose_env_keys(compose_file_path: Path) -> set[str]:
    if not compose_file_path.exists():
        return set()

    keys: set[str] = set()
    for raw_line in compose_file_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue

        key = line.split(":", 1)[0].strip()
        if key.isupper() and all(
            ch.isupper() or ch.isdigit() or ch == "_" for ch in key
        ):
            keys.add(key)
    return keys
