"""Shared pytest fixtures."""

from __future__ import annotations

import base64
import pathlib
import tempfile
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Config, get_config
from app.database import get_session
from app.main import create_app
from app.models import Base, UserSettings


# ---------------------------------------------------------------------------
# Shared engine — StaticPool ensures all sessions see the same in-memory DB.
# ---------------------------------------------------------------------------


def _make_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def _init_engine(
    engine: AsyncEngine,
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as s:
        s.add(UserSettings(id=1))
        await s.commit()


# ---------------------------------------------------------------------------
# Per-test shared state
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def _shared() -> (
    AsyncIterator[
        tuple[AsyncEngine, async_sessionmaker[AsyncSession], pathlib.Path, Config]
    ]
):
    engine, factory = _make_engine()
    await _init_engine(engine, factory)

    tmp_media = pathlib.Path(tempfile.mkdtemp())
    test_config = Config(
        media_root=tmp_media,
        db_path=tmp_media / "tracker.db",
        admin_user="testuser",
        admin_pass="testpass",
        compose_file_path=pathlib.Path(__file__).resolve().parents[1]
        / "docker-compose.yml",
    )
    yield engine, factory, tmp_media, test_config
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(
    _shared: tuple[AsyncEngine, async_sessionmaker[AsyncSession], pathlib.Path, Config],
) -> AsyncIterator[AsyncSession]:
    _, factory, _, _ = _shared
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(
    _shared: tuple[AsyncEngine, async_sessionmaker[AsyncSession], pathlib.Path, Config],
) -> AsyncIterator[AsyncClient]:
    """Return an AsyncClient wired to the same in-memory DB as db_session."""
    _, factory, tmp_media, test_config = _shared

    from app.app_state import AppState
    from app.worker import ConversionWorker

    app_state = AppState()
    app = create_app()
    app.dependency_overrides[get_config] = lambda: test_config
    app.dependency_overrides[get_session] = _override_session(factory)
    app.state.config = test_config
    app.state.app_state = app_state
    app.state.worker = ConversionWorker(factory, tmp_media, test_config, app_state)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _override_session(
    factory: async_sessionmaker[AsyncSession],
):  # type: ignore[no-untyped-def]
    async def _inner() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    return _inner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def auth_header(user: str = "testuser", password: str = "testpass") -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}
