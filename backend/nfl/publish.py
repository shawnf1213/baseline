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
import time

log = logging.getLogger("baseline.nfl.publish")

def _svc_headers() -> dict:
    """X-Service-Token for every backend call — see core/service_token.py."""
    t = (os.getenv("BASELINE_SERVICE_TOKEN") or "").strip()
    return {"X-Service-Token": t} if t else {}


API_BASE = os.getenv(
    "BASELINE_API_URL", "https://backend-production-84ab.up.railway.app"
).rstrip("/")
TOKEN = os.getenv("NFL_BOARD_TOKEN", "")
TIMEOUT = 45

# Cap on rows pushed in one call. A full Sunday prices a few hundred lines and
# the payload is small, but an unbounded POST is how a transport limit becomes a
# silent truncation somewhere in the middle.
MAX_ROWS = 400

# ── EVERY GAME THE BOOKS LIST, EVERY DAY (operator, 2026-10-06) ──────────────
# The website refresh used to scan "today plus one day", so on the four days a
# week with no football tomorrow it priced nothing and the board read empty —
# a Tuesday showed no NFL at all while PrizePicks was already listing
# Thursday's and Sunday's lines. The rule now: whenever a book lists a line for
# a game in the next REFRESH_WINDOW_DAYS, it is priced and on the board,
# whatever the Discord posting schedule is doing.
#
# Only the WINDOW changed. The scan, the model, its filters and its ranking are
# the ones the board always used; the Discord boards still pin their own
# one-day slate (bot.py nfl_daily_boards) and are untouched by this.
REFRESH_WINDOW_DAYS = int(os.getenv("NFL_REFRESH_WINDOW_DAYS", "7") or 7)
BOOKS = ("prizepicks", "underdog")

# Profiles are season snapshots — the weekly frame, the depth chart, the
# defence table — and none of it moves between two refreshes 45 minutes apart.
# Rebuilding 120 of them per game day per refresh, all week, is pure load, so a
# profile is reused for this long and only new names are built.
PROFILE_TTL_SECONDS = int(os.getenv("NFL_PROFILE_TTL_MINUTES", "180") or 180) * 60
_PROFILE_CACHE: dict = {}    # (slate, player) -> (monotonic ts, payload entry)
_TABLES_CACHE: dict = {}     # "t" -> (monotonic ts, defence tables)
_PLAYERS_SENT: dict = {}     # slate -> (frozenset(names), monotonic ts)


def _slate_of(r: dict, fallback) -> str:
    """The ET date a row's game is PLAYED — the slate it belongs to.

    Stamping rows with the scan date instead filed Monday night's game under
    Sunday, and with a week-long scan it would file Sunday's games under
    Tuesday. The posted record keys on the game date, so this is also what lets
    a board row find its ⭐ and its result.
    """
    from .board import _et_date
    d = _et_date(r.get("kickoff"))
    return str(d) if d else str(fallback)


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
                             headers={"X-NFL-Board-Token": TOKEN, **_svc_headers()},
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
        n = publish(rows[:max_rows], book, str(day))
        # Profiles come from the same scan, so the sheet can never show stats
        # for a player the board does not have.
        publish_players(rows[:max_rows], str(day))
        return n
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl publish_scan failed: %s", exc)
        return 0


def publish_upcoming(books=BOOKS, day=None, window_days: int = None,
                     max_rows: int = MAX_ROWS) -> dict:
    """Price every listed line with a game in the window; publish per game day.

    What the scheduled website refresh calls. Each (book, game day) is its own
    stored slate, so the backend's finished-slate filter retires Thursday's
    game on Friday without touching Sunday's lines, and each row merges with
    the posted record under the same date the record uses.

    Profiles are published once per game day from BOTH books' rows — one call
    per book let the second book's player list replace the first's.

    Returns {book: {slate: rows written}}. Never raises.
    """
    from . import board as _b
    out = {}
    try:
        day = day or _b.slate_date()
        win = REFRESH_WINDOW_DAYS if window_days is None else int(window_days)
        for_profiles = {}
        for book in books:
            out[book] = {}
            try:
                rows = _b.scan_board(book, one_per_player=False, day=day,
                                     window_days=win) or []
            except Exception as exc:  # noqa: BLE001 — one book must not stop the other
                log.warning("nfl publish_upcoming: %s scan failed: %s", book,
                            str(exc)[:160])
                continue
            by_slate = {}
            for r in rows:
                by_slate.setdefault(_slate_of(r, day), []).append(r)
            # A game day with no rows this time is left as it stands rather than
            # cleared: an empty scan is far more often a feed hiccup than a book
            # pulling a whole slate, and the board already behaves this way.
            for slate in sorted(by_slate):
                grp = by_slate[slate][:max_rows]
                out[book][slate] = publish(grp, book, slate)
                for_profiles.setdefault(slate, []).extend(grp)
        for slate in sorted(for_profiles):
            # Strongest first, so the profile cap keeps the plays people open.
            ranked = sorted(for_profiles[slate],
                            key=lambda r: -(r.get("win_prob") or 0.0))
            publish_players(ranked, slate)
        log.warning("nfl publish_upcoming: %s (+%dd) -> %s", day, win,
                    {b: dict(v) for b, v in out.items()})
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.exception("nfl publish_upcoming failed: %s", exc)
    return out

