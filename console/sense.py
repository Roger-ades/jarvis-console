"""Local sense of a passage: hashed words plus a few synonym groups. No model, nothing leaves the PC.

Full-text search still wins when the words are in the document. This is the fallback for a paraphrase
("mettre fin au bail" against "dénonciation"). It is not a neural embedding: names and numbers stay
with the word index.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata

DIM = 256
_WORD = re.compile(r"[a-z0-9]{3,}")
# Folded words. A group is added to the vector when the text already uses one of its words.
_GROUPS = (
    {"resiliation", "resilier", "denonciation", "denoncer", "rompre", "resilie"},
    {"bail", "location", "loyer", "locataire"},
    {"decision", "decide", "decider", "retenir", "garde", "garder", "choix", "tranche"},
    {"devis", "offre", "proposition", "chiffrage", "estimation"},
    {"facture", "paiement", "regler", "reglement", "impaye"},
    {"relance", "rappel", "relancer"},
    {"vlan", "reseau", "switch"},
)
# Both words in the query pull in the words a document would use instead.
_PHRASES = (
    ({"mettre", "fin"}, {"resiliation", "denonciation", "resilier", "denoncer"}),
)


def fold(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or "").lower())
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def words(text: str) -> set[str]:
    return set(_WORD.findall(fold(text)))


def expand(tokens: set[str]) -> set[str]:
    extra = set()
    for group in _GROUPS:
        if tokens & group:
            extra |= group
    for need, add in _PHRASES:
        if need <= tokens:
            extra |= add
    return tokens | extra


def _bucket(token: str) -> tuple[int, int]:
    digest = hashlib.blake2s(token.encode("utf-8"), digest_size=8).digest()
    sign = 1 if digest[4] & 1 else -1
    return int.from_bytes(digest[:4], "little") % DIM, sign


def embed(text: str) -> bytes:
    """256 signed weights, packed. An empty text is 256 zero bytes."""
    vec = [0.0] * DIM
    for token in expand(words(text)):
        i, sign = _bucket(token)
        vec[i] += sign
        if len(token) >= 5:
            for k in range(len(token) - 2):
                j, s2 = _bucket("#" + token[k:k + 3])
                vec[j] += s2 * 0.35
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    out = bytearray(DIM)
    for i, v in enumerate(vec):
        n = int(round(v / norm * 100))
        out[i] = max(-127, min(127, n)) & 0xFF
    return bytes(out)


def cosine(a: bytes, b: bytes) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0
    for x, y in zip(a, b):
        x = x - 256 if x > 127 else x
        y = y - 256 if y > 127 else y
        dot += x * y
        na += x * x
        nb += y * y
    if not na or not nb:
        return 0.0
    return dot / math.sqrt(na * nb)


def useful(vec: bytes) -> bool:
    return any(vec)
