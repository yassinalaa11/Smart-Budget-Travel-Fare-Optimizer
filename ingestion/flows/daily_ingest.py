"""Daily 02:00 UTC price collection flow served by the Prefect runner."""

from __future__ import annotations

import asyncio

from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run
from prefect.server.schemas.schedules import CronSchedule

from ingestion.collector import run_collection


@task(name="build-silver-and-gold-warehouse")
async def build_warehouse() -> None:
    logger = get_run_logger()
    process = await asyncio.create_subprocess_exec(
        "dbt",
        "run",
        "--project-dir",
        "/app/transform",
        "--profiles-dir",
        "/app/transform",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await process.communicate()
    log_output = output.decode("utf-8", errors="replace").strip()
    if log_output:
        logger.info("dbt output:\n%s", log_output)
    if process.returncode != 0:
        raise RuntimeError(f"dbt run failed with exit code {process.returncode}")


@flow(name="smartfare-daily-ingest")
async def daily_ingest() -> dict[str, int | str]:
    logger = get_run_logger()
    succeeded, failed = await run_collection(dag_run_id=str(flow_run.id))
    logger.info("Collection completed: %s succeeded, %s failed", succeeded, failed)
    await build_warehouse()
    if failed:
        raise RuntimeError(f"{failed} provider requests failed; see request log for details")
    return {"succeeded": succeeded, "failed": failed, "warehouse": "built"}


if __name__ == "__main__":
    daily_ingest.serve(
        name="smartfare-daily-ingest",
        schedule=CronSchedule(cron="0 2 * * *", timezone="UTC"),
        pause_on_shutdown=False,
        limit=1,
        tags=["ingestion", "smartfare"],
    )
