"""Daily 02:00 UTC price collection flow served by the Prefect runner."""

from __future__ import annotations

from prefect import flow, get_run_logger
from prefect.runtime import flow_run
from prefect.server.schemas.schedules import CronSchedule

from ingestion.collector import run_collection


@flow(name="smartfare-daily-ingest")
async def daily_ingest() -> dict[str, int]:
    logger = get_run_logger()
    succeeded, failed = await run_collection(dag_run_id=str(flow_run.id))
    logger.info("Collection completed: %s succeeded, %s failed", succeeded, failed)
    if failed:
        raise RuntimeError(f"{failed} provider requests failed; see request log for details")
    return {"succeeded": succeeded, "failed": failed}


if __name__ == "__main__":
    daily_ingest.serve(
        name="smartfare-daily-ingest",
        schedule=CronSchedule(cron="0 2 * * *", timezone="UTC"),
        pause_on_shutdown=False,
        limit=1,
        tags=["ingestion", "smartfare"],
    )
