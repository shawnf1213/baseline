"""Which pick does today's Short showcase?

THE ⭐ IF THERE IS ONE, OTHERWISE THE #1 PLAY — and the rules are IMPORTED from
pick_of_day, never restated here. Two separate implementations of "confidence"
already drifted apart between the bot and the website this month; a third copy of
the star rule would drift the same way, and this one would be publishing to
YouTube where nobody sees the disagreement until it is permanent.

What that gives us for free: STAR_BP_MIN_CONF (70), POTD_THRESHOLD (80), the
Break-Points-Won first refusal, the Total Games favourite gate, and every prop
exclusion — whatever they happen to be on the day this runs.
"""

import sys as _sys
try:
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
import sys
import datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, os.path.join(_REPO, "discord-bot"))

import requests  # noqa: E402

API = os.getenv("BASELINE_API",
                "https://backend-production-84ab.up.railway.app")

# The bot's own selection rules. Import failure is fatal ON PURPOSE: falling back
# to a local guess is precisely how the video would start disagreeing with
# Discord without anyone noticing.
import pick_of_day as POD  # noqa: E402


def _et_today() -> str:
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        tz = _dt.timezone(_dt.timedelta(hours=-4))
    return _dt.datetime.now(tz).date().isoformat()


def _rel_edge(p: dict) -> float:
    line = p.get("line")
    proj = p.get("model_projection")
    if not isinstance(line, (int, float)) or not line:
        return 0.0
    if not isinstance(proj, (int, float)):
        return 0.0
    return abs(proj - line) / abs(line)


def _is_challenger(p: dict) -> bool:
    t = (p.get("tournament") or "").lower()
    return any(k in t for k in ("challenger", "itf", "125"))


def _et(ts: str):
    """Parse an ISO timestamp and return it in ET."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/New_York")
    except Exception:  # noqa: BLE001
        tz = _dt.timezone(_dt.timedelta(hours=-4))
    s = str(ts or "").replace("Z", "+00:00")
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(tz)


def latest_batch() -> list:
    """The picks from the MOST RECENT run, not from a calendar day.

    THIS USED TO FILTER ON THE DATE and it selected the wrong board. The POTD run
    fires at 22:00 ET, which stamps generated_at at 02:0x UTC the FOLLOWING day —
    so matching an ET date string against a UTC timestamp picked up the PREVIOUS
    night's run. The symptom was the Short showcasing Sloane Stephens OVER 5.0
    while the bot had just posted Sloane Stephens UNDER 3.5; the pick it showed
    had already been graded a loss.

    Taking the newest batch instead removes the timezone arithmetic entirely: a
    run writes all of its picks within a second or two, so everything close to the
    newest timestamp is that run, and that is by definition what the bot just
    posted.
    """
    r = requests.get(f"{API}/api/results/record", timeout=180)
    r.raise_for_status()
    picks = [p for p in ((r.json() or {}).get("picks") or [])
             if p.get("generated_at") and not p.get("excluded_from_record")]
    if not picks:
        return []
    newest = max(_et(p["generated_at"]) for p in picks)
    window = _dt.timedelta(minutes=2)
    return [p for p in picks if newest - _et(p["generated_at"]) <= window]


def slate_date(rows: list) -> str:
    """The ET slate these picks are FOR, mirroring pick_of_day's own rule: a run
    from noon ET onwards is building tomorrow's board, not today's."""
    if not rows:
        return _et_today()
    t = _et(rows[0]["generated_at"])
    d = t.date() + (_dt.timedelta(days=1) if t.hour >= 12 else _dt.timedelta())
    return d.isoformat()


def todays_picks(day: str = None) -> list:
    """Back-compat shim — the latest run is what we post."""
    return latest_batch()


def choose(day: str = None):
    """(pick, kind) where kind is 'potd' or 'top'. (None, None) on an empty day.

    The ⭐ test is POD._star_eligible verbatim. When something is eligible, Break
    Points Won takes the slot ahead of higher-confidence plays — BP confidence is
    half a scenario probability while every other prop's is a data-quality
    composite, so they are not comparable numbers and BP loses a naive sort it
    should not lose. That is the bot's rule, and this just follows it.
    """
    rows = todays_picks(day)
    if not rows:
        return None, None

    # Rank the way the board does: tour matches above challengers, then relative
    # edge, then confidence.
    ordered = sorted(
        rows,
        key=lambda p: (0 if _is_challenger(p) else 1,
                       _rel_edge(p),
                       p.get("confidence") or 0),
        reverse=True)

    eligible = [p for p in ordered if POD._star_eligible(p)]
    if eligible:
        bp = next((p for p in eligible
                   if p.get("prop_type") == "Break Points Won"), None)
        return (bp or eligible[0]), "potd"
    return ordered[0], "top"


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    rows = todays_picks(day)
    print(f"picks for {day or _et_today()}: {len(rows)}\n")
    print(f"{'player':22} {'prop':24} {'line':>6} {'proj':>7} {'conf':>5} "
          f"{'rel_edge':>9}  {'star?':>6}")
    for p in sorted(rows, key=lambda p: -_rel_edge(p)):
        print(f"{(p.get('player') or '')[:22]:22} "
              f"{(p.get('prop_type') or '')[:24]:24} "
              f"{p.get('line'):>6} {p.get('model_projection'):>7} "
              f"{p.get('confidence'):>5} {_rel_edge(p):>9.3f}  "
              f"{str(POD._star_eligible(p)):>6}")
    pick, kind = choose(day)
    print()
    if not pick:
        print("nothing to show today")
    else:
        print(f"SHOWCASE ({kind}): {pick['player']} {pick['prop_type']} "
              f"{pick['lean']} {pick['line']} -> {pick['model_projection']} "
              f"(conf {pick['confidence']}, {pick.get('tournament')})")
