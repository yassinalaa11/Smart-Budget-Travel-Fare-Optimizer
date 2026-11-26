from __future__ import annotations

import os

import httpx
from prefect import flow, get_run_logger, task
from prefect.server.schemas.schedules import CronSchedule

from ingestion.alerts import send_alert
from ml.train import run_training_pipeline


@task(name="train-and-backtest-ml-models")
async def train_models_task() -> dict:
    return await run_training_pipeline()


@task(name="reload-api-model-registry")
async def reload_api_models() -> bool:
    api_url = os.getenv("SMARTFARE_API_URL", "http://api:8000").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{api_url}/api/v1/models/reload")
            return 200 <= resp.status_code < 300
    except httpx.HTTPError:
        return False


@flow(name="smartfare-weekly-train")
async def weekly_train() -> dict:
    logger = get_run_logger()
    try:
        scorecard = await train_models_task()
        reloaded = await reload_api_models()
        logger.info(
            "Training complete: version=%s reloaded_api=%s",
            scorecard.get("model_version"),
            reloaded,
        )
        return {"scorecard": scorecard, "api_reloaded": reloaded}
    except Exception as exc:
        await send_alert("smartfare-weekly-train failed", str(exc))
        raise


if __name__ == "__main__":
    weekly_train.serve(
        name="smartfare-weekly-train",
        schedule=CronSchedule(cron="0 4 * * 0", timezone="UTC"),
        pause_on_shutdown=False,
        limit=1,
        tags=["ml", "smartfare"],
    )
