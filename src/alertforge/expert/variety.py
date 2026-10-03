"""Balanced choice of text variants across a build (R2).

A text that every task carries (README paragraphs, ADRs, the handover, runbook styles) picks its variant from a
fixed shuffle of the 45 slots of its block (5 families x 9 seeds), so each of k variants is used by 45/k tasks.
A text that belongs to one card (a ticket, a thread, a postmortem narrative) does the same over the tasks of the
block whose composition carries that card. Either way a rebuild of one task picks what the full build picked: the
choice depends on the task's family, seed and master seed only.

Never call `house` or `card` while a world is being composed (`families.build`): `card` composes the block.
"""

from __future__ import annotations

import hashlib
import random
from functools import lru_cache

FAMILIES = ["E1", "E2", "E3", "E4", "E5"]
SURVEY_CODES = {"H06", "H08", "H09", "H10", "H23", "R10", "R11", "R12", "R13", "R18"}


def _master(w) -> int:
    return int(w.notes.get("master", 20260930))


def slot(w) -> tuple[int, int]:
    """(block, index of this task within its block of 45)."""
    return (w.seed - 1) // 9, FAMILIES.index(w.family) * 9 + (w.seed - 1) % 9


def _balanced(key: str, n: int, k: int, master: int, block: int) -> list[int]:
    seq = [i % k for i in range(n)]
    random.Random(f"{master}:{block}:{key}").shuffle(seq)
    return seq


def house(w, key: str, options: list):
    """The variant of a text every task carries."""
    block, i = slot(w)
    return options[_balanced(key, 45, len(options), _master(w), block)[i]]


def item_keys(w, it) -> set[str]:
    """The text an item's prose is filed under: its code (R01 by tier), or `survey:<code>` where the item is an
    answer in E5's on-call survey rather than a queue ticket."""
    code = it.code + (getattr(it, "tier", "")[:1] if it.code == "R01" else "")
    if w.family == "E5" and it.code in SURVEY_CODES:
        return {f"survey:{it.code}"}
    return {code}


def base_key(key: str) -> str:
    """`H04:pm` is one of H04's texts, `survey:H08:x` one of survey:H08's: balanced over the tasks carrying the
    card, each text with its own shuffle."""
    parts = key.split(":")
    return ":".join(parts[:2]) if parts[0] in ("survey", "any") else parts[0]


def text_keys(w, items) -> set[str]:
    out = set()
    for it in items:
        out |= item_keys(w, it) | {f"any:{it.code}"}   # `any:` texts (runbooks) are the same in every family
    if (w.trigger or {}).get("moved"):
        out.add("split")
    if w.family == "E5":
        out.add("survey")
        trng = random.Random(w.tseed ^ 0x7E47)    # the same draw build_task makes for the kept complaint
        if trng.random() < 0.7:
            from .items_triage import Kept
            if Kept(w, trng).ok:
                out.add("kept")
    return out


@lru_cache(maxsize=None)
def _block_keys(master: int, block: int) -> tuple:
    from . import families
    out = []
    for f in FAMILIES:
        for s in range(block * 9 + 1, block * 9 + 10):
            try:
                w, items = families.build(f, s, master)
            except RuntimeError:
                continue
            out.append(((f, s), frozenset(text_keys(w, items))))
    return tuple(out)


def carriers(w, key: str) -> list[tuple[str, int]]:
    block, _ = slot(w)
    return [fs for fs, keys in _block_keys(_master(w), block) if key in keys]


def card(w, key: str, options: list):
    """The variant of a card's text for this task, balanced over the block's tasks that carry the card."""
    who = carriers(w, base_key(key))
    me = (w.family, w.seed)
    if me not in who:   # a world composed outside the standard block (a test's own world): stable fallback
        h = int(hashlib.sha256(f"{_master(w)}:{key}:{w.family}:{w.seed}".encode()).hexdigest()[:8], 16)
        return options[h % len(options)]
    return options[_balanced(key, len(who), len(options), _master(w), slot(w)[0])[who.index(me)]]


def rank(w, key: str) -> tuple[int, int]:
    """(this task's position among the block's tasks carrying `key`, how many there are); (hash, 1) outside it."""
    who = carriers(w, base_key(key))
    me = (w.family, w.seed)
    if me not in who:
        return int(hashlib.sha256(f"{key}:{w.family}:{w.seed}".encode()).hexdigest()[:8], 16), 1
    order = _balanced(key, len(who), len(who), _master(w), slot(w)[0])
    return order[who.index(me)], len(who)


def index(w, key: str, k: int, house_wide: bool = False) -> int:
    """The variant number (0..k-1) `card`/`house` would pick."""
    return (house if house_wide else card)(w, key, list(range(k)))
