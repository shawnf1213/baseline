"""
Match Charting Project — per-player PLAYSTYLE profiles.

Source: github.com/JeffSackmann/tennis_MatchChartingProject (raw CSVs). This is
the one Sackmann repo that is still alive — tennis_atp and tennis_wta are 404
and permanently gone (see the retirement/opponent-quality notes), but the
charting repo is actively updated; rows land within days of a charted match.

WHAT THIS ANSWERS that Sofascore cannot: not who won, but HOW they play. Return
depth, serve-and-volley frequency, forehand/backhand share, how many shots they
keep in a rally, how often they come forward. Sofascore gives outcomes; this
gives style.

WHAT IT CANNOT ANSWER: movement. There is no court-coverage or speed field
anywhere in the dataset. A "slow mover" can only ever be inferred here from
rally behaviour, never measured. Do not present any number from this module as
a fitness or movement reading.

COVERAGE IS BIMODAL AND THAT GOVERNS EVERY USE. The project is crowd-charted:
1,734 players appear, but the MEDIAN player has about two charted matches. The
top 100 are deep (Swiatek 226, Ruud 171, Bencic 77, Tauson 41) and the
Challenger/ITF tail is empty (Oliynykova 1, Atmane 1, O'Connell 0). Every
profile therefore carries `matches_charted` and every consumer MUST gate on it.
`profile()` returns None below MIN_CHARTED rather than a thin, confident-looking
answer.

ISOLATION: nothing here raises. A network failure, a schema change, or an
unparseable row degrades to "no profile", which every caller must already handle
because most players have no profile at all. Tennis projections must be
unaffected by this module being unavailable — it is additive only.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE = ("https://raw.githubusercontent.com/JeffSackmann/"
        "tennis_MatchChartingProject/master/")
TIMEOUT = 90

# Charted matches a player needs before a profile is returned at all. The
# median player has ~2, so this deliberately excludes most of the field rather
# than serve a style read built on one match against one opponent — a single
# chart describes that matchup, not the player.
MIN_CHARTED = 8

# Refresh weekly. Matches are charted continuously but a player's style does not
# move week to week, and the payload is ~25 MB across five files.
_TTL = 7 * 24 * 3600
_CACHE: dict = {"ts": 0.0, "data": None}
_LOCK = threading.Lock()


def _norm(s: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFKD", s or "")
                if not unicodedata.combining(c))
    return re.sub(r"[^a-z ]", " ", s.lower()).strip()


def _get(name: str) -> Optional[str]:
    """Fetch one CSV as text. None on any failure.

    NOTE .text, never .raw — GitHub serves these gzipped and reading .raw
    returns compressed bytes that look exactly like a corrupt file.
    """
    try:
        r = requests.get(BASE + name, timeout=TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            logger.warning("[MCP] %s -> HTTP %d", name, r.status_code)
            return None
        return r.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MCP] %s fetch failed: %s", name, str(exc)[:160])
        return None


def _rows(text: str):
    if not text:
        return []
    try:
        return list(csv.DictReader(io.StringIO(text)))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MCP] parse failed: %s", str(exc)[:160])
        return []


def _f(row, key, default=0.0) -> float:
    try:
        v = row.get(key)
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _build() -> dict:
    """Aggregate every charted match into {norm_player: profile}.

    Totals are summed across matches and rates computed at the END, so a player
    is weighted by how much they have been charted rather than each match
    counting equally. A 3-shot cameo and a 250-shot final should not carry the
    same weight in a style read.
    """
    agg: dict = {}

    def slot(name):
        k = _norm(name)
        if not k:
            return None
        return agg.setdefault(k, {
            "name": name.strip(), "matches": set(),
            "ret_returnable": 0.0, "ret_deep": 0.0, "ret_very_deep": 0.0,
            "ret_shallow": 0.0,
            "snv_pts": 0.0, "snv_won": 0.0, "serve_pts": 0.0,
            "net_pts": 0.0, "net_won": 0.0,
            "fh": 0.0, "bh": 0.0,
            "shots": 0.0, "winners": 0.0, "induced": 0.0, "unforced": 0.0,
            "pt_ending": 0.0,
        })

    for tour_tag, tour in (("m", "ATP"), ("w", "WTA")):
        # Match index — the only place hands and the charted-match count live.
        for r in _rows(_get(f"charting-{tour_tag}-matches.csv")):
            mid = (r.get("match_id") or "").strip()
            for pk, hk in (("Player 1", "Pl 1 hand"), ("Player 2", "Pl 2 hand")):
                s = slot(r.get(pk) or "")
                if s is None:
                    continue
                s["matches"].add(mid)
                s["tour"] = tour
                if not s.get("hand"):
                    s["hand"] = (r.get(hk) or "").strip() or None

        for r in _rows(_get(f"charting-{tour_tag}-stats-ReturnDepth.csv")):
            if (r.get("row") or "").strip() != "Total":
                continue
            s = slot(r.get("player") or "")
            if s is None:
                continue
            # very_deep is a SUBSET of deep (verified: true in 100% of rows),
            # and shallow+deep <= returnable in 96% of rows while
            # shallow+deep+very_deep <= returnable in only 24% — so the depth
            # denominator is shallow+deep, and adding very_deep to it
            # double-counts. Doing that produced "111% deep returns".
            s["ret_deep"] += _f(r, "deep")
            s["ret_very_deep"] += _f(r, "very_deep")
            s["ret_shallow"] += _f(r, "shallow")

        # SnV rows are SnV / nonSnV / SnV1st / SnV2nd / nonSnV1st / nonSnV2nd.
        # The 1st/2nd rows are a BREAKDOWN of the first two, so summing every
        # row triple-counts — that is how Tommy Paul came out serve-volleying
        # 136 times a match. SnV and nonSnV together are all service points,
        # which is the denominator the rate needs.
        for r in _rows(_get(f"charting-{tour_tag}-stats-SnV.csv")):
            lab = (r.get("row") or "").strip()
            if lab not in ("SnV", "nonSnV"):
                continue
            s = slot(r.get("player") or "")
            if s is None:
                continue
            if lab == "SnV":
                s["snv_pts"] += _f(r, "snv_pts")
                s["snv_won"] += _f(r, "pts_won")
            s["serve_pts"] += _f(r, "snv_pts")

        for r in _rows(_get(f"charting-{tour_tag}-stats-NetPoints.csv")):
            # The label is "NetPoints"; Approach/*Rallies are separate cuts of
            # the same points. Matching on "net"/"total" matched nothing at all,
            # which is why every player reported 0.0 net points per match.
            if (r.get("row") or "").strip() != "NetPoints":
                continue
            s = slot(r.get("player") or "")
            if s is None:
                continue
            s["net_pts"] += _f(r, "net_pts")
            s["net_won"] += _f(r, "pts_won")

        # ShotTypes carries the forehand/backhand split in its `row` column:
        # Fgs / Bgs are groundstrokes, Total is everything. Using the GROUNDSTROKE
        # rows rather than F/B keeps serves, returns and volleys out of a number
        # that is meant to describe rally preference.
        for r in _rows(_get(f"charting-{tour_tag}-stats-ShotTypes.csv")):
            s = slot(r.get("player") or "")
            if s is None:
                continue
            lab = (r.get("row") or "").strip()
            if lab == "Fgs":
                s["fh"] += _f(r, "shots")
            elif lab == "Bgs":
                s["bh"] += _f(r, "shots")
            elif lab == "Total":
                s["shots"] += _f(r, "shots")
                s["winners"] += _f(r, "winners")
                s["induced"] += _f(r, "induced_forced")
                s["unforced"] += _f(r, "unforced")
                s["pt_ending"] += _f(r, "pt_ending")

    out = {}
    for k, s in agg.items():
        n = len(s["matches"])
        if not n:
            continue
        ret_n = s["ret_shallow"] + s["ret_deep"]
        prof = {
            "name": s["name"],
            "tour": s.get("tour"),
            "hand": s.get("hand"),
            "matches_charted": n,
            "return_deep_pct": (round(100.0 * s["ret_deep"] / ret_n, 1)
                                if ret_n >= 40 else None),
            "return_very_deep_pct": (round(100.0 * s["ret_very_deep"] / ret_n, 1)
                                     if ret_n >= 40 else None),
            "return_shallow_pct": (round(100.0 * s["ret_shallow"] / ret_n, 1)
                                   if ret_n >= 40 else None),
            "snv_pts": int(s["snv_pts"]),
            "snv_win_pct": (round(100.0 * s["snv_won"] / s["snv_pts"], 1)
                            if s["snv_pts"] >= 20 else None),
            "net_pts_per_match": round(s["net_pts"] / n, 1) if s["net_pts"] else 0.0,
            "net_win_pct": (round(100.0 * s["net_won"] / s["net_pts"], 1)
                            if s["net_pts"] >= 40 else None),
            "forehand_share_pct": (round(100.0 * s["fh"] / (s["fh"] + s["bh"]), 1)
                                   if (s["fh"] + s["bh"]) >= 200 else None),
            # Aggression = how often a shot ENDS the point in this player's
            # favour. Winners plus shots that force an error, over all shots.
            # A grinder sits low; a first-strike player sits high.
            "aggression_pct": (round(100.0 * (s["winners"] + s["induced"]) / s["shots"], 2)
                               if s["shots"] >= 500 else None),
            "unforced_pct": (round(100.0 * s["unforced"] / s["shots"], 2)
                             if s["shots"] >= 500 else None),
            "shots_per_match": round(s["shots"] / n, 0) if s["shots"] else None,
        }
        prof["snv_rate_per_match"] = round(s["snv_pts"] / n, 1)
        prof["snv_pct_of_serve_pts"] = (round(100.0 * s["snv_pts"] / s["serve_pts"], 2)
                                        if s["serve_pts"] >= 200 else None)
        out[k] = prof
    logger.info("[MCP] built playstyle profiles for %d players", len(out))
    return out


def _data() -> dict:
    """Cached {norm_name: profile}. Empty dict when unavailable — never raises."""
    now = time.time()
    if _CACHE["data"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["data"]
    with _LOCK:
        if _CACHE["data"] is not None and (time.time() - _CACHE["ts"]) < _TTL:
            return _CACHE["data"]
        try:
            d = _build()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MCP] build failed: %s", str(exc)[:200])
            d = _CACHE["data"] or {}
        if d:
            _CACHE["data"], _CACHE["ts"] = d, time.time()
        return d


def profile(player_name: str, min_charted: int = MIN_CHARTED) -> Optional[dict]:
    """Playstyle profile for one player, or None.

    None means "we do not know", and for most of the tour that is the honest
    answer — see the coverage note at the top. Callers must treat None as the
    normal case, not an error, and must never substitute a default profile: a
    neutral style read is a claim about the player, and we do not have one.
    """
    d = _data()
    if not d:
        return None
    key = _norm(player_name)
    p = d.get(key)
    if p is None:
        # Surname + fuzzy fallback: our names come from Sofascore, the charts
        # from volunteers, and they disagree on accents and given-name forms.
        best, score = None, 0.0
        last = key.split()[-1] if key else ""
        for k, v in d.items():
            if last and k.split()[-1:] == [last]:
                r = SequenceMatcher(None, key, k).ratio()
                if r > score:
                    best, score = v, r
        if score >= 0.80:
            p = best
    if p is None or p.get("matches_charted", 0) < min_charted:
        return None
    return p


def describe(p: dict) -> str:
    """One-line style read. Descriptive only — no projection meaning attached.

    EVERY CUTOFF BELOW IS A PERCENTILE OF THE CHARTED POPULATION, not a chosen
    number. Measured across the 516 players clearing the sample gate:

        metric                p25    p50    p75    p90
        return_deep_pct      74.2   76.9   80.0   82.6
        aggression_pct       10.2   11.6   13.7   15.6
        forehand_share_pct   49.2   51.7   54.2   56.5
        net_pts_per_match    11.5   15.8   21.7   32.0
        snv_pct_of_serve_pts  2.5    4.1    7.7   27.9

    My first pass invented the thresholds and called every single player
    "first-strike, deep returner" — the labels carried no information because
    the cutoffs sat below the whole distribution. A label only means something
    if it separates this player from the field.
    """
    if not p:
        return ""
    bits = []
    ag = p.get("aggression_pct")
    if ag is not None:
        bits.append("first-strike" if ag >= 15.6 else
                    "aggressive" if ag >= 13.7 else
                    "counterpuncher" if ag < 10.2 else "balanced")
    rd = p.get("return_deep_pct")
    if rd is not None:
        bits.append("deep returner" if rd >= 80.0 else
                    "short returner" if rd < 74.2 else "average return depth")
    snv = p.get("snv_pct_of_serve_pts")
    if snv is not None and snv >= 7.7:
        bits.append("serve-volleys" if snv < 27.9 else "heavy serve-volley")
    npm = p.get("net_pts_per_match")
    if npm is not None:
        if npm >= 21.7:
            bits.append("frequent net")
        elif npm < 11.5:
            bits.append("stays back")
    fh = p.get("forehand_share_pct")
    if fh is not None:
        bits.append("forehand-heavy" if fh >= 54.2 else
                    "backhand-heavy" if fh <= 49.2 else "even wings")
    uf = p.get("unforced_pct")
    if uf is not None and uf >= 12.5:
        bits.append("error-prone")
    return ", ".join(bits)
