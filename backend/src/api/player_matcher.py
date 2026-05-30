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

_DEFAULT_THRESHOLD = 0.85


def _normalize_name(s: str) -> str:
    """Lowercase, strip diacritics-friendly chars, collapse whitespace."""
    if not s:
        return ""
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
    Generate search query variants. PrizePicks names like 'C. Alcaraz' need
    to be reduced to just the last name to surface Sofascore matches.
    """
    norm = _normalize_name(name)
    parts = norm.split()
    variants: list = []
    if parts:
        # Last name alone (most reliable for surname-prefixed formats)
        variants.append(parts[-1])
        # Full normalized form
        if len(parts) > 1:
            variants.append(norm)
        # 'Lastname, F.' format → swap
        if "," in name:
            swapped = " ".join(reversed(name.split(",")))
            variants.append(_normalize_name(swapped))
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
