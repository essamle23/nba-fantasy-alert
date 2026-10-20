"""ESPN league-wide injury list (unofficial JSON). Alerts on status changes only —
comments get rewritten daily and would be noise."""
from __future__ import annotations

import requests

from ..classify import from_status
from ..models import Alert
from ..state import State

NAME = "espn"
URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"


def parse(data: dict) -> dict[str, dict]:
    out = {}
    for team in data.get("injuries", []):
        for inj in team.get("injuries", []):
            ath = inj.get("athlete", {})
            pid = str(inj.get("id") or ath.get("displayName"))
            link = next((l["href"] for l in ath.get("links", [])
                         if "playercard" in l.get("rel", [])), "")
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
    prev = sec.get("players", {})
    seeded = state.is_seeded(NAME)

    alerts = []
    if seeded:
        for pid, p in current.items():
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
            if pid not in current:
                alerts.append(Alert("ESPN", old["player"], "AVAILABLE",
                                    f"Removed from injury list (was {old['status']})",
                                    team=old["team"], url=old["url"]))
    sec["players"] = {pid: {k: p[k] for k in ("player", "team", "status", "url")}
                      for pid, p in current.items()}
    state.mark_seeded(NAME)
    return alerts
