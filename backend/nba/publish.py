"""
Publish the scanned NBA board to the backend, for the website to render.

WHY THE BOT PUSHES INSTEAD OF THE BACKEND PRICING — the same division nfl/
uses. The model lives in nba/, which deploys with the BOT, and a board scan
holds a season of game logs in memory plus a cached parquet. Running it inside
the web backend would put that on the service that has to cold-start fast for
the app, and keep a second copy of a cache the bot already maintains. The bot
scans on a schedule anyway, so it posts the result and the backend stores and
serves it.

WHAT GETS PUSHED IS THE WHOLE BOARD, NOT THE POSTED PICKS. Those are different
things in different tables. nba_picks is the record: the handful of plays that
were posted, kept forever, graded. nba_board is the market: every line the scan
could price, replaced wholesale each run, never graded.

THE WRITE IS TOKEN-GATED. Without NBA_BOARD_TOKEN this does nothing and says so
— an open ingest would let anyone publish invented projections under our name.
"""

import logging
import os

log = logging.getLogger("baseline.nba.publish")

API_BASE = os.getenv(
    "BASELINE_API_URL", "https://backend-production-84ab.up.railway.app"
).rstrip("/")
TOKEN = os.getenv("NBA_BOARD_TOKEN", "")
TIMEOUT = 45

# Cap on rows pushed in one call. A full slate prices a few hundred lines and
# the payload is small, but an unbounded POST is how a transport limit becomes a
# silent truncation somewhere in the middle.
MAX_ROWS = 400


def _row(r: dict, slate: str, book: str) -> dict:
    return {
        "book": book, "slate_date": slate,
        "player": r.get("player"), "team": r.get("team") or "",
        "opponent": r.get("opponent") or "",
        "matchup": r.get("matchup") or "",
        "tipoff": str(r.get("tipoff") or ""),
        "prop_type": r.get("prop"), "line": r.get("line"),
        "model_projection": r.get("projection"),
        "fair_line": r.get("fair_line"),
        "lean": r.get("lean"), "confidence": r.get("confidence"),
        "edge": r.get("edge"),
        "minutes": r.get("minutes"), "rotation": r.get("rotation") or "",
        "usage_window": r.get("window") or "",
        "prior_season_only": 1 if r.get("prior_season_only") else 0,
    }


def publish(rows: list, book: str, slate_date: str) -> int:
    """POST one slate's board. Returns rows written; 0 on any failure."""
    import requests
    if not TOKEN:
        log.warning("nba publish: NBA_BOARD_TOKEN unset — the website board is "
                    "NOT being updated (ingest is closed, not open)")
        return 0
    try:
        payload = {"book": book, "slate_date": slate_date,
                   "rows": [_row(r, slate_date, book) for r in (rows or [])
                            [:MAX_ROWS]]}
        r = requests.post(f"{API_BASE}/api/nba/board", json=payload,
                          headers={"x-nba-board-token": TOKEN},
                          timeout=TIMEOUT)
        r.raise_for_status()
        n = int((r.json() or {}).get("written") or 0)
        log.info("nba publish: %d row(s) -> website board (%s %s)",
                 n, book, slate_date)
        return n
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nba publish failed: %s", str(exc)[:200])
        return 0


def publish_scan(book: str = "prizepicks", day=None,
                 window_days: int = None, season: int = None) -> int:
    """Scan and publish in one call — what the scheduled job runs."""
    from . import board as _b
    try:
        slate = str(day or _b.slate_date())
        rows = _b.scan_board(book=book, season=season, one_per_player=False,
                             day=slate, window_days=window_days)
        return publish(rows, book, slate)
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nba publish_scan failed: %s", exc)
        return 0
