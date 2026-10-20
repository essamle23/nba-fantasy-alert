"""Sleeper player DB as a fantasy-relevance lookup (search_rank: 1 = most valuable).

Cached in memory for a few hours: the file is ~2.5MB and ranks move slowly.
"""
from __future__ import annotations

import re
import time

import requests

from .models import normalize_name

SLEEPER_URL = "https://api.sleeper.app/v1/players/nba"
TRENDING_URL = "https://api.sleeper.app/v1/players/nba/trending/add?lookback_hours=24&limit=300"
TTL = 6 * 3600

_cache: dict = {"ts": 0.0}


def _load() -> None:
    data = requests.get(SLEEPER_URL, timeout=60).json()
    by_espn, by_name, by_sleeper = {}, {}, {}
    for pid, p in data.items():
        if not (p.get("active") and p.get("team")):
            continue
        info = {"rank": p.get("search_rank") or 99999, "team": p["team"],
                "pos": "/".join(p.get("fantasy_positions") or []), "sleeper_id": pid}
        if p.get("espn_id"):
            by_espn[str(p["espn_id"])] = info
        by_name[normalize_name(p.get("full_name", ""))] = info
        by_sleeper[pid] = info
    _cache.update(ts=time.time(), espn=by_espn, name=by_name, sleeper=by_sleeper)


def lookup(name: str = "", espn_id: str = "") -> dict | None:
    if time.time() - _cache["ts"] > TTL:
        _load()
    return _cache["espn"].get(str(espn_id)) or _cache["name"].get(normalize_name(name))


def espn_id_from_url(url: str) -> str:
    m = re.search(r"/id/(\d+)", url or "")
    return m.group(1) if m else ""


def trending_adds() -> dict[str, int]:
    """sleeper player_id -> adds in the last 24h across Sleeper leagues."""
    try:
        return {str(t["player_id"]): t["count"]
                for t in requests.get(TRENDING_URL, timeout=15).json()}
    except (requests.RequestException, ValueError):
        return {}
