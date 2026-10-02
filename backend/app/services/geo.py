"""Pure clinic-ranking helpers (no DB, no network, no geocoding API).

MVP scope: we have no user coordinates and no external geocoder, so
"nearest clinic" means best textual match of the user's city/address
against clinic city/address, with known RU/EN/PL city aliases. Clinics
carrying coordinates sort before ones without (they are placeable on a
map later). Deterministic and unit-tested; the DB layer stays untouched.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol


class _GeoClinic(Protocol):
    city: str | None
    address: str | None
    latitude: float | None
    longitude: float | None


# Canonical city -> known spellings (lowercase, ё already folded to е).
_CITY_ALIASES: dict[str, frozenset[str]] = {
    "warszawa": frozenset(
        {
            "варшава",
            "варшавы",
            "варшаве",
            "варшаву",
            "варшавой",
            "warszawa",
            "warsaw",
            "warschau",
        }
    ),
    "lisboa": frozenset(
        {
            "лиссабон",
            "лиссабона",
            "лиссабоне",
            "лисабон",
            "lisboa",
            "lisbon",
            "lisbona",
            "lissabon",
        }
    ),
}

_TOKEN_RE = re.compile(r"[a-zа-я0-9]+", re.IGNORECASE)


def normalize(text: str) -> str:
    """Lowercase, fold ё→е, strip accents for comparison."""
    return unicodedata.normalize("NFKC", text.strip().lower()).replace("ё", "е")


def canonical_city(query: str) -> str | None:
    """Map a free-form address query to a known canonical city, if any."""
    q = normalize(query)
    for canonical, aliases in _CITY_ALIASES.items():
        if canonical in q or any(alias in q for alias in aliases):
            return canonical
    return None


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(normalize(text)) if len(t) >= 4}


def _clinic_city_key(clinic: _GeoClinic) -> str | None:
    if clinic.city is None:
        return None
    c = normalize(clinic.city)
    if c in _CITY_ALIASES:
        return c
    for canonical, aliases in _CITY_ALIASES.items():
        if c in aliases:
            return canonical
    return c


def rank_clinics(
    clinics: Sequence[_GeoClinic], query: str | None
) -> tuple[list[_GeoClinic], str | None]:
    """Sort clinics by relevance to the user's address query.

    Returns (ranked, matched_city). Empty query → original order, None city.
    Scoring: city match (+100) dominates; shared address tokens (+10 each)
    break ties between same-city clinics; geocoded rows (+1) go first when
    nothing else matches. Stable: original order breaks remaining ties.
    """
    if query is None or not query.strip():
        return list(clinics), None
    matched = canonical_city(query)
    query_tokens = _tokens(query)
    scored: list[tuple[int, int, _GeoClinic]] = []
    for index, clinic in enumerate(clinics):
        score = 0
        if matched is not None and _clinic_city_key(clinic) == matched:
            score += 100
        haystack = f"{clinic.city or ''} {clinic.address or ''}"
        overlap = query_tokens & _tokens(haystack)
        # City token itself already counted via the +100 above; only
        # street-level tokens add signal here.
        score += 10 * len(overlap)
        if clinic.latitude is not None and clinic.longitude is not None:
            score += 1
        scored.append((score, index, clinic))
    if all(score == scored[0][0] for score, _, _ in scored):
        return list(clinics), matched
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [clinic for _, _, clinic in scored], matched
