"""Tokens for lexical memory search: lowercase words, plus overlapping 2-grams over CJK runs."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable

WORD = re.compile(r"[^\W_㐀-鿿]+")
CJK_RUN = re.compile(r"[㐀-鿿]+")

STOPWORDS = frozenset(
    "a an and are as at be but by for from has have i in is it its me my of on or so that the this to was were "
    "will with you your he she they them we our us do does did not no".split()
)


def tokens(text: str) -> list[str]:
    out = [w.lower() for w in WORD.findall(text)]
    for run in CJK_RUN.findall(text):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    return out


def token_set(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in STOPWORDS}


def idf(word: str, df: Counter, n_docs: int) -> float:
    """IDF(w) = ln(1 + (N − n(w) + 0.5) / (n(w) + 0.5))"""
    n = df.get(word, 0)
    return math.log(1 + (n_docs - n + 0.5) / (n + 0.5))


def document_frequency(docs: Iterable[set[str]]) -> tuple[Counter, int]:
    df: Counter = Counter()
    n = 0
    for doc in docs:
        df.update(doc)
        n += 1
    return df, n


def score(query: set[str], doc: set[str], df: Counter, n_docs: int) -> float:
    """Score(Q, M) = Σ IDF(w) over w ∈ tokens(Q) ∩ tokens(M)"""
    return sum(idf(w, df, n_docs) for w in query & doc)


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)
