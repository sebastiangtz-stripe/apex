#!/usr/bin/env python3
from __future__ import annotations
"""
Shared merchant/project name-normalization + fuzzy-match helpers.

Single source of truth for the name-matching logic used by handover-search.py
(backfill manifest) and handover-match.py (scanner roster classifier). Keeping
these in one module avoids the drift that comes from copy-pasting the regexes
into each script.
"""

import re
import unicodedata

NOISE_PATTERN = re.compile(r"[\-\s]*[\[\#\(].*$")
TRAILING_NOISE = re.compile(
    r"[\-\s]+(US|AMER|EMEA|APAC|LATAM|"
    r"\$\d+[KkMm]?|"
    r"\d+[KkMm])"
    r"$",
    re.IGNORECASE,
)

NAME_STOPWORDS = {
    "the", "inc", "llc", "corp", "co", "ltd", "gmbh", "sa", "us", "usa", "uk",
    "payments", "payment", "billing", "connect", "terminal", "standard",
}


def slugify(name: str) -> str:
    """Merchant name -> kebab-case folder slug."""
    s = name.lower().strip()
    s = re.sub(r"[\[\](){}|,;:+/\\.]", " ", s)
    s = re.sub(r"[-–—]", " ", s)
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "-", s).strip("-")
    return s or "unnamed-merchant"


def strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def clean_project_name(raw_name: str) -> str:
    """Strip bracket/noise suffixes from a Hubble project_name for search."""
    name = raw_name.strip()
    name = NOISE_PATTERN.sub("", name)
    name = TRAILING_NOISE.sub("", name)
    return name.strip(" -")


def norm_name(text: str) -> str:
    if not text:
        return ""
    s = text.lower()
    s = re.sub(r"[\[\](){}\|,;:\+\/\\]", " ", s)
    s = re.sub(r"[\-–—]", " ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    tokens = [t for t in s.split() if t and t not in NAME_STOPWORDS and len(t) > 1]
    return " ".join(tokens)


def name_similarity(a: str, b: str) -> float:
    a_tokens = set(norm_name(a).split())
    b_tokens = set(norm_name(b).split())
    if not a_tokens or not b_tokens:
        return 0.0
    overlap = a_tokens & b_tokens
    return len(overlap) / min(len(a_tokens), len(b_tokens))
