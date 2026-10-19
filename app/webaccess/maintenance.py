"""Периодическая ограниченная уборка просроченных идентификаторов защиты."""

import asyncio
import logging
import time

from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.product.service import locked_session
from app.webaccess.security import prune


def cleanup(database):
    with locked_session(database) as session:
        prune(session, int(time.time()), limit=2500)


async def maintain(database):
    while True:
        await asyncio.sleep(60)
        try:
            await run_in_threadpool(cleanup, database)
        except SQLAlchemyError:
            logging.getLogger("shluz.web").warning(
                "Уборка защиты отложена: база временно недоступна"
            )
