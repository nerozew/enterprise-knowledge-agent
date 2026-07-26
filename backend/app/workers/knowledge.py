from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.core.database import SessionFactory, engine
from app.services.knowledge import KnowledgeWorker, close_knowledge_adapters

logger = logging.getLogger(__name__)


async def run() -> None:
    settings = get_settings()
    try:
        while True:
            async with SessionFactory() as session:
                processed = await KnowledgeWorker(session, settings).drain(limit=100)
            if processed == 0:
                await asyncio.sleep(1)
    finally:
        await close_knowledge_adapters()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
