"""Routes: conversion job management."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import asc, desc, select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_session
from app.models import ConversionJob, ConversionStatus, MediaFile
from app.schemas import JobCreateRequest, JobLogOut, JobOut, JobPage, MessageOut
from app.worker import ConversionWorker

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

type _Auth = Annotated[str, Depends(require_auth)]
type _Session = Annotated[AsyncSession, Depends(get_session)]

_JOB_SORT_COLS = {
    "id": ConversionJob.id,
    "status": ConversionJob.status,
    "quality": ConversionJob.quality,
    "progress": ConversionJob.progress,
    "created_at": ConversionJob.created_at,
    "started_at": ConversionJob.started_at,
}


def _get_worker(request: Request) -> ConversionWorker:
    return request.app.state.worker  # type: ignore[no-any-return]


@router.get("", response_model=JobPage)
async def list_jobs(
    _: _Auth,
    session: _Session,
    status: str | None = Query(default=None),
    sort_by: Literal[
        "id", "status", "quality", "progress", "created_at", "started_at"
    ] = Query(default="created_at"),
    sort_dir: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
) -> JobPage:
    stmt = select(ConversionJob).options(selectinload(ConversionJob.media_file))
    if status:
        stmt = stmt.where(ConversionJob.status == status)

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    col = _JOB_SORT_COLS[sort_by]
    order = asc(col) if sort_dir == "asc" else desc(col)
    stmt = stmt.order_by(order).offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(stmt)).scalars().all()

    return JobPage(
        total=total,
        page=page,
        per_page=per_page,
        items=[JobOut.model_validate(r) for r in rows],
    )


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: int,
    _: _Auth,
    session: _Session,
) -> JobOut:
    result = await session.execute(
        select(ConversionJob)
        .options(selectinload(ConversionJob.media_file))
        .where(ConversionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobOut.model_validate(job)


@router.get("/{job_id}/logs", response_model=JobLogOut)
async def get_job_logs(
    job_id: int,
    request: Request,
    _: _Auth,
    session: _Session,
    tail: int = Query(default=400, ge=20, le=5000),
) -> JobLogOut:
    job = await session.get(ConversionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")

    worker: ConversionWorker = _get_worker(request)
    log, truncated = worker.read_job_log(job_id, max_lines=tail)
    return JobLogOut(job_id=job_id, log=log, truncated=truncated)


@router.post("", response_model=list[JobOut], status_code=201)
async def create_jobs(
    body: JobCreateRequest,
    request: Request,
    _: _Auth,
    session: _Session,
) -> list[JobOut]:
    """Create conversion jobs for the given file IDs and enqueue them."""
    worker: ConversionWorker = _get_worker(request)
    created: list[JobOut] = []

    for file_id in body.media_file_ids:
        mf = await session.get(MediaFile, file_id)
        if mf is None:
            raise HTTPException(
                status_code=404, detail=f"MediaFile {file_id} not found"
            )

        job = ConversionJob(
            media_file_id=file_id,
            quality=body.quality,
            delete_original=body.delete_original,
        )
        session.add(job)
        await session.flush()
        await worker.enqueue(job.id)
        created.append(JobOut.model_validate(job))

    await session.commit()
    return created


@router.post("/{job_id}/cancel", response_model=MessageOut)
async def cancel_job(
    job_id: int,
    request: Request,
    _: _Auth,
    session: _Session,
) -> MessageOut:
    job = await session.get(ConversionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (ConversionStatus.PENDING, ConversionStatus.RUNNING):
        raise HTTPException(
            status_code=400, detail=f"Job is {job.status}, cannot cancel"
        )

    worker: ConversionWorker = _get_worker(request)
    await worker.cancel(job_id)
    return MessageOut(message="Cancellation requested")


@router.delete("/{job_id}", response_model=MessageOut)
async def delete_job(
    job_id: int,
    _: _Auth,
    session: _Session,
) -> MessageOut:
    job = await session.get(ConversionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status == ConversionStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Cannot delete a running job")
    await session.delete(job)
    await session.commit()
    return MessageOut(message="Job deleted")
