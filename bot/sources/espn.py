"""ESPN league-wide injury list (unofficial JSON). Alerts on status changes only —
comments get rewritten daily and would be noise."""
from __future__ import annotations

import logging
import re

import requests

from ..classify import from_status
from ..models import Alert
from ..state import State

log = logging.getLogger(__name__)

NAME = "espn"
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


def parse(data: dict) -> dict[str, dict]:
    out = {}
    for team in data.get("injuries", []):
        for inj in team.get("injuries", []):
            ath = inj.get("athlete", {})
            link = next((l["href"] for l in ath.get("links", [])
                         if "playercard" in l.get("rel", [])), "")
            # Key by athlete, not injury record: ESPN issues a new injury id on every
            # update, which looked like "removed" + "added" for the same player.
            m = re.search(r"/id/(\d+)", link)
            pid = m.group(1) if m else ath.get("displayName", "?")
            out[pid] = {
                "player": ath.get("displayName", "?"),
                "team": team.get("displayName", ""),
                "status": inj.get("status", ""),
                "comment": inj.get("shortComment", ""),
                "date": inj.get("date", ""),
                "url": link,
            }
    return out


def poll(state: State) -> list[Alert]:
    r = requests.get(URL, timeout=15)
    r.raise_for_status()
    current = parse(r.json())
    sec = state.section(NAME)
    if sec.get("v") != 2:  # state keyed by the old injury ids: reseed silently
        sec.clear()
        sec["v"] = 2
    prev = sec.get("players", {})
    seeded = state.is_seeded(NAME)
    missing = sec.setdefault("missing", {})  # pid -> consecutive polls absent

    if seeded and prev and len(current) < 0.7 * len(prev):
        log.warning("espn list shrank %d -> %d; ignoring this poll", len(prev), len(current))
        return []

    alerts = []
    if seeded:
        for pid, p in current.items():
            missing.pop(pid, None)
            old = prev.get(pid)
            if old and old["status"] == p["status"]:
                continue
            cat = from_status(p["status"])
            if not cat:
                continue
            headline = (f"Status: {old['status']} → {p['status']}" if old
                        else f"Added to injury list: {p['status']}")
            alerts.append(Alert("ESPN", p["player"], cat, headline, team=p["team"],
                                detail=p["comment"], url=p["url"]))
        for pid, old in prev.items():
            if pid in current:
                continue
            missing[pid] = missing.get(pid, 0) + 1
            if missing[pid] < 2:
                current[pid] = old  # keep until absence is confirmed on the next poll
                continue
            missing.pop(pid, None)
            alerts.append(Alert("ESPN", old["player"], "AVAILABLE",
                                f"Removed from injury list (was {old['status']})",
                                team=old["team"], url=old["url"], extra={"inferred": True}))
    sec["players"] = {pid: {k: p[k] for k in ("player", "team", "status", "url")}
                      for pid, p in current.items()}
    state.mark_seeded(NAME)
    return alerts
