"""Background conversion worker — processes jobs one at a time."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.app_state import AppState
from app.config import Config
from app.converter import ConversionResult, convert_file
from app.models import ConversionJob, ConversionStatus, CRF_MAP, MediaFile, Quality

logger = logging.getLogger(__name__)


class ConversionWorker:
    """Serialises conversion jobs through an asyncio Queue.

    A single worker is appropriate for the RPi4b — running multiple
    simultaneous ffmpeg encodes would saturate the CPU.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        media_root: Path,
        config: Config,
        app_state: AppState | None = None,
    ) -> None:
        self._sf = session_factory
        self._media_root = media_root
        self._config = config
        self._app_state = app_state
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._cancel_events: dict[int, asyncio.Event] = {}
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="conversion-worker")
        logger.info("Conversion worker started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Conversion worker stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, job_id: int) -> None:
        self._cancel_events[job_id] = asyncio.Event()
        await self._queue.put(job_id)
        logger.info("Enqueued job %d", job_id)
        self._notify_worker_state()

    async def cancel(self, job_id: int) -> bool:
        """Request cancellation of a pending or running job."""
        if event := self._cancel_events.get(job_id):
            event.set()
            return True
        return False

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    def _notify_worker_state(self) -> None:
        if self._app_state is not None:
            self._app_state.worker.queue_size = self._queue.qsize()
            self._app_state.notify()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._process(job_id)
            except Exception:
                logger.exception("Unhandled error processing job %d", job_id)
            finally:
                self._queue.task_done()
                self._cancel_events.pop(job_id, None)

    async def _process(self, job_id: int) -> None:  # noqa: C901  (a bit long but clear)
        # ------ mark running in AppState -----------------------------------
        if self._app_state is not None:
            self._app_state.worker.running_job_id = job_id
            self._app_state.worker.running_job_progress = 0.0
            self._app_state.worker.queue_size = self._queue.qsize()
            self._app_state.notify()

        try:
            await self._process_inner(job_id)
        finally:
            if self._app_state is not None:
                self._app_state.worker.running_job_id = None
                self._app_state.worker.running_job_progress = 0.0
                self._app_state.worker.queue_size = self._queue.qsize()
                self._app_state.notify()

    async def _process_inner(self, job_id: int) -> None:
        # ------ load job & file -----------------------------------------
        async with self._sf() as session:
            result = await session.execute(
                select(ConversionJob)
                .options(selectinload(ConversionJob.media_file))
                .where(ConversionJob.id == job_id)
            )
            job = result.scalar_one_or_none()
            if job is None:
                logger.warning("Job %d not found, skipping", job_id)
                return

            media_file = job.media_file
            input_path = self._media_root / media_file.path

            if not input_path.exists():
                job.status = ConversionStatus.FAILED
                job.error_message = "Source file not found on disk"
                job.completed_at = datetime.now(UTC).replace(tzinfo=None)
                await session.commit()
                return

            job.status = ConversionStatus.RUNNING
            job.started_at = datetime.now(UTC).replace(tzinfo=None)
            await session.commit()

        cancel_event = self._cancel_events.get(job_id, asyncio.Event())
        crf = CRF_MAP[Quality(job.quality)]
        duration = media_file.duration_seconds

        async def on_progress(progress: float) -> None:
            if self._app_state is not None:
                self._app_state.worker.running_job_progress = progress
                self._app_state.notify()
            async with self._sf() as ps:
                row = await ps.get(ConversionJob, job_id)
                if row is not None:
                    row.progress = progress
                    await ps.commit()

        conv_result: ConversionResult = await convert_file(
            input_path=input_path,
            crf=crf,
            destination_codec=self._config.destination_codec,
            on_progress=on_progress,
            cancel_event=cancel_event,
            duration_seconds=duration,
        )

        # ------ persist outcome -----------------------------------------
        async with self._sf() as session:
            job = await session.get(ConversionJob, job_id)
            assert job is not None

            job.completed_at = datetime.now(UTC).replace(tzinfo=None)

            if cancel_event.is_set():
                job.status = ConversionStatus.CANCELLED

            elif conv_result.success and conv_result.output_path:
                job.status = ConversionStatus.COMPLETED
                job.progress = 1.0
                job.output_path = str(
                    conv_result.output_path.relative_to(self._media_root)
                )
                if job.delete_original:
                    input_path.unlink(missing_ok=True)
                    mf = await session.get(MediaFile, job.media_file_id)
                    if mf is not None:
                        mf.is_missing = True

            else:
                job.status = ConversionStatus.FAILED
                job.error_message = conv_result.error

            await session.commit()
        logger.info("Job %d finished with status %s", job_id, job.status)
