from __future__ import annotations

import os

import httpx


async def send_alert(title: str, message: str) -> dict[str, bool]:
    results = {"slack": False, "telegram": False}
    text = f"[SmartFare Alert] {title}\n{message}"

    slack_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    async with httpx.AsyncClient(timeout=10.0) as client:
        if slack_url:
            try:
                resp = await client.post(slack_url, json={"text": text})
                results["slack"] = 200 <= resp.status_code < 300
            except httpx.HTTPError:
                results["slack"] = False

        if tg_token and tg_chat_id:
            try:
                resp = await client.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json={"chat_id": tg_chat_id, "text": text},
                )
                results["telegram"] = 200 <= resp.status_code < 300
            except httpx.HTTPError:
                results["telegram"] = False

    return results
