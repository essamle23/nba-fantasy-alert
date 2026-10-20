"""Buy signals: low-profile players who exploded, especially while a key teammate sat.

Runs on ESPN box scores as soon as each game goes final, so the alert lands before
the waiver-wire rush the next morning.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta

import requests

from .. import ranks
from ..models import Alert
from ..state import State
from ..telegram import ET

log = logging.getLogger(__name__)
NAME = "breakouts"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={:%Y%m%d}"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={}"

LOW_PROFILE_RANK = 130   # top ~130 are rostered in nearly every 12-team league
KEY_TEAMMATE_RANK = 150  # a teammate this valuable sitting opens real minutes
FP_BREAKOUT = 28.0
FP_WATCH = 35.0          # without a key teammate out, only true explosions
MIN_MINUTES = 20
# Preseason is useful for testing only.
SEASON_TYPES = {2, 3} if os.environ.get("BREAKOUT_PRESEASON") != "1" else {1, 2, 3}


def fantasy_points(s: dict) -> float:
    """Yahoo default points-league scoring."""
    return s["pts"] + 1.2 * s["reb"] + 1.5 * s["ast"] + 3 * s["stl"] + 3 * s["blk"] - s["to"]


def _num(v: str) -> int:
    try:
        return int(str(v).lstrip("+"))
    except ValueError:
        return 0


def parse_line(keys: list[str], raw: list[str]) -> dict:
    row = dict(zip(keys, raw))
    made3 = row.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "0-0").split("-")[0]
    return {
        "min": _num(row.get("minutes", 0)), "pts": _num(row.get("points", 0)),
        "reb": _num(row.get("rebounds", 0)), "ast": _num(row.get("assists", 0)),
        "stl": _num(row.get("steals", 0)), "blk": _num(row.get("blocks", 0)),
        "to": _num(row.get("turnovers", 0)), "3pm": _num(made3),
    }


def stat_line(s: dict) -> str:
    parts = [f"{s['pts']} pts", f"{s['reb']} reb", f"{s['ast']} ast"]
    for k, label in (("stl", "stl"), ("blk", "blk"), ("3pm", "3PM")):
        if s[k] >= 2:
            parts.append(f"{s[k]} {label}")
    return ", ".join(parts) + f" in {s['min']} min"


def analyze(summary: dict) -> list[dict]:
    """Return breakout candidates from one final game's ESPN summary."""
    comp = summary["header"]["competitions"][0]
    score = " – ".join(f"{c['team']['abbreviation']} {c.get('score', '')}"
                       for c in sorted(comp["competitors"], key=lambda c: c["homeAway"] != "away"))

    # Key absences per team: DNPs in the box score plus the team injury list.
    absent: dict[str, dict[str, int]] = {}
    for t in summary.get("injuries", []):
        abbr = t["team"]["abbreviation"]
        for i in t.get("injuries", []):
            if i.get("status") in ("Out", "Day-To-Day", "Doubtful", "Questionable"):
                absent.setdefault(abbr, {})[i["athlete"]["displayName"]] = i["athlete"]["id"]

    out = []
    for team in summary["boxscore"]["players"]:
        abbr = team["team"]["abbreviation"]
        block = team["statistics"][0]
        keys = block["keys"]
        played = {a["athlete"]["id"] for a in block["athletes"] if a.get("stats") and not a.get("didNotPlay")}
        for a in block["athletes"]:
            if a.get("didNotPlay") or not a.get("stats"):
                absent.setdefault(abbr, {})[a["athlete"]["displayName"]] = a["athlete"]["id"]
        key_out = []
        for name, aid in absent.get(abbr, {}).items():
            info = ranks.lookup(name, aid)
            if aid not in played and info and info["rank"] <= KEY_TEAMMATE_RANK:
                key_out.append((info["rank"], name))
        key_out = [n for _, n in sorted(key_out)]

        for a in block["athletes"]:
            if a.get("didNotPlay") or not a.get("stats"):
                continue
            s = parse_line(keys, a["stats"])
            fp = fantasy_points(s)
            stocks = s["stl"] + s["blk"]
            if s["min"] < MIN_MINUTES or not (fp >= FP_BREAKOUT or stocks >= 5):
                continue
            ath = a["athlete"]
            info = ranks.lookup(ath["displayName"], ath["id"]) or {"rank": 99999, "pos": "", "sleeper_id": ""}
            if info["rank"] <= LOW_PROFILE_RANK:
                continue
            started = bool(a.get("starter"))
            if key_out and (started or s["min"] >= 30) and fp >= FP_BREAKOUT:
                tier = "STRONG BUY"
            elif key_out:
                tier = "BUY"
            elif fp >= FP_WATCH:
                tier = "WATCH"
            else:
                continue
            out.append({
                "player": ath["displayName"], "espn_id": ath["id"], "team": abbr, "pos": info["pos"],
                "rank": info["rank"], "sleeper_id": info["sleeper_id"], "fp": round(fp, 1),
                "line": stat_line(s), "started": started, "key_out": key_out[:3],
                "tier": tier, "score": score,
            })
    return out


TIER_EMOJI = {"STRONG BUY": "🔥", "BUY": "✅", "WATCH": "👀"}


def to_alert(c: dict, adds: dict[str, int]) -> Alert:
    head = f"{c['fp']} FP — {c['line']}" + (" (started)" if c["started"] else " (bench)")
    ctx = []
    if c["key_out"]:
        ctx.append("With " + ", ".join(c["key_out"]) + " out")
    rank = "unranked" if c["rank"] >= 99999 else f"Sleeper rank #{c['rank']}"
    ctx.append(f"{rank} — likely on waivers in most leagues")
    n = adds.get(c["sleeper_id"])
    if n:
        ctx.append(f"Sleeper adds (24h): {n:,}")
    a = Alert("ESPN box score", c["player"], "BUY", head, team=f"{c['team']}{', ' + c['pos'] if c['pos'] else ''}",
              detail="\n".join(ctx), url=f"https://www.espn.com/nba/player/_/id/{c['espn_id']}",
              extra={"tier": c["tier"], "score": c["score"]})
    return a


def poll(state: State, now: datetime | None = None) -> list[Alert]:
    now = now or datetime.now(ET)
    sec = state.section(NAME)
    done: list = sec.setdefault("done", [])
    seeded = state.is_seeded(NAME)

    alerts, adds = [], None
    for day in (now - timedelta(days=1), now):
        events = requests.get(SCOREBOARD.format(day), timeout=15).json().get("events", [])
        for e in events:
            st = e["competitions"][0]["status"]["type"]
            if not st.get("completed") or e["id"] in done:
                continue
            done.append(e["id"])
            if not seeded or e.get("season", {}).get("type") not in SEASON_TYPES:
                continue
            summary = requests.get(SUMMARY.format(e["id"]), timeout=20).json()
            cands = analyze(summary)
            if adds is None:
                adds = ranks.trending_adds()
            for c in sorted(cands, key=lambda c: -c["fp"]):
                alerts.append(to_alert(c, adds))
                sec.setdefault("recent", []).append({**c, "ts": time.time()})
    del done[:-200]
    cutoff = time.time() - 36 * 3600
    sec["recent"] = [c for c in sec.get("recent", []) if c["ts"] > cutoff]
    state.mark_seeded(NAME)
    return alerts
