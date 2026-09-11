"""
Publish the scanned NFL board to the backend, for the website to render.

WHY THE BOT PUSHES INSTEAD OF THE BACKEND PRICING. The model lives in nfl/,
which deploys with the BOT. Running it inside the web backend would mean pyarrow
plus a play-by-play parquet of a few hundred megabytes on the service that has
to cold-start fast for the app — and a second copy of a cache the bot already
maintains. The bot scans on a schedule anyway, so it posts the result and the
backend simply stores and serves it.

WHAT GETS PUSHED IS THE WHOLE BOARD, NOT THE POSTED PICKS. Those are different
things and they live in different tables. nfl_picks is the record: the handful
of plays that were posted, kept forever, graded. nfl_board is the market: every
line the scan could price, replaced wholesale on each run, never graded. The
website needs the second to show what the tennis board shows.

THE WRITE IS TOKEN-GATED. Without NFL_BOARD_TOKEN this does nothing and says so
— an open ingest would let anyone publish invented projections under our name.
"""

import logging
import os

log = logging.getLogger("baseline.nfl.publish")

API_BASE = os.getenv(
    "BASELINE_API_URL", "https://backend-production-84ab.up.railway.app"
).rstrip("/")
TOKEN = os.getenv("NFL_BOARD_TOKEN", "")
TIMEOUT = 45

# Cap on rows pushed in one call. A full Sunday prices a few hundred lines and
# the payload is small, but an unbounded POST is how a transport limit becomes a
# silent truncation somewhere in the middle.
MAX_ROWS = 400


def _row(r: dict, slate: str, book: str) -> dict:
    return {
        "book": book, "slate_date": slate,
        "player": r.get("player"), "team": r.get("team") or "",
        "opponent": r.get("opponent") or "",
        "matchup": r.get("matchup") or "",
        "kickoff": str(r.get("kickoff") or ""),
        "prop_type": r.get("prop"), "line": r.get("line"),
        "model_projection": r.get("projection"),
        "lean": r.get("lean"), "confidence": r.get("win_prob"),
        "edge": r.get("edge"),
        "usage_window": r.get("window") or "",
        "prior_season_only": 1 if r.get("prior_season_only") else 0,
    }


def publish(rows: list, book: str, slate_date: str) -> int:
    """Replace the stored board for one (book, slate). Returns rows written.

    Never raises — a publish failure must cost the website its refresh, never
    the Discord post that already went out.
    """
    if not TOKEN:
        log.info("nfl publish: NFL_BOARD_TOKEN unset — not publishing the board "
                 "to the website")
        return 0
    if not rows:
        return 0
    try:
        import requests
        payload = {"book": book, "slate_date": str(slate_date),
                   "rows": [_row(r, str(slate_date), book) for r in rows[:MAX_ROWS]]}
        resp = requests.post(f"{API_BASE}/api/nfl/board", json=payload,
                             headers={"X-NFL-Board-Token": TOKEN},
                             timeout=TIMEOUT)
        resp.raise_for_status()
        n = int((resp.json() or {}).get("written") or 0)
        log.warning("nfl publish: %d board row(s) -> website (%s %s)",
                    n, book, slate_date)
        return n
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl publish failed (%s %s): %s", book, slate_date,
                    str(exc)[:160])
        return 0


def publish_scan(book: str = "prizepicks", day=None, window_days: int = None,
                 max_rows: int = MAX_ROWS) -> int:
    """Scan and publish in one call — what the scheduled refresh uses.

    Publishes the FULL ranked board, not the top N: the website shows the whole
    market with our number beside each line, and trimming here would make the
    site a copy of the Discord post instead.
    """
    from . import board as _b
    try:
        day = day or _b.slate_date()
        rows = _b.scan_board(book, one_per_player=False, day=day,
                             window_days=window_days)
        return publish(rows[:max_rows], book, str(day))
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl publish_scan failed: %s", exc)
        return 0
