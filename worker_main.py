# worker_main.py
# PURPOSE: Entry point for the ARQ Background Worker process.
# WHY HERE: Runs independently from the bot to process the Transactional Outbox.

# worker_main.py
from arq import run_worker

from app.infrastructure.logging import configure_logging
from app.infrastructure.worker import WorkerSettings


def main() -> None:
    configure_logging()
    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()