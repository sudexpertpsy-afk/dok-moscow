"""Отдельный процесс worker: jobs + периодические задачи (W-30/W-32).

Запуск: python -m app.worker
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from app import db as dbmod
from app.billing.jobs import billing_background_loop
from app.services.jobs import process_pending_batch
from app.services.ops import ops_loop_tick

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dok.worker")


async def jobs_loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(1)
    ticks = 0
    while not stop.is_set():
        try:
            db = dbmod.SessionLocal()
            try:
                n = await asyncio.to_thread(process_pending_batch, db, limit=5)
                if n:
                    log.info("Processed %s job(s)", n)
                ticks += 1
                if ticks % 1800 == 0:  # ~раз в час при timeout=2
                    from app.services.cp_import import cleanup_stale_imports

                    cleaned = await asyncio.to_thread(cleanup_stale_imports)
                    if cleaned:
                        log.info("Removed %s stale import dirs", cleaned)
            finally:
                db.close()
        except Exception:
            log.exception("jobs loop error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
        except TimeoutError:
            pass


async def ops_loop(stop: asyncio.Event) -> None:
    """Heartbeat + алерты + еженедельный дайджест (W-32)."""
    await asyncio.sleep(3)
    while not stop.is_set():
        try:
            db = dbmod.SessionLocal()
            try:
                await asyncio.to_thread(ops_loop_tick, db)
            finally:
                db.close()
        except Exception:
            log.exception("ops loop error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            pass


async def main() -> None:
    stop = asyncio.Event()

    def _stop(*_args):
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # В worker всегда крутим периодику; веб-процесс отключает BILLING_WORKER
    os.environ.setdefault("BILLING_WORKER", "1")
    await asyncio.gather(
        jobs_loop(stop),
        billing_background_loop(stop),
        ops_loop(stop),
    )
    log.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
