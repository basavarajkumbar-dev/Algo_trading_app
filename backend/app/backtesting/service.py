"""BacktestService: runs queued backtests one at a time in the background.

A run executes in a worker thread (the signal replay inside it uses a
small process pool), so the API and the live paper engine keep running.
Progress is kept in memory and the outcome is stored when the run ends.
Runs left QUEUED or RUNNING by a restart are marked FAILED at startup.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.backtesting import repository as repo
from app.backtesting.runner import run_backtest
from app.backtesting.schemas import BacktestRequest
from app.core.logging import get_logger

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


@dataclass
class LiveProgress:
    progress: float
    message: str


class BacktestService:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        data_dir: str | None = None,
        workers: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self.data_dir = data_dir
        self.workers = workers
        self._queue: asyncio.Queue[tuple[uuid.UUID, BacktestRequest]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._live: dict[uuid.UUID, LiveProgress] = {}
        self._cancel: set[uuid.UUID] = set()
        self.current: uuid.UUID | None = None

    async def start(self) -> None:
        try:
            async with self._session_factory() as session:
                n = await repo.mark_interrupted(session)
            if n:
                logger.warning(
                    "%d backtest run(s) interrupted by the restart were marked FAILED", n
                )
        except Exception:  # noqa: BLE001 - the service still accepts runs once the DB is back
            logger.warning("Could not mark interrupted backtests")
        self._task = asyncio.create_task(self._worker(), name="backtests")

    async def stop(self) -> None:
        if self.current is not None:
            self._cancel.add(self.current)
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def queued(self) -> int:
        return self._queue.qsize()

    def live(self, run_id: uuid.UUID) -> LiveProgress | None:
        return self._live.get(run_id)

    async def submit(self, run_id: uuid.UUID, req: BacktestRequest) -> None:
        self._live[run_id] = LiveProgress(0.0, "Queued")
        await self._queue.put((run_id, req))

    def cancel(self, run_id: uuid.UUID) -> bool:
        if run_id in self._live:
            self._cancel.add(run_id)
            return True
        return False

    async def _worker(self) -> None:
        while True:
            run_id, req = await self._queue.get()
            try:
                await self._run(run_id, req)
            except Exception:  # noqa: BLE001 - one bad run must not stop the queue
                logger.exception("Backtest %s crashed", run_id)
            finally:
                self._queue.task_done()

    async def _run(self, run_id: uuid.UUID, req: BacktestRequest) -> None:
        if run_id in self._cancel:
            await self._finish(run_id, "CANCELLED", "Cancelled before it started")
            return
        self.current = run_id
        started = time.perf_counter()
        async with self._session_factory() as session:
            await repo.set_status(
                session, run_id, status="RUNNING", message="Starting", started_at=datetime.now(UTC)
            )

        def progress(p: float, message: str) -> None:
            self._live[run_id] = LiveProgress(round(min(max(p, 0.0), 1.0), 3), message)

        try:
            outcome = await asyncio.to_thread(
                run_backtest,
                req,
                data_dir=self.data_dir,
                progress=progress,
                should_stop=lambda: run_id in self._cancel,
                workers=self.workers,
            )
        except InterruptedError:
            await self._finish(run_id, "CANCELLED", "Cancelled")
            return
        except Exception as exc:  # noqa: BLE001 - stored on the run
            logger.exception("Backtest %s failed", run_id)
            await self._finish(
                run_id, "FAILED", "Failed", error=f"{type(exc).__name__}: {exc}"[:2000]
            )
            return
        finally:
            self.current = None
        duration = int((time.perf_counter() - started) * 1000)
        async with self._session_factory() as session:
            await repo.save_result(session, run_id, req, outcome, duration_ms=duration)
        self._live.pop(run_id, None)
        self._cancel.discard(run_id)
        logger.info("Backtest %s completed in %.1fs", run_id, duration / 1000)

    async def _finish(
        self, run_id: uuid.UUID, status: str, message: str, error: str | None = None
    ) -> None:
        async with self._session_factory() as session:
            await repo.set_status(
                session,
                run_id,
                status=status,
                message=message,
                error=error,
                finished_at=datetime.now(UTC),
            )
        self._live.pop(run_id, None)
        self._cancel.discard(run_id)