# ── PLAYER PROFILES ──────────────────────────────────────────────────────────
# The website's NFL card opened a sheet with no stats and no history, because
# the backend has no way to compute them — nfl/queries.py ships with the bot.
# Same handoff as the board: the bot computes, the backend serves.
#
# PROFILES ARE EXPENSIVE, so this is capped and deduped. player_profile() reads
# the weekly frame, the depth chart and the snap counts, and builds opponent
# splits on top; running it for 171 board rows would mean running it repeatedly
# for the same player. Unique players only, and a hard ceiling.
MAX_PLAYERS = 120


def _matchup(opponent: str, tables: dict, normalize_team) -> dict:
    """{opponent, by_prop: {prop: {rank, of, factor, raw, raw_label}}}.

    Returns {} for an unknown opponent rather than a table of average ranks: a
    card that says "16th of 32" about a team we could not identify is worse than
    a card that says nothing.
    """
    if not opponent:
        return {}
    try:
        team = normalize_team(opponent)
        by = {p: tbl[team] for p, tbl in (tables or {}).items()
              if tbl and team in tbl}
        return {"opponent": team, "by_prop": by} if by else {}
    except Exception:  # noqa: BLE001 — Rule 2, one player must not stop the rest
        return {}


def publish_players(rows: list, slate_date: str, limit: int = MAX_PLAYERS) -> int:
    """Publish profile + recent form for the players on a board.

    Never raises. A profile that fails is SKIPPED rather than published empty:
    a card showing blank stats is worse than a card that says it has none.
    """
    if not TOKEN or not rows:
        return 0
    try:
        import requests
        from . import queries as _q
        from . import ratings as _r
        from .client import normalize_team

        now = time.monotonic()
        slate_key = str(slate_date)
        # Forget profiles well past their life so the cache cannot grow without
        # bound across a season of slates.
        for k in [k for k, (t, _) in _PROFILE_CACHE.items()
                  if now - t > 2 * PROFILE_TTL_SECONDS]:
            _PROFILE_CACHE.pop(k, None)

        # THE DEFENCE THIS PLAYER FACES, computed once for the whole board.
        # The website could only ever ask "how has he done against this team
        # before", and 71% of board rows had no such game — most opponents are
        # faced once a season or not at all. What the defence allows EVERYONE is
        # the question with a real sample behind it, and it is the same rating
        # the projection already applied, so the card shows a model input rather
        # than a decorative stat. One table per prop family; 32 lookups each.
        # Reused for PROFILE_TTL_SECONDS — it is a season-to-date rating.
        hit = _TABLES_CACHE.get("t")
        if hit and now - hit[0] < PROFILE_TTL_SECONDS:
            tables = hit[1]
        else:
            tables = {p: _r.defense_table(p) for p in _r.PROP_METRIC}
            _TABLES_CACHE["t"] = (now, tables)

        seen, payload, built = set(), [], 0
        for r in rows:
            name = r.get("player")
            if not name or name in seen:
                continue
            seen.add(name)
            if len(payload) >= limit:
                break
            cached = _PROFILE_CACHE.get((slate_key, name))
            if cached and now - cached[0] < PROFILE_TTL_SECONDS:
                payload.append(cached[1])
                continue
            try:
                prof = _q.player_profile(name) or {}
                if not prof:
                    continue
                # A FULL SEASON, not six games. The website draws a game-log
                # bar chart with the prop line through it and counts how often
                # he cleared it; six bars cannot support a hit rate, and the
                # payload is a few hundred bytes per player.
                form = _q.recent_form(name, 17) or {}
                # Rides INSIDE the profile blob on purpose. nfl_players_replace
                # persists `profile` verbatim but copies only named top-level
                # keys, so a sibling key would be silently dropped — and this
                # needs no column, because the opponent is fixed per player per
                # slate.
                prof["matchup"] = _matchup(r.get("opponent"), tables,
                                           normalize_team)
                entry = {
                    "player": prof.get("player") or name,
                    "team": r.get("team") or "",
                    "position": prof.get("position") or "",
                    "profile": prof,
                    "form": form if not form.get("ambiguous") else {},
                }
                payload.append(entry)
                _PROFILE_CACHE[(slate_key, name)] = (now, entry)
                built += 1
            except Exception:  # noqa: BLE001 — one player must not stop the rest
                log.warning("nfl publish: profile failed for %s", name)
        if not payload:
            return 0
        # Nothing new to say: same players, all from cache, sent recently.
        names = frozenset(p["player"] for p in payload)
        sent = _PLAYERS_SENT.get(slate_key)
        if (not built and sent and sent[0] == names
                and now - sent[1] < PROFILE_TTL_SECONDS):
            return len(payload)
        resp = requests.post(f"{API_BASE}/api/nfl/players",
                             json={"slate_date": str(slate_date),
                                   "players": payload},
                             headers={"X-NFL-Board-Token": TOKEN, **_svc_headers()},
                             timeout=120)
        resp.raise_for_status()
        n = int((resp.json() or {}).get("written") or 0)
        if n:
            _PLAYERS_SENT[slate_key] = (names, now)
        log.warning("nfl publish: %d player profile(s) -> website (%s, %d built, "
                    "%d cached)", n, slate_date, built, len(payload) - built)
        return n
    except Exception as exc:  # noqa: BLE001 — Rule 2
        log.warning("nfl publish_players failed (%s): %s", slate_date,
                    str(exc)[:160])
        return 0
