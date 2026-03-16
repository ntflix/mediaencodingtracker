"""Routes: media file listing and manual scan trigger."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_session
from app.models import MediaFile
from app.schemas import MediaFileOut, MediaFilePage, MessageOut, ScanResult
from app.services import run_scan

router = APIRouter(prefix="/api/files", tags=["files"])

type _Auth = Annotated[str, Depends(require_auth)]
type _Session = Annotated[AsyncSession, Depends(get_session)]

_FILE_SORT_COLS = {
    "filename": MediaFile.filename,
    "video_codec": MediaFile.video_codec,
    "width": MediaFile.width,
    "duration_seconds": MediaFile.duration_seconds,
    "size_bytes": MediaFile.size_bytes,
    "discovered_at": MediaFile.discovered_at,
}


@router.get("", response_model=MediaFilePage)
async def list_files(
    _: _Auth,
    session: _Session,
    codec: str | None = Query(default=None),
    missing: bool | None = Query(default=None),
    sort_by: Literal[
        "filename",
        "video_codec",
        "width",
        "duration_seconds",
        "size_bytes",
        "discovered_at",
    ] = Query(default="filename"),
    sort_dir: Literal["asc", "desc"] = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=500),
) -> MediaFilePage:
    stmt = select(MediaFile)
    if codec:
        stmt = stmt.where(MediaFile.video_codec == codec)
    if missing is True:
        stmt = stmt.where(MediaFile.is_missing.is_(True))
    elif missing is False:
        stmt = stmt.where(MediaFile.is_missing.is_(False))

    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()

    col = _FILE_SORT_COLS[sort_by]
    order = asc(col) if sort_dir == "asc" else desc(col)
    stmt = stmt.order_by(order).offset((page - 1) * per_page).limit(per_page)
    rows = (await session.execute(stmt)).scalars().all()

    return MediaFilePage(
        total=total,
        page=page,
        per_page=per_page,
        items=[MediaFileOut.model_validate(r) for r in rows],
    )


@router.get("/{file_id}", response_model=MediaFileOut)
async def get_file(
    file_id: int,
    _: _Auth,
    session: _Session,
) -> MediaFileOut:
    mf = await session.get(MediaFile, file_id)
    if mf is None:
        raise HTTPException(status_code=404, detail="File not found")
    return MediaFileOut.model_validate(mf)


@router.post("/scan", response_model=ScanResult)
async def trigger_scan(
    request: Request,
    _: _Auth,
    session: _Session,
) -> ScanResult:
    """Trigger an immediate directory scan (runs in-request, may take a while)."""
    media_root: Path = request.app.state.config.media_root
    return await run_scan(session, media_root, request.app.state.app_state)


@router.delete("/{file_id}", response_model=MessageOut)
async def delete_file_record(
    file_id: int,
    _: _Auth,
    session: _Session,
) -> MessageOut:
    """Remove a file record from the database (does not delete from disk)."""
    mf = await session.get(MediaFile, file_id)
    if mf is None:
        raise HTTPException(status_code=404, detail="File not found")
    await session.delete(mf)
    await session.commit()
    return MessageOut(message="Record deleted")
