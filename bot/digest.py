"""Daily briefing: what changed for fantasy-relevant players since the last one.

Ranks players by Sleeper's search_rank (a fantasy relevance proxy) and keeps only those
inside MAX_RANK, so deep-bench injuries don't bury the ones that matter.

  python -m bot.digest            # print
  python -m bot.digest --send     # post to Telegram
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone

import requests

from .classify import classify
from . import ranks
from .models import normalize_name
from .state import State
from .sources import espn, nba_report
from .telegram import ET, Telegram, chunk, esc

KST = timezone(timedelta(hours=9))
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


def _buy_lines(recent: list[dict]) -> list[str]:
    tiers = {"STRONG BUY": 0, "BUY": 1, "WATCH": 2}
    emoji = {"STRONG BUY": "🔥", "BUY": "✅", "WATCH": "👀"}
    lines = []
    for c in sorted(recent, key=lambda c: (tiers[c["tier"]], -c["fp"])):
        ctx = f" · with {esc(', '.join(c['key_out']))} out" if c["key_out"] else ""
        lines.append(f"{emoji[c['tier']]} <b>{esc(c['player'])}</b> ({c['team']}) "
                     f"{c['fp']} FP — {esc(c['line'])}{ctx}")
    return lines


def build(now: datetime | None = None, state: State | None = None, commit: bool = False) -> list[str]:
    """Change-focused briefing. Compares against the snapshot saved at the previous
    briefing so unchanged long-term injuries collapse to one line instead of repeating.
    commit=True saves the new snapshot (only when the briefing is actually sent)."""
    now = now or datetime.now(ET)
    sec = state.section("digest") if state else {}
    prev: dict = sec.get("snapshot") or {}
    baseline = not prev
    full_week_view = datetime.now(KST).weekday() == 0  # Mondays: full detail

    def rank_of(name: str, url: str = "") -> dict | None:
        info = ranks.lookup(name, ranks.espn_id_from_url(url))
        return info if info and info["rank"] < 99999 else None

    # --- ESPN list: notable players, diffed against the last briefing -------------
    current = espn.parse(requests.get(espn.URL, timeout=15).json())
    snap: dict[str, dict] = {}
    for p in current.values():
        info = rank_of(p["player"], p["url"])
        if info and info["rank"] <= MAX_RANK:
            snap[normalize_name(p["player"])] = {**p, "rank": info["rank"], "team": info["team"]}

    def emoji_for(p: dict) -> str:
        if p["status"] != "Out" and classify("", p["comment"]) == "AVAILABLE":
            return "🟢"  # day-to-day but reported as expected to play
        return STATUS_EMOJI.get(p["status"], "⚪")

    def note(p: dict) -> str:
        when = short_date(p.get("date", ""))
        return esc(short_comment(p["comment"])) + (f" <i>({when})</i>" if when else "")

    changes, notes, unchanged = [], [], []
    for key, p in sorted(snap.items(), key=lambda kv: kv[1]["rank"]):
        old = prev.get(key)
        head = f"{emoji_for(p)} <b>{esc(p['player'])}</b> ({p['team']})"
        if baseline:
            unchanged.append((p, f"{head} — {note(p)}"))
        elif old is None:
            changes.append(f"{head} — <b>new: {esc(p['status'])}</b> — {note(p)}")
        elif old["status"] != p["status"]:
            changes.append(f"{head} — <b>{esc(old['status'])} → {esc(p['status'])}</b> — {note(p)}")
        elif p.get("date") and p.get("date") != old.get("date"):
            notes.append(f"{head} — {note(p)}")
        else:
            unchanged.append((p, f"{head} — {note(p)}"))
    for key, old in sorted(prev.items(), key=lambda kv: kv[1]["rank"]):
        if key not in snap and key not in {normalize_name(p["player"]) for p in current.values()}:
            changes.append(f"🟢 <b>{esc(old['player'])}</b> ({old['team']}) — <b>off the injury list</b> "
                           f"(was {esc(old['status'])})")
    known_out = {k for k, v in prev.items() if v["status"] == "Out"}

    # --- Tonight: official report; collapse unchanged long-term Outs ----------------
    tonight_lines, tonight_quiet, tonight_names = [], [], set()
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
            tonight_names.add(normalize_name(r["player"]))
            if r["status"] == "Out" and normalize_name(r["player"]) in known_out:
                tonight_quiet.append(esc(r["player"]))
                continue
            reason = f" — {esc(r['reason'])}" if r["reason"] else ""
            tonight_lines.append(f"{STATUS_EMOJI.get(r['status'], '⚪')} <b>{esc(r['player'])}</b> "
                                 f"({info['team']}, {esc(r['matchup'])}) {esc(r['status'])}{reason}")
        if tonight_quiet:
            tonight_lines.append("<i>Also out, no change: " + ", ".join(tonight_quiet) + "</i>")

    recent = state.data.get("breakouts", {}).get("recent", []) if state else []

    # --- Assemble -----------------------------------------------------------------
    date = datetime.now(KST).strftime("%a %b %-d")
    if baseline:
        summary = "Baseline briefing — from tomorrow, only what changed is shown in detail."
    else:
        summary = (f"{len(recent)} buy signals · {len(changes)} status changes · "
                   f"{len(notes)} new updates since yesterday")
    msgs = [f"🏀 <b>Daily Fantasy Briefing · {date}</b>\n{summary}\n"
            f"<i>Fantasy-relevant players only (Sleeper top {MAX_RANK}).</i>"]
    if recent:
        msgs += chunk(_buy_lines(recent), "💰 <b>Buy signals from last night</b>")
    if changes:
        msgs += chunk(changes, "🆕 <b>Status changes since yesterday</b>")
    if notes:
        msgs += chunk(notes, "📝 <b>New updates (same status)</b>")
    if tonight_lines:
        msgs += chunk(tonight_lines, f"📋 <b>Tonight — official injury report</b> · {esc(report_time)} ET"
                                     f' · <a href="{esc(url)}">PDF</a>')
    if unchanged:
        if baseline or full_week_view:
            outs = [ln for p, ln in unchanged if p["status"] == "Out"]
            dtd = [ln for p, ln in unchanged if p["status"] != "Out"]
            title = "" if baseline else " (weekly full list)"
            if outs:
                msgs += chunk(outs, f"🔴 <b>Out / extended absence{title}</b>")
            if dtd:
                msgs += chunk(dtd, f"🟡 <b>Day-to-day{title}</b>")
        else:
            rest = [p for p, _ in unchanged if normalize_name(p["player"]) not in tonight_names]
            if rest:
                names = ", ".join(esc(p["player"]) for p in rest)
                msgs.append(f"🔁 <b>No change</b> ({len(rest)}): {names}\n"
                            f"<i>Full details every Monday.</i>")
    if not baseline and not (recent or changes or notes):
        msgs.insert(1, "😴 Quiet night — no status changes or buy signals since yesterday.")

    if commit and state is not None:
        sec["snapshot"] = {k: {f: v[f] for f in ("player", "team", "status", "date", "rank")}
                           for k, v in snap.items()}
    return msgs


def main() -> None:
    send = "--send" in sys.argv
    state = State()
    msgs = build(state=state, commit=send)
    if send:
        state.save()
    tg = Telegram() if send else None
    for m in msgs:
        if tg:
            tg.send(m)
        else:
            print(m + "\n" + "-" * 40)


if __name__ == "__main__":
    main()
