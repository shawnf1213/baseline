"""Tell the app a board or a recap just posted — AFTER the Discord post
succeeded, never before, and never in a way that can slow the bot down.

notify() hands the request to a daemon thread with a short timeout and
returns immediately; every failure is a log line. The backend fans out to
devices (backend/src/push.py); this only says what happened.
"""
import logging
import os
import threading

import requests

import service_token as _service_token

log = logging.getLogger("baseline-bot.push")

API_BASE = os.getenv("BASELINE_API_URL", "https://backend-production-84ab.up.railway.app").rstrip("/")
TIMEOUT = 10


def notify(event: str, sport: str, title: str, body: str, data: dict = None) -> None:
    """event: board | recap. sport: tennis | nfl | nba. Fire and forget."""
    payload = {"event": event, "sport": sport, "title": str(title)[:80],
               "body": str(body)[:180], "data": data or {}}

    def _run():
        try:
            r = requests.post(f"{API_BASE}/api/push/send", json=payload,
                              headers=_service_token.headers(), timeout=TIMEOUT)
            if r.status_code >= 300:
                log.warning("push notify %s/%s refused: HTTP %s %s", event, sport,
                            r.status_code, r.text[:120])
            else:
                log.info("push notify %s/%s queued: %s", event, sport, r.text[:120])
        except Exception as exc:  # noqa: BLE001 — a push failure is never the bot's problem
            log.warning("push notify %s/%s failed: %s", event, sport, str(exc)[:120])

    try:
        threading.Thread(target=_run, daemon=True, name=f"push-{event}-{sport}").start()
    except Exception as exc:  # noqa: BLE001
        log.warning("push notify thread failed: %s", exc)
