from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket

from app.core.config import get_settings
from app.core.database import SessionFactory, engine
from app.services.agent_dispatch import AgentDispatchWorker

logger = logging.getLogger(__name__)


async def main(*, once: bool = False) -> None:
    settings = get_settings()
    instance = os.getenv("AGENT_WORKER_INSTANCE_ID") or f"{socket.gethostname()}-{os.getpid()}"
    worker = AgentDispatchWorker(
        SessionFactory,
        settings,
        worker_id=instance[:80],
    )
    logger.info("P8 agent dispatch worker started", extra={"worker_instance": instance[:80]})
    try:
        if once:
            claimed = await worker.run_once()
            logger.info("P8 agent dispatch worker cycle completed", extra={"claimed": claimed})
        else:
            await worker.run_forever()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="P8 Agent dispatch worker")
    parser.add_argument("--once", action="store_true", help="claim one batch and exit")
    arguments = parser.parse_args()
    asyncio.run(main(once=arguments.once))
