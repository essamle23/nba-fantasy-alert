"""Last check before anything is posted: never claim a player is available on inference
alone when another source still has him out.

"Inferred" AVAILABLE alerts come from a player *disappearing* from a list (ESPN injury
list, official report). Disappearance can be a data artifact — ESPN re-issuing a record,
a partial response — so those are cross-checked. Explicit news ("Available to play")
is never blocked here.
"""
from __future__ import annotations

import logging

from .models import Alert, normalize_name
from .state import State

log = logging.getLogger(__name__)
NEGATIVE = {"OUT", "DOUBTFUL", "QUESTIONABLE"}
STILL_OUT = {"Out", "Doubtful", "Suspension"}


def known_out(state: State) -> dict[str, str]:
    """normalized name -> 'Out (ESPN)' etc., from every source's confirmed state."""
    out: dict[str, str] = {}
    for p in state.data.get("espn", {}).get("players", {}).values():
        if p.get("status") in STILL_OUT:
            out[normalize_name(p["player"])] = f"{p['status']} on ESPN"
    for name, p in state.data.get("nba_report", {}).get("upcoming", {}).items():
        if p.get("status") in STILL_OUT:
            out.setdefault(normalize_name(name), f"{p['status']} on the official report")
    return out


def check(state: State, alerts: list[Alert]) -> list[Alert]:
    negative_now = {normalize_name(a.player) for a in alerts if a.category in NEGATIVE}
    still_out = None
    kept = []
    for a in alerts:
        if a.category == "AVAILABLE" and a.extra.get("inferred"):
            key = normalize_name(a.player)
            if key in negative_now:
                log.warning("guard: dropped inferred AVAILABLE for %s — same batch says otherwise", a.player)
                continue
            if still_out is None:
                still_out = known_out(state)
            if key in still_out:
                log.warning("guard: dropped inferred AVAILABLE for %s (%s) — still %s",
                            a.player, a.source, still_out[key])
                continue
        kept.append(a)
    return kept
