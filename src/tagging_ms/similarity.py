from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

_NON_ALNUM_RE = re.compile(r"[\W_]+", re.UNICODE)
_WORD_SPLIT_RE = re.compile(r"\W+", re.UNICODE)


def strip_non_alnum(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value)


def normalize(value: str) -> str:
    normalized = strip_non_alnum(value.lower())
    return normalized or value.lower()


def word_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()


def similarity2(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    alist = [token for token in _WORD_SPLIT_RE.split(a.lower()) if token]
    blist = [token for token in _WORD_SPLIT_RE.split(b.lower()) if token]
    if not alist or not blist:
        return 0.0
    if len(alist) > len(blist):
        alist, blist = blist, alist

    score = 0.0
    remaining = list(blist)
    for av in alist:
        best_score = 0.0
        best_index: int | None = None
        for index, bv in enumerate(remaining):
            candidate_score = word_similarity(av, bv)
            if candidate_score > best_score:
                best_score = candidate_score
                best_index = index
        if best_index is not None:
            score += best_score
            if best_score > 0.6:
                del remaining[best_index]

    return score / (len(alist) + len(remaining) * 0.4)


def linear_combination_of_weights(parts: list[tuple[float, int]]) -> float:
    total_weight = sum(weight for _, weight in parts)
    if not total_weight:
        return 0.0
    return sum(score * weight for score, weight in parts) / total_weight


def extract_year_from_date(value: str | object) -> int | None:
    if not value:
        return None
    if isinstance(value, dict):
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    if not value:
        return None

    for fmt in ("%Y", "%Y-%m", "%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).year
        except ValueError:
            continue

    if len(value) >= 4 and value[:4].isdigit():
        return int(value[:4])
    return None


def trackcount_score(actual: int, expected: int) -> float:
    return 0.0 if actual > expected else 0.3 if actual < expected else 1.0


def length_score(actual_ms: int, expected_ms: int) -> float:
    threshold_ms = 30000
    if not actual_ms or not expected_ms:
        return 0.0
    return 1.0 - min(abs(actual_ms - expected_ms), threshold_ms) / float(threshold_ms)
