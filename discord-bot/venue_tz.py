"""Where a tournament actually is, so a card can be dated in its own time.

WHY THIS EXISTS. A tournament DAY is defined in the venue's local time — that is
what "day 3 of the draw" means — but the board was grouping matches by their ET
calendar date. Those are the same thing only for events in the Americas. The
Singapore WTA card of 2026-09-21 opened at 23:00 ET on the 20th and finished at
08:00 ET on the 21st: one lineup, two ET dates, and the opener was being dropped
from the board that listed the rest of it.

The first fix was a constant — treat anything after 21:00 ET as tomorrow's card.
That worked for Singapore and Seoul and would have been wrong by January:
Melbourne opens around 19:00 ET the night before, and lowering the cutoff far
enough to catch it starts sweeping in US night sessions. A number tuned to one
swing has to be retuned for the next one, which is exactly the thing that should
not need doing.

So the zone is DERIVED instead. Sofascore names tennis events after their venue
— "Singapore, Singapore", "Seoul, Korea Republic", "Rome, Italy", "US Open, New
York, USA" — and the IANA database is already a list of the world's cities. Match
the city against it and the venue's clock falls out, with a country table behind
it for cities IANA does not name in its own right (Wuhan, Cincinnati, Eastbourne).

Nothing here needs touching when the tour moves.
"""

import logging
import re
import zoneinfo

log = logging.getLogger("baseline.venue_tz")

# Representative zone per country, for venues IANA has no city entry for. Only
# has to be right to the DAY: a card runs from late morning to late evening
# local, so being an hour or two off inside a country never moves its date. The
# wide ones (USA, Canada, Australia, Brazil) take the zone most of their tennis
# is played in.
_COUNTRY = {
    "usa": "America/New_York", "united states": "America/New_York",
    "canada": "America/Toronto", "mexico": "America/Mexico_City",
    "brazil": "America/Sao_Paulo", "argentina": "America/Argentina/Buenos_Aires",
    "colombia": "America/Bogota", "chile": "America/Santiago",
    "peru": "America/Lima", "ecuador": "America/Guayaquil",
    "great britain": "Europe/London", "united kingdom": "Europe/London",
    "england": "Europe/London", "ireland": "Europe/Dublin",
    "france": "Europe/Paris", "spain": "Europe/Madrid",
    "portugal": "Europe/Lisbon", "italy": "Europe/Rome",
    "germany": "Europe/Berlin", "austria": "Europe/Vienna",
    "switzerland": "Europe/Zurich", "netherlands": "Europe/Amsterdam",
    "belgium": "Europe/Brussels", "denmark": "Europe/Copenhagen",
    "sweden": "Europe/Stockholm", "norway": "Europe/Oslo",
    "finland": "Europe/Helsinki", "poland": "Europe/Warsaw",
    "czech republic": "Europe/Prague", "czechia": "Europe/Prague",
    "slovakia": "Europe/Bratislava", "slovenia": "Europe/Ljubljana",
    "croatia": "Europe/Zagreb", "serbia": "Europe/Belgrade",
    "hungary": "Europe/Budapest", "romania": "Europe/Bucharest",
    "bulgaria": "Europe/Sofia", "greece": "Europe/Athens",
    "turkiye": "Europe/Istanbul", "turkey": "Europe/Istanbul",
    "russia": "Europe/Moscow", "ukraine": "Europe/Kyiv",
    "estonia": "Europe/Tallinn", "latvia": "Europe/Riga",
    "lithuania": "Europe/Vilnius", "luxembourg": "Europe/Luxembourg",
    "israel": "Asia/Jerusalem", "qatar": "Asia/Qatar",
    "united arab emirates": "Asia/Dubai", "uae": "Asia/Dubai",
    "saudi arabia": "Asia/Riyadh", "bahrain": "Asia/Bahrain",
    "india": "Asia/Kolkata", "china": "Asia/Shanghai",
    "japan": "Asia/Tokyo", "korea republic": "Asia/Seoul",
    "south korea": "Asia/Seoul", "singapore": "Asia/Singapore",
    "thailand": "Asia/Bangkok", "vietnam": "Asia/Ho_Chi_Minh",
    "indonesia": "Asia/Jakarta", "malaysia": "Asia/Kuala_Lumpur",
    "hong kong": "Asia/Hong_Kong", "taiwan": "Asia/Taipei",
    "kazakhstan": "Asia/Almaty", "uzbekistan": "Asia/Tashkent",
    "australia": "Australia/Melbourne", "new zealand": "Pacific/Auckland",
    "south africa": "Africa/Johannesburg", "morocco": "Africa/Casablanca",
    "egypt": "Africa/Cairo", "tunisia": "Africa/Tunis",
}

