"""In-process application state shared between routes, worker, and scheduler.

Consumers subscribe to change notifications via an asyncio.Queue so the SSE
endpoint can push updates without polling.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ScanState:
    is_running: bool = False
    total_files: int = 0
    probed_files: int = 0
    last_scan_at: datetime | None = None
    new_files: int = 0
    updated_files: int = 0
    missing_files: int = 0
    error: str | None = None


@dataclass
class WorkerState:
    queue_size: int = 0
    running_job_id: int | None = None
    running_job_progress: float = 0.0


class AppState:
    """Singleton held on ``app.state.app_state``."""

    def __init__(self) -> None:
        self.scan = ScanState()
        self.worker = WorkerState()
        self._listeners: list[asyncio.Queue[None]] = []

    def notify(self) -> None:
        """Wake all SSE subscribers."""
        for q in self._listeners:
            if q.empty():
                q.put_nowait(None)

    def subscribe(self) -> asyncio.Queue[None]:
        q: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._listeners.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[None]) -> None:
        try:
            self._listeners.remove(q)
        except ValueError:
            pass

    def to_dict(self) -> dict[str, object]:
        s = self.scan
        w = self.worker
        return {
            "scan": {
                "running": s.is_running,
                "total": s.total_files,
                "probed": s.probed_files,
                "last_at": s.last_scan_at.isoformat() if s.last_scan_at else None,
                "new": s.new_files,
                "updated": s.updated_files,
                "missing": s.missing_files,
                "error": s.error,
            },
            "worker": {
                "queue_size": w.queue_size,
                "job_id": w.running_job_id,
                "progress": w.running_job_progress,
            },
        }
