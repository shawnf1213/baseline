"""
Fuzzy match PrizePicks-format player names ("C. Alcaraz", "Alcaraz, C.") to
Sofascore player IDs using stdlib SequenceMatcher (no new deps).
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from src.api.matchstat_client import search_players

logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 0.80


def _to_first_last(name: str) -> str:
    """
    Normalize a PrizePicks-format name to 'First Last' as best we can.

    Handles all four formats the user listed:
      "Carlos Alcaraz"    →  "Carlos Alcaraz"
      "C. Alcaraz"        →  "C. Alcaraz"
      "ALCARAZ C."        →  "C. Alcaraz"        (lastname uppercase first)
      "Alcaraz, C."       →  "C. Alcaraz"        (comma swap)
      "Alcaraz, Carlos"   →  "Carlos Alcaraz"
    Anything we can't classify is returned unchanged.
    """
    if not name:
        return ""
    s = str(name).strip()

    # "Lastname, Firstname" / "Lastname, F."
    if "," in s:
        lhs, _, rhs = s.partition(",")
        lhs, rhs = lhs.strip(), rhs.strip()
        if lhs and rhs:
            s = f"{rhs} {lhs}"

    tokens = s.split()

    # "LASTNAME F." or "DE MINAUR A." — one or more uppercase surname tokens
    # followed by a 1-2 char initial. Swap to "F. Lastname Lastname".
    if (len(tokens) >= 2
            and all(t.isupper() and len(t) >= 2 for t in tokens[:-1])
            and len(tokens[-1].rstrip(".")) <= 2):
        initial = tokens[-1] if tokens[-1].endswith(".") else f"{tokens[-1]}."
        lastname = " ".join(t.title() for t in tokens[:-1])
        s = f"{initial} {lastname}"

    return s.strip()


def _normalize_name(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Used for similarity."""
    if not s:
        return ""
    s = _to_first_last(s)
    s = str(s).strip().lower()
    s = re.sub(r"[.,'`]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _last_name(s: str) -> str:
    parts = _normalize_name(s).split()
    return parts[-1] if parts else ""


def _name_similarity(query: str, candidate: str) -> float:
    """
    Hybrid similarity score:
      0.40 weight  → full-name ratio
      0.60 weight  → last-name ratio  (last name carries the most info)
    Returns 0.0–1.0.
    """
    q_full = _normalize_name(query)
    c_full = _normalize_name(candidate)
    if not q_full or not c_full:
        return 0.0

    full_ratio = SequenceMatcher(None, q_full, c_full).ratio()
    last_ratio = SequenceMatcher(None, _last_name(query), _last_name(candidate)).ratio()
    return 0.40 * full_ratio + 0.60 * last_ratio


def _query_variants(name: str) -> list:
    """
    Generate search query variants. PrizePicks names come in several formats
    ("C. Alcaraz", "Carlos Alcaraz", "ALCARAZ C.", "Alcaraz, C.") — we
    normalize first then try multiple substrings so the Sofascore search
    surfaces the right player.
    """
    norm = _normalize_name(name)     # e.g. "c alcaraz" or "carlos alcaraz"
    parts = norm.split()
    variants: list = []
    if parts:
        # Last name alone — most reliable for any abbreviated-first-name format
        variants.append(parts[-1])
        # Full normalized form
        if len(parts) > 1:
            variants.append(norm)
        # Last two words (handles long names like "del potro" or "de minaur")
        if len(parts) >= 3:
            variants.append(" ".join(parts[-2:]))
    # Dedupe preserving order
    seen, out = set(), []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def match_player(
    pp_name: str,
    tour: str = "ATP",
    threshold: float = _DEFAULT_THRESHOLD,
) -> Optional[dict]:
    """
    Best-effort match of a PrizePicks-format name to a Sofascore player.
    Returns the player dict {id, name, currentRank, countryAcr, ...} or None.
    Logs unmatched names so they can be reviewed.
    """
    if not pp_name:
        return None

    variants = _query_variants(pp_name)
    if not variants:
        return None

    best_player, best_score = None, 0.0
    for q in variants:
        try:
            candidates = search_players(q, tour=tour) or []
        except Exception as exc:
            logger.debug("[NameMatch] search_players(%r) failed: %s", q, exc)
            continue
        for cand in candidates:
            cand_name = cand.get("name") or ""
            score = _name_similarity(pp_name, cand_name)
            if score > best_score:
                best_score, best_player = score, cand
        if best_score >= 0.97:
            break   # near-perfect match, stop searching

    if best_player and best_score >= threshold:
        logger.info("[NameMatch] %r → %r (score=%.2f, id=%s)",
                    pp_name, best_player.get("name"), best_score, best_player.get("id"))
        return best_player

    logger.warning("[NameMatch] no match for %r (best=%r, score=%.2f)",
                   pp_name, best_player.get("name") if best_player else None, best_score)
    return None
