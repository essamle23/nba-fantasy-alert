"""Official NBA injury report PDF (published every 15 minutes on game days).

The PDF has no ruling lines, so pdfplumber's table extraction fails. Instead we bucket
words into columns by the header's x positions, then attach wrapped cell lines (which sit
~7px above/below the row) to the nearest row that has a player name.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import datetime, timedelta

import pdfplumber
import requests

from ..classify import from_status
from ..models import Alert
from ..state import State
from ..telegram import ET

log = logging.getLogger(__name__)
NAME = "nba_report"
URL_FMT = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_{:%Y-%m-%d_%I_%M%p}.pdf"
COLS = ["date", "time", "matchup", "team", "player", "status", "reason"]
_SKIP_LINE = re.compile(r"^(Injury Report:|Page \d+ of \d+$|Game Date Game Time)")


def candidate_urls(now: datetime, slots: int = 8) -> list[str]:
    """Newest-first URLs for the last `slots` 15-minute report times."""
    t = now.astimezone(ET).replace(second=0, microsecond=0)
    t -= timedelta(minutes=t.minute % 15)
    return [URL_FMT.format(t - timedelta(minutes=15 * i)) for i in range(slots)]


def find_latest(now: datetime, session=requests) -> str | None:
    for url in candidate_urls(now):
        try:
            if session.head(url, timeout=10).status_code == 200:
                return url
        except requests.RequestException:
            pass
    return None


def _lines(page, tol: float = 2.5) -> list[tuple[float, list[dict]]]:
    words = sorted(page.extract_words(x_tolerance=1.5), key=lambda w: (w["top"], w["x0"]))
    lines: list[tuple[float, list[dict]]] = []
    for w in words:
        if lines and abs(lines[-1][0] - w["top"]) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append((w["top"], [w]))
    return lines


def parse_pdf(data: bytes) -> tuple[str, list[dict]]:
    rows: list[dict] = []
    bounds: list[float] | None = None
    report_time = ""
    carry = {"date": "", "time": "", "matchup": "", "team": ""}

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            anchors: list[dict] = []
            loose: list[tuple[float, dict]] = []
            for top, words in _lines(page):
                text = " ".join(w["text"] for w in words)
                if text.startswith("Injury Report:"):
                    report_time = text.split(":", 1)[1].strip()
                if "Matchup" in text and "Reason" in text:
                    xs = {w["text"]: w["x0"] for w in words}
                    games = [w["x0"] for w in words if w["text"] == "Game"]
                    bounds = [games[0], games[1], xs["Matchup"], xs["Team"],
                              xs["Player"], xs["Current"], xs["Reason"]]
                    continue
                if bounds is None or _SKIP_LINE.match(text):
                    continue
                cells: dict[str, list[str]] = {}
                for w in words:
                    col = max((i for i, b in enumerate(bounds) if w["x0"] >= b - 3), default=0)
                    cells.setdefault(COLS[col], []).append(w["text"])
                cells_s = {k: " ".join(v) for k, v in cells.items()}
                if "player" in cells_s or ("team" in cells_s and "reason" in cells_s):
                    anchors.append({"_top": top, **cells_s})
                else:
                    loose.append((top, cells_s))

            # Wrapped lines: append (or prepend, if above) to the nearest anchor.
            for top, cells_s in loose:
                if not anchors:
                    continue
                a = min(anchors, key=lambda r: abs(r["_top"] - top))
                for k, v in cells_s.items():
                    a[k] = f"{v} {a[k]}" if top < a["_top"] and a.get(k) else f"{a.get(k, '')} {v}".strip()

            for a in anchors:
                for k in carry:
                    if a.get(k):
                        carry[k] = a[k]
                    else:
                        a[k] = carry[k]
                if not a.get("player"):
                    continue  # e.g. "NOT YET SUBMITTED" team rows
                rows.append({
                    "date": a["date"], "time": a["time"], "matchup": a["matchup"],
                    "team": a["team"], "player": flip_name(a["player"]),
                    "status": a.get("status", ""), "reason": tidy_reason(a.get("reason", "")),
                })
    return report_time, rows


def flip_name(s: str) -> str:
    """'Jackson Jr., Jaren' -> 'Jaren Jackson Jr.'"""
    if "," not in s:
        return s
    last, first = (p.strip() for p in s.split(",", 1))
    return f"{first} {last}".strip()


def tidy_reason(s: str) -> str:
    s = re.sub(r"\s*/\s*", "/", s)
    s = re.sub(r"^Injury/Illness\s*-\s*", "", s)
    return " ".join(s.split())


def game_dt(row: dict) -> datetime | None:
    m = re.match(r"(\d{1,2}):(\d{2})", row["time"])
    try:
        d = datetime.strptime(row["date"], "%m/%d/%Y")
    except ValueError:
        return None
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h < 12:
        h += 12  # times are printed without AM/PM; NBA tips are noon or later
    return d.replace(hour=h % 24, minute=mi, tzinfo=ET)


def poll(state: State, now: datetime | None = None) -> list[Alert]:
    now = now or datetime.now(ET)
    sec = state.section(NAME)
    url = find_latest(now)
    if not url or url == sec.get("last_url"):
        return []
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    report_time, rows = parse_pdf(r.content)
    if not rows:
        log.warning("injury report %s parsed 0 rows; skipping", url)
        return []
    alerts = diff(state, rows, now, url, report_time)
    sec["last_url"] = url
    return alerts


def diff(state: State, rows: list[dict], now: datetime, url: str, report_time: str) -> list[Alert]:
    sec = state.section(NAME)
    seeded = state.is_seeded(NAME)
    last_status: dict = sec.setdefault("status", {})
    prev_upcoming: dict = sec.get("upcoming", {})

    # One row per player: their next game that hasn't tipped yet.
    upcoming: dict[str, dict] = {}
    for row in rows:
        if row["reason"].lower().startswith("g league"):
            continue  # two-way/assignment designations don't matter for fantasy
        gdt = game_dt(row)
        if gdt and gdt < now:
            continue
        cur = upcoming.get(row["player"])
        if cur is None or (gdt and game_dt(cur) and gdt < game_dt(cur)):
            upcoming[row["player"]] = row

    alerts = []
    for player, row in upcoming.items():
        prev = last_status.get(player)
        last_status[player] = row["status"]
        if not seeded or prev == row["status"]:
            continue
        cat = from_status(row["status"])
        if not cat:
            continue
        change = f"{prev} → {row['status']}" if prev else row["status"]
        reason = f" · {row['reason']}" if row["reason"] else ""
        alerts.append(Alert("NBA Injury Report", player, cat, f"{change} ({row['matchup']}){reason}",
                            team=row["team"], url=url, extra={"report_time": report_time}))

    # Dropped off the report before their game tipped -> cleared to play.
    if seeded and len(upcoming) >= 0.3 * len(prev_upcoming):
        for player, p in prev_upcoming.items():
            if player in upcoming:
                continue
            tip = datetime.fromisoformat(p["tip"]) if p.get("tip") else None
            if tip and tip > now:
                last_status[player] = "Available"
                alerts.append(Alert("NBA Injury Report", player, "AVAILABLE",
                                    f"Removed from report (was {p['status']}) ({p['matchup']})",
                                    team=p["team"], url=url, extra={"report_time": report_time}))

    sec["upcoming"] = {
        pl: {"status": r["status"], "matchup": r["matchup"], "team": r["team"],
             "tip": game_dt(r).isoformat() if game_dt(r) else None}
        for pl, r in upcoming.items()
    }
    state.mark_seeded(NAME)
    return alerts
