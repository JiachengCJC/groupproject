"""Run the shared durable task worker: python -m app.worker."""
import logging
import signal

from .config import Settings
from .database import Database
from .jobs import Worker


def main():
    logging.basicConfig(level=logging.INFO)
    settings=Settings.from_env()
    db=Database(settings.database_url or settings.db_path,analytics_url=settings.analytics_database_url,analytics_role=settings.analytics_role)
    db.initialize()
    worker=Worker(db,settings)
    def stop(*_):worker.stop_event.set()
    signal.signal(signal.SIGTERM,stop)
    signal.signal(signal.SIGINT,stop)
    worker.run()


if __name__=="__main__":
    main()
