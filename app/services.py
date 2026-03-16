"""Shared service logic used by both HTTP routes and the scheduler."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.app_state import AppState
from app.config import Config
from app.models import ConversionJob, ConversionStatus, MediaFile
from app.scanner import scan_directory
from app.schemas import ScanResult
from app.worker import ConversionWorker

logger = logging.getLogger(__name__)


async def run_scan(
    session: AsyncSession,
    media_root: Path,
    app_state: AppState | None = None,
) -> ScanResult:
    """Scan *media_root* and synchronise results with the database."""
    if app_state is not None:
        app_state.scan.is_running = True
        app_state.scan.total_files = 0
        app_state.scan.probed_files = 0
        app_state.scan.error = None
        app_state.notify()

    async def _on_progress(probed: int, total: int) -> None:
        if app_state is not None:
            app_state.scan.total_files = total
            app_state.scan.probed_files = probed
            app_state.notify()

    now = datetime.now(UTC).replace(tzinfo=None)
    new = updated = missing = 0

    try:
        infos = await scan_directory(media_root, on_progress=_on_progress)
    except Exception as exc:
        if app_state is not None:
            app_state.scan.is_running = False
            app_state.scan.error = str(exc)
            app_state.notify()
        raise
    found_paths: set[str] = set()

    for info in infos:
        rel = str(info.path.relative_to(media_root))
        found_paths.add(rel)

        result = await session.execute(select(MediaFile).where(MediaFile.path == rel))
        existing = result.scalar_one_or_none()

        if existing is None:
            session.add(
                MediaFile(
                    path=rel,
                    filename=info.path.name,
                    size_bytes=info.size_bytes,
                    duration_seconds=info.duration_seconds,
                    video_codec=info.video_codec,
                    audio_codec=info.audio_codec,
                    width=info.width,
                    height=info.height,
                    container_format=info.container_format,
                    discovered_at=now,
                    last_seen_at=now,
                    is_missing=False,
                )
            )
            new += 1
        else:
            existing.last_seen_at = now
            existing.size_bytes = info.size_bytes
            existing.video_codec = info.video_codec
            existing.is_missing = False
            updated += 1

    # Mark files no longer on disk as missing.
    all_rows = (await session.execute(select(MediaFile))).scalars().all()
    for mf in all_rows:
        if mf.path not in found_paths and not mf.is_missing:
            mf.is_missing = True
            missing += 1

    await session.commit()
    logger.info("Scan complete: %d new, %d updated, %d missing", new, updated, missing)

    result = ScanResult(
        new_files=new,
        updated_files=updated,
        missing_files=missing,
        total_scanned=len(infos),
    )
    if app_state is not None:
        app_state.scan.is_running = False
        app_state.scan.last_scan_at = datetime.now(UTC).replace(tzinfo=None)
        app_state.scan.new_files = new
        app_state.scan.updated_files = updated
        app_state.scan.missing_files = missing
        app_state.notify()

    return result


async def enqueue_auto_convert(
    session: AsyncSession,
    worker: ConversionWorker,
    config: Config,
) -> int:
    """Create and enqueue pending jobs for files that match configured source codecs."""
    if not config.auto_convert_enabled:
        return 0

    target = set(config.source_codecs)
    quality = config.default_quality
    delete = config.delete_original_after_convert

    # Find files matching target codecs that have no pending/running job.
    subq = select(ConversionJob.media_file_id).where(
        ConversionJob.status.in_([ConversionStatus.PENDING, ConversionStatus.RUNNING])
    )
    result = await session.execute(
        select(MediaFile).where(
            MediaFile.video_codec.in_(target),
            MediaFile.is_missing.is_(False),
            MediaFile.id.not_in(subq),
        )
    )
    files = result.scalars().all()

    count = 0
    for mf in files:
        job = ConversionJob(
            media_file_id=mf.id,
            quality=quality,
            delete_original=delete,
        )
        session.add(job)
        await session.flush()  # assign job.id
        await worker.enqueue(job.id)
        count += 1

    await session.commit()
    if count:
        logger.info("Auto-enqueued %d conversion jobs", count)
    return count
