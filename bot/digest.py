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
from . import ranks
from .models import normalize_name
from .state import State
from .sources import espn, nba_report
from .telegram import ET, Telegram, chunk, esc

MAX_RANK = 200
TONIGHT_MAX_RANK = 300  # tonight's report matters for streamers too

STATUS_ORDER = {"Out": 0, "Doubtful": 1, "Questionable": 2, "Day-To-Day": 2, "Probable": 3}
STATUS_EMOJI = {"Out": "🔴", "Doubtful": "🟠", "Questionable": "🟡", "Day-To-Day": "🟡",
                "Probable": "🟢", "Available": "🟢"}


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


def build(now: datetime | None = None, state: State | None = None) -> list[str]:
    now = now or datetime.now(ET)

    def rank_of(name: str, url: str = "") -> dict | None:
        info = ranks.lookup(name, ranks.espn_id_from_url(url))
        return info if info and info["rank"] < 99999 else None

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
    msgs = [f"🏀 <b>Daily Fantasy Briefing · {date}</b>\n"
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

    recent = (state.data.get("breakouts", {}).get("recent", []) if state else [])
    if recent:
        tiers = {"STRONG BUY": 0, "BUY": 1, "WATCH": 2}
        emoji = {"STRONG BUY": "🔥", "BUY": "✅", "WATCH": "👀"}
        lines = []
        for c in sorted(recent, key=lambda c: (tiers[c["tier"]], -c["fp"])):
            ctx = f" · with {esc(', '.join(c['key_out']))} out" if c["key_out"] else ""
            lines.append(f"{emoji[c['tier']]} <b>{esc(c['player'])}</b> ({c['team']}) "
                         f"{c['fp']} FP — {esc(c['line'])}{ctx}")
        msgs += chunk(lines, "💰 <b>Buy signals from last night</b>")
    return msgs


def main() -> None:
    msgs = build(state=State())
    send = "--send" in sys.argv
    tg = Telegram() if send else None
    for m in msgs:
        if tg:
            tg.send(m)
        else:
            print(m + "\n" + "-" * 40)


if __name__ == "__main__":
    main()
