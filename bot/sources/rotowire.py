"""RotoWire NBA player-news RSS. The feed only holds the latest ~5 items, so poll it often."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests

from ..classify import classify
from ..models import Alert
from ..state import State

NAME = "rotowire"
URL = "https://www.rotowire.com/rss/news.php?sport=NBA"
UA = {"User-Agent": "Mozilla/5.0 (nba-fantasy-alert)"}
KEEP_SEEN = 400


def parse(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if ":" not in title:
            continue
        player, headline = (s.strip() for s in title.split(":", 1))
        desc = (it.findtext("description") or "").strip()
        desc = re.sub(r"\s*Visit RotoWire\.com.*$", "", desc, flags=re.S).strip()
        items.append({
            "guid": (it.findtext("guid") or title).strip(),
            "player": player,
            "headline": headline,
            "detail": desc,
            "url": (it.findtext("link") or "").replace(".com//", ".com/"),
        })
    return items


def poll(state: State) -> list[Alert]:
    r = requests.get(URL, headers=UA, timeout=15)
    r.raise_for_status()
    items = parse(r.text)
    sec = state.section(NAME)
    seen = sec.setdefault("seen", [])
    seeded = state.is_seeded(NAME)

    alerts = []
    for item in reversed(items):  # oldest first
        if item["guid"] in seen:
            continue
        seen.append(item["guid"])
        if not seeded:
            continue
        cat = classify(item["headline"], item["detail"])
        if cat:
            alerts.append(Alert("RotoWire", item["player"], cat, item["headline"],
                                detail=item["detail"], url=item["url"]))
    del seen[:-KEEP_SEEN]
    state.mark_seeded(NAME)
    return alerts
