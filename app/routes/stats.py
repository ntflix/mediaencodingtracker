"""Routes: statistics endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_session
from app.models import ConversionJob, ConversionStatus, MediaFile
from app.schemas import StatsOut

router = APIRouter(prefix="/api/stats", tags=["stats"])

type _Auth = Annotated[str, Depends(require_auth)]
type _Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("", response_model=StatsOut)
async def get_stats(
    _: _Auth,
    session: _Session,
) -> StatsOut:
    # --- file aggregates ---------------------------------------------------
    file_rows = (await session.execute(select(MediaFile))).scalars().all()
    total_files = len(file_rows)
    total_size_bytes = sum(f.size_bytes for f in file_rows)
    missing_files = sum(1 for f in file_rows if f.is_missing)

    by_codec_count: dict[str, int] = {}
    by_codec_bytes: dict[str, int] = {}
    for f in file_rows:
        by_codec_count[f.video_codec] = by_codec_count.get(f.video_codec, 0) + 1
        by_codec_bytes[f.video_codec] = (
            by_codec_bytes.get(f.video_codec, 0) + f.size_bytes
        )

    # --- job aggregates ----------------------------------------------------
    job_status_counts: dict[str, int] = {}
    rows = await session.execute(
        select(ConversionJob.status, func.count()).group_by(ConversionJob.status)
    )
    for status, count in rows:
        job_status_counts[status] = count

    return StatsOut(
        total_files=total_files,
        total_size_bytes=total_size_bytes,
        missing_files=missing_files,
        by_codec_count=by_codec_count,
        by_codec_bytes=by_codec_bytes,
        jobs_pending=job_status_counts.get(ConversionStatus.PENDING, 0),
        jobs_running=job_status_counts.get(ConversionStatus.RUNNING, 0),
        jobs_completed=job_status_counts.get(ConversionStatus.COMPLETED, 0),
        jobs_failed=job_status_counts.get(ConversionStatus.FAILED, 0),
    )
