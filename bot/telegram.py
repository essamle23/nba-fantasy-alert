from __future__ import annotations

import html
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .models import CATEGORIES, Alert

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
MAX_LEN = 4000  # Telegram hard limit is 4096


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def format_alert(a: Alert) -> str:
    emoji, label = CATEGORIES[a.category]
    if a.extra.get("tier"):
        emoji, label = {"STRONG BUY": "🔥", "BUY": "✅", "WATCH": "👀"}[a.extra["tier"]], a.extra["tier"]
    team = f" ({esc(a.team)})" if a.team else ""
    lines = [f"{emoji} <b>{label} · {esc(a.player)}</b>{team}", esc(a.headline)]
    if a.detail:
        detail = a.detail if len(a.detail) <= 350 else a.detail[:347].rsplit(" ", 1)[0] + "…"
        lines.append(f"<i>{esc(detail)}</i>")
    stamp = datetime.now(ET).strftime("%-I:%M %p ET")
    src = f'<a href="{esc(a.url)}">{esc(a.source)}</a>' if a.url else esc(a.source)
    game = f" · {esc(a.extra['score'])}" if a.extra.get("score") else ""
    lines.append(f"— {src}{game} · {stamp}")
    return "\n".join(lines)


def chunk(lines: list[str], header: str) -> list[str]:
    """Pack lines into as few messages as fit under MAX_LEN."""
    out, cur = [], header
    for ln in lines:
        if len(cur) + len(ln) + 1 > MAX_LEN:
            out.append(cur)
            cur = header + " (cont.)"
        cur += "\n" + ln
    out.append(cur)
    return out


class Telegram:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self.dry_run = not (self.token and self.chat_id) or os.environ.get("DRY_RUN") == "1"
        self._last = 0.0
        if self.dry_run:
            log.warning("Telegram DRY RUN — messages are printed, not sent")

    def send(self, text: str) -> bool:
        if self.dry_run:
            print("\n" + text + "\n" + "-" * 40, flush=True)
            return True
        for attempt in range(4):
            # Channels allow ~20 msgs/min; keep a 3s gap.
            wait = 3.0 - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = requests.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": True},
                    timeout=15,
                )
            except requests.RequestException as e:
                log.warning("telegram send failed (%s), retrying", e)
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                retry = r.json().get("parameters", {}).get("retry_after", 5)
                log.warning("telegram 429, sleeping %ss", retry)
                time.sleep(retry + 1)
                continue
            if r.ok:
                return True
            log.error("telegram error %s: %s", r.status_code, r.text[:300])
            return False
        return False
