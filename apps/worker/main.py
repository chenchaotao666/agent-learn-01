"""Run independently: python -m apps.worker.main [--once]."""
import argparse
import asyncio

from src.config import Settings
from src.ingestions import Ingestions
from src.rag.embedding import Embedder
from src.rag.service import RagService
from src.store import Store


async def main(once=False):
    settings = Settings.from_env()
    store = Store(settings.database)
    jobs = Ingestions(store, RagService(store, Embedder(settings), settings.min_score))
    while True:
        worked = await jobs.work_once()
        if once:
            return
        if not worked:
            await asyncio.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    asyncio.run(main(parser.parse_args().once))
