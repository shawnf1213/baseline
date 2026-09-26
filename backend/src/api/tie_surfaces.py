"""Davis Cup tie surfaces, from Wikipedia's structured tie boxes.

WHY THIS EXISTS. Sofascore publishes no groundType for Davis Cup — verified on
the 2026 ties: the words "clay", "hard" and "grass" appear nowhere in the event
payload, not on the event, the tournament or the unique tournament. The surface
chain therefore fell through to keyword matching on "Davis Cup Single Matches",
which holds no surface word, and _infer_surface ends in a bare `return "Hard"`.
Every Davis Cup tie was priced on generic hard. On 2026-09-19 the ⭐ Pick of the
Day was Bergs v Rodionov in Vienna — a tie played on indoor CLAY — priced hard.

WHY NOT THE VENUE. Host nations install temporary surfaces: Austria laid clay
inside the Wiener Athletic Club for that tie. Sofascore also had the venue
wrong (it said Wiener Stadthalle). A venue lookup would have been confidently
wrong, which is worse than knowing nothing.

WHY NOT THE ITF's OWN API. It exists — itftennis.com/tennis/api/TieApi/GetTie,
with the tie's UUID visible in its daviscup.com URL — but it sits behind an
Incapsula bot challenge that returns a JavaScript interstitial rather than JSON.
Working around a bot challenge is not something we do.

WHY PER TIE, NOT PER HOST. A nation hosts more than one tie a year and picks a
different surface each time: in 2026 Austria played JPN away on indoor hard in
February and hosted BEL on indoor clay in September. In a single round the
seven ties ran four different surface/indoor combinations. The tie is the unit.

The wikitext is a template, not prose, so this is a parse rather than a scrape:

    {{DavisCupbox3sets
    |team1=AUT
    |team2=BEL
    |venue=Wiener Athletic Club, [[Vienna]], [[Austria]]
    |surface=Clay (i)

team1/team2 are alpha-3 codes, which is exactly what Sofascore's parent event
gives us for the two nations — so the join is on codes, not on names.
"""

import json
import logging
import re
import time
import urllib.request

logger = logging.getLogger(__name__)

# Wikipedia asks for a descriptive agent that identifies the caller.
_UA = "BaselineEV/1.0 (tennis projections; contact: support@baselineev.com)"
_API = ("https://en.wikipedia.org/w/api.php?action=parse&page=%s"
        "&prop=wikitext&format=json&formatversion=2")

# One Davis Cup year spans several pages and their titles are not uniform. Each
# is tried and a miss is not an error — a round that has not been written yet
# simply contributes nothing.
_PAGE_PATTERNS = (
    "%d_Davis_Cup_Qualifiers_first_round",
    "%d_Davis_Cup_Qualifiers_second_round",
    "%d_Davis_Cup_World_Group_I",
    "%d_Davis_Cup_World_Group_I_play-offs",
    "%d_Davis_Cup_World_Group_II",
    "%d_Davis_Cup_World_Group_II_play-offs",
    "%d_Davis_Cup_Finals",
    "%d_Davis_Cup",
)

_TIE_RE = re.compile(r"\{\{\s*DavisCupbox[^\n]*\n(.*?)\n\}\}", re.S)
_FIELD_RE = re.compile(r"^\|(\w+)=([^\n]*)$", re.M)

_TTL = 12 * 3600          # a tie's surface does not change during a round
_cache: dict = {}         # year -> {(team1, team2): {...}}
_cache_at: dict = {}      # year -> unix seconds


def _normalise(raw: str):
    """'Clay (i)' -> ('Clay', True). Unknown text -> (None, False)."""
    s = (raw or "").lower()
    indoor = "(i)" in s or "indoor" in s
    if "clay" in s:
        return "Clay", indoor
    if "grass" in s:
        return "Grass", indoor
    if "hard" in s or "carpet" in s:
        return "Hard", indoor
    return None, indoor


def _fetch(page: str) -> str:
    req = urllib.request.Request(_API % page, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    return (d.get("parse") or {}).get("wikitext") or ""


def _parse(wikitext: str) -> dict:
    out = {}
    for m in _TIE_RE.finditer(wikitext):
        f = dict(_FIELD_RE.findall(m.group(1)))
        t1, t2 = (f.get("team1") or "").strip(), (f.get("team2") or "").strip()
        surface, indoor = _normalise(f.get("surface", ""))
        if not (t1 and t2 and surface):
            continue
        out[(t1.upper(), t2.upper())] = {
            "surface": surface,
            "indoor": indoor,
            "raw": (f.get("surface") or "").strip(),
            "venue": re.sub(r"\[\[|\]\]|<ref.*|\{\{.*?\}\}", "",
                            f.get("venue", "")).strip(),
        }
    return out


def load_year(year: int) -> dict:
    """{(team1, team2): {surface, indoor, raw, venue}} for one Davis Cup year.

    Cached for _TTL. Never raises: a failed fetch returns whatever is already
    cached, or an empty map, and the caller falls back as it did before.
    """
    now = time.time()
    if year in _cache and (now - _cache_at.get(year, 0)) < _TTL:
        return _cache[year]
    ties = {}
    for pat in _PAGE_PATTERNS:
        page = pat % year
        try:
            wt = _fetch(page)
        except Exception as exc:  # noqa: BLE001 — a missing page is normal
            logger.debug("tie surfaces: %s unavailable (%s)", page, exc)
            continue
        if not wt:
            continue
        found = _parse(wt)
        if found:
            logger.info("tie surfaces: %s -> %d tie(s)", page, len(found))
            ties.update(found)
    if ties:
        _cache[year] = ties
        _cache_at[year] = now
        return ties
    # Keep a previous good answer rather than replacing it with nothing.
    return _cache.get(year, {})


def lookup(home: str, away: str, year):
    """Surface for one tie, by alpha-3 nation codes. None when not found.

    Tries home/away as given first — the HOST picks the surface, so the pair is
    ordered — then the reverse, because Wikipedia occasionally lists a tie from
    the other side and a reversed hit is still the same tie at the same venue.
    """
    try:
        year = int(year)
    except (TypeError, ValueError):
        return None
    if not home or not away:
        return None
    ties = load_year(year)
    hit = ties.get((home.upper(), away.upper())) or ties.get((away.upper(), home.upper()))
    return hit
