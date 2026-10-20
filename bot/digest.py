"""One-shot status digest: fantasy-relevant injuries right now.

Ranks players by Sleeper's search_rank (a fantasy relevance proxy) and keeps only those
inside MAX_RANK, so deep-bench injuries don't bury the ones that matter.

  python -m bot.digest            # print
  python -m bot.digest --send     # post to Telegram
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta

import requests

from .classify import classify
from .models import normalize_name
from .sources import espn, nba_report
from .telegram import ET, Telegram, chunk, esc

SLEEPER_URL = "https://api.sleeper.app/v1/players/nba"
MAX_RANK = 200
TONIGHT_MAX_RANK = 300  # tonight's report matters for streamers too

STATUS_ORDER = {"Out": 0, "Doubtful": 1, "Questionable": 2, "Day-To-Day": 2, "Probable": 3}
STATUS_EMOJI = {"Out": "🔴", "Doubtful": "🟠", "Questionable": "🟡", "Day-To-Day": "🟡",
                "Probable": "🟢", "Available": "🟢"}


def load_ranks() -> tuple[dict[str, dict], dict[str, dict]]:
    """(by ESPN id, by normalized name) -> {rank, team}"""
    data = requests.get(SLEEPER_URL, timeout=60).json()
    by_espn, by_name = {}, {}
    for p in data.values():
        if not (p.get("active") and p.get("team") and p.get("search_rank")):
            continue
        info = {"rank": p["search_rank"], "team": p["team"]}
        if p.get("espn_id"):
            by_espn[str(p["espn_id"])] = info
        by_name[normalize_name(p.get("full_name", ""))] = info
    return by_espn, by_name


def short_comment(text: str, limit: int = 150) -> str:
    # Split after a lowercase/digit/quote/paren + period so initials like "K.C." don't end the sentence.
    s = re.split(r"(?<=[a-z0-9)\"'][.!?])\s+(?=[A-Z])", text.strip(), maxsplit=1)[0]
    # Drop trailing attribution: ", Shams Charania of ESPN reports." / ", per X." / ", NBA writer Y reports."
    s = re.sub(r",\s*(per|according to) [^,]+\.$", ".", s)
    s = re.sub(r",\s*[^,]{3,70}\s(reports?|relays?|writes|notes|says)\.$", ".", s)
    return s if len(s) <= limit else s[: limit - 1].rsplit(" ", 1)[0] + "…"


def short_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET).strftime("%b %-d")
    except ValueError:
        return ""


def espn_id(url: str) -> str:
    m = re.search(r"/id/(\d+)", url or "")
    return m.group(1) if m else ""


def build(now: datetime | None = None) -> list[str]:
    now = now or datetime.now(ET)
    by_espn, by_name = load_ranks()

    def rank_of(name: str, url: str = "") -> dict | None:
        return by_espn.get(espn_id(url)) or by_name.get(normalize_name(name))

    # Tonight: the official report, games that haven't tipped.
    tonight_lines, tonight_players = [], set()
    url = nba_report.find_latest(now)
    report_time = ""
    if url:
        report_time, rows = nba_report.parse_pdf(requests.get(url, timeout=30).content)
        picked = []
        for r in rows:
            gdt = nba_report.game_dt(r)
            info = rank_of(r["player"])
            if not info or info["rank"] > TONIGHT_MAX_RANK or (gdt and gdt < now - timedelta(hours=3)):
                continue
            picked.append((STATUS_ORDER.get(r["status"], 9), info["rank"], r, info))
        for _, _, r, info in sorted(picked, key=lambda t: (t[0], t[1])):
            tonight_players.add(normalize_name(r["player"]))
            reason = f" — {esc(r['reason'])}" if r["reason"] else ""
            tonight_lines.append(f"{STATUS_EMOJI.get(r['status'], '⚪')} <b>{esc(r['player'])}</b> "
                                 f"({info['team']}, {esc(r['matchup'])}) {esc(r['status'])}{reason}")

    # Everyone else on ESPN's list, notable players only.
    current = espn.parse(requests.get(espn.URL, timeout=15).json())
    out_lines, dtd_lines = [], []
    for p in sorted(current.values(),
                    key=lambda p: (rank_of(p["player"], p["url"]) or {"rank": 9e9})["rank"]):
        info = rank_of(p["player"], p["url"])
        if not info or info["rank"] > MAX_RANK or normalize_name(p["player"]) in tonight_players:
            continue
        emoji = STATUS_EMOJI.get(p["status"], "⚪")
        if p["status"] != "Out" and classify("", p["comment"]) == "AVAILABLE":
            emoji = "🟢"  # day-to-day but reported as expected to play
        when = short_date(p.get("date", ""))
        line = (f"{emoji} <b>{esc(p['player'])}</b> ({info['team']}) — "
                f"{esc(short_comment(p['comment']))}" + (f" <i>({when})</i>" if when else ""))
        (out_lines if p["status"] == "Out" else dtd_lines).append(line)

    date = now.strftime("%b %-d")
    msgs = [f"🏀 <b>Fantasy Injury Digest · {date}</b>\n"
            f"Fantasy-relevant players only (top {MAX_RANK} by Sleeper rank), most valuable first.\n"
            f"🔴 Out · 🟠 Doubtful · 🟡 Questionable/GTD · 🟢 Probable/expected to play\n"
            f"<i>(date)</i> = last update; older notes may predate today's status."]
    if tonight_lines:
        msgs += chunk(tonight_lines, f"📋 <b>Official injury report — next games</b> · {esc(report_time)} ET"
                                     f' · <a href="{esc(url)}">PDF</a>')
    if out_lines:
        msgs += chunk(out_lines, "🔴 <b>Out / extended absence</b>")
    if dtd_lines:
        msgs += chunk(dtd_lines, "🟡 <b>Day-to-day</b>")
    return msgs


def main() -> None:
    msgs = build()
    send = "--send" in sys.argv
    tg = Telegram() if send else None
    for m in msgs:
        if tg:
            tg.send(m)
        else:
            print(m + "\n" + "-" * 40)


if __name__ == "__main__":
    main()
