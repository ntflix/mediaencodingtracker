"""Integration tests for the FastAPI HTTP endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConversionJob, ConversionStatus, MediaFile, Quality, VideoCodec
from tests.conftest import auth_header

_AUTH = auth_header()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _add_file(session: AsyncSession, codec: str = VideoCodec.HEVC) -> MediaFile:
    mf = MediaFile(
        path=f"test/{codec}_movie.mkv",
        filename=f"{codec}_movie.mkv",
        size_bytes=1_000_000_000,
        video_codec=codec,
        audio_codec="aac",
        width=1920,
        height=1080,
        duration_seconds=7200.0,
    )
    session.add(mf)
    await session.commit()
    await session.refresh(mf)
    return mf


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(client: AsyncClient) -> None:
    res = await client.get("/api/stats")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_wrong_password_returns_401(client: AsyncClient) -> None:
    res = await client.get("/api/stats", headers=auth_header("testuser", "wrong"))
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_api_docs_require_auth(client: AsyncClient) -> None:
    res = await client.get("/api/docs")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_openapi_requires_auth(client: AsyncClient) -> None:
    res = await client.get("/api/openapi.json")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_empty_db(client: AsyncClient) -> None:
    res = await client.get("/api/stats", headers=_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert data["total_files"] == 0
    assert data["jobs_pending"] == 0


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_files_empty(client: AsyncClient) -> None:
    res = await client.get("/api/files", headers=_AUTH)
    assert res.status_code == 200
    assert res.json()["total"] == 0


@pytest.mark.asyncio
async def test_get_file_not_found(client: AsyncClient) -> None:
    res = await client.get("/api/files/999", headers=_AUTH)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_file_record(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mf = await _add_file(db_session)
    res = await client.delete(f"/api/files/{mf.id}", headers=_AUTH)
    assert res.status_code == 200
    assert "deleted" in res.json()["message"].lower()


@pytest.mark.asyncio
async def test_list_files_codec_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _add_file(db_session, codec=VideoCodec.HEVC)
    await _add_file(db_session, codec=VideoCodec.H264)

    res = await client.get("/api/files?codec=hevc", headers=_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert all(f["video_codec"] == "hevc" for f in data["items"])


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_job(client: AsyncClient, db_session: AsyncSession) -> None:
    mf = await _add_file(db_session)
    res = await client.post(
        "/api/jobs",
        json={"media_file_ids": [mf.id], "quality": "medium", "delete_original": False},
        headers=_AUTH,
    )
    assert res.status_code == 201
    jobs = res.json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == ConversionStatus.PENDING
    assert jobs[0]["quality"] == Quality.MEDIUM


@pytest.mark.asyncio
async def test_create_job_unknown_file_returns_404(client: AsyncClient) -> None:
    res = await client.post(
        "/api/jobs",
        json={"media_file_ids": [9999]},
        headers=_AUTH,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_list_jobs_empty(client: AsyncClient) -> None:
    res = await client.get("/api/jobs", headers=_AUTH)
    assert res.status_code == 200
    assert res.json()["total"] == 0


@pytest.mark.asyncio
async def test_delete_pending_job(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mf = await _add_file(db_session)
    job = ConversionJob(media_file_id=mf.id, status=ConversionStatus.PENDING)
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    res = await client.delete(f"/api/jobs/{job.id}", headers=_AUTH)
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_cannot_delete_running_job(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mf = await _add_file(db_session)
    job = ConversionJob(media_file_id=mf.id, status=ConversionStatus.RUNNING)
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    res = await client.delete(f"/api/jobs/{job.id}", headers=_AUTH)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_get_job_logs_empty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    mf = await _add_file(db_session)
    job = ConversionJob(media_file_id=mf.id, status=ConversionStatus.FAILED)
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    res = await client.get(f"/api/jobs/{job.id}/logs", headers=_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert data["job_id"] == job.id
    assert data["log"] == ""
    assert data["truncated"] is False


@pytest.mark.asyncio
async def test_get_job_logs_not_found(client: AsyncClient) -> None:
    res = await client.get("/api/jobs/9999/logs", headers=_AUTH)
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings(client: AsyncClient) -> None:
    res = await client.get("/api/settings", headers=_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert "scan_schedule" in data
    assert "destination_codec" in data
    assert "source_codecs" in data
    assert "ffmpeg_bin" in data


@pytest.mark.asyncio
async def test_setup_check(client: AsyncClient) -> None:
    res = await client.get("/api/settings/setup-check", headers=_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert "ready" in data
    assert isinstance(data["checks"], list)


@pytest.mark.asyncio
async def test_stats_reflects_files(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _add_file(db_session, codec=VideoCodec.HEVC)
    await _add_file(db_session, codec=VideoCodec.H264)

    res = await client.get("/api/stats", headers=_AUTH)
    assert res.status_code == 200
    data = res.json()
    assert data["total_files"] == 2
    assert data["by_codec_count"]["hevc"] == 1
    assert data["by_codec_count"]["h264"] == 1


# ---------------------------------------------------------------------------
# SSE events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_requires_auth(client: AsyncClient) -> None:
    """SSE endpoint enforces auth."""
    res = await client.get("/api/events")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# AppState pub/sub (unit test — no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_appstate_to_dict_shape() -> None:
    """AppState.to_dict() always has the expected keys."""
    from app.app_state import AppState

    state = AppState()
    d = state.to_dict()
    assert set(d) == {"scan", "worker"}
    assert d["scan"]["running"] is False  # type: ignore[index]
    assert d["worker"]["job_id"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_appstate_notify_wakes_subscriber() -> None:
    """notify() puts a token into every subscriber queue."""
    import asyncio
    from app.app_state import AppState

    state = AppState()
    q = state.subscribe()

    assert q.empty()
    state.notify()
    # Queue should have exactly one item immediately (no await needed).
    assert not q.empty()

    # A second notify when queue is already full should NOT raise.
    state.notify()
    assert q.qsize() == 1  # still 1 (maxsize=1, put_nowait skipped silently)

    state.unsubscribe(q)
    state.notify()
    # After unsubscribe the queue should stay at 1 (nothing added).
    assert q.qsize() == 1