# Cities whose tennis is played well away from their country's representative
# zone. Only the ones where being wrong would move a DATE, not merely an hour.
_CITY = {
    "indian wells": "America/Los_Angeles", "los angeles": "America/Los_Angeles",
    "san diego": "America/Los_Angeles", "tiburon": "America/Los_Angeles",
    "stanford": "America/Los_Angeles", "san jose": "America/Los_Angeles",
    "las vegas": "America/Los_Angeles", "scottsdale": "America/Phoenix",
    "perth": "Australia/Perth", "brisbane": "Australia/Brisbane",
    # Named without a country in the feed ("Washington"), and IANA has no leaf
    # for them, so neither lookup above can reach them.
    "washington": "America/New_York", "winston salem": "America/New_York",
    "newport": "America/New_York", "delray beach": "America/New_York",
    "atlanta": "America/New_York", "dallas": "America/Chicago",
    "houston": "America/Chicago", "austin": "America/Chicago",
}


def _iana_index():
    """{lowercased city -> zone} taken from the IANA zone names themselves."""
    idx = {}
    for z in zoneinfo.available_timezones():
        leaf = z.rsplit("/", 1)[-1].replace("_", " ").lower()
        # First writer wins so Europe/Rome beats a later exotic match.
        idx.setdefault(leaf, z)
    return idx


_IANA = _iana_index()
_MISSES = set()          # logged once each, so a new venue is visible not silent


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+\d+$", "", s)        # "Antalya 4" -> "antalya"
    return re.sub(r"\s+", " ", s)


def parse(tournament: str):
    """(city, country) from a Sofascore tennis tournament name.

    Handles every shape the record actually contains:
        "Rome, Italy"                     -> ("rome", "italy")
        "Hamburg, Germany, Qualifying"    -> ("hamburg", "germany")
        "US Open, New York, USA"          -> ("new york", "usa")
        "Washington"                      -> ("washington", "")
        "Davis Cup Single Matches"        -> ("davis cup single matches", "")
    """
    parts = [p.strip() for p in (tournament or "").split(",") if p.strip()]
    # The qualifying draw plays at the same venue as the main draw.
    parts = [p for p in parts if _norm(p) not in ("qualifying", "qualification")]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return _norm(parts[0]), ""
    return _norm(parts[-2]), _norm(parts[-1])


def zone_for(tournament: str):
    """ZoneInfo for this tournament's venue, or None if it cannot be resolved.

    None is deliberate rather than a silent fall back to ET — the caller decides
    what to do without a venue clock, and the miss is logged once so a venue we
    do not know about surfaces instead of quietly reverting to the old
    ET-calendar behaviour.
    """
    city, country = parse(tournament)
    for key, table in ((city, _CITY), (city, _IANA), (country, _COUNTRY)):
        z = table.get(key) if key else None
        if z:
            try:
                return zoneinfo.ZoneInfo(z)
            except Exception:  # noqa: BLE001 — a bad zone must not stop a board
                pass
    if tournament and tournament not in _MISSES:
        _MISSES.add(tournament)
        log.warning("venue_tz: no timezone for %r (city=%r country=%r) — its "
                    "card date will fall back to ET", tournament, city, country)
    return None
