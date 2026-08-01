"""Отдельный процесс worker: jobs + периодические задачи (W-30).

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("dok.worker")


async def jobs_loop(stop: asyncio.Event) -> None:
    await asyncio.sleep(1)
    while not stop.is_set():
        try:
            db = dbmod.SessionLocal()
            try:
                n = await asyncio.to_thread(process_pending_batch, db, limit=5)
                if n:
                    log.info("Processed %s job(s)", n)
            finally:
                db.close()
        except Exception:
            log.exception("jobs loop error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=2)
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
    )
    log.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
