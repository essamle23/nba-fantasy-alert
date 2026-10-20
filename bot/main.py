"""Poll all sources in a loop until RUN_MINUTES elapse, then exit 0 so the workflow
can save state and dispatch the next run."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from . import digest
from .models import CATEGORIES, Alert
from .sources import breakouts, espn, nba_report, rotowire
from .state import State
from .telegram import Telegram, chunk, esc, format_alert

log = logging.getLogger("bot")

# (module, interval seconds)
SOURCES = [
    (rotowire, 30),
    (espn, 60),
    (nba_report, 120),
    (breakouts, 180),
]
KST = timezone(timedelta(hours=9))
# Daily briefing, Korea time: before the first tip-offs (~08:00 KST) and after
# game-day injury reports are mostly in. Only sent inside a 1-hour window so a
# restart later in the day doesn't fire a stale briefing.
DIGEST_HOUR_KST = int(os.environ.get("DIGEST_HOUR_KST", "7"))


def maybe_send_digest(tg: Telegram, state: State) -> None:
    now = datetime.now(KST)
    sec = state.section("digest")
    today = now.strftime("%Y-%m-%d")
    if "snapshot" not in sec:
        # No baseline yet: record one silently so the first briefing shows changes
        # instead of re-sending the full injury list.
        digest.build(state=state, commit=True)
        state.save()
        log.info("digest baseline snapshot recorded")
    if now.hour != DIGEST_HOUR_KST or sec.get("last") == today:
        return
    sec["last"] = today
    for msg in digest.build(state=state, commit=True):
        tg.send(msg)
    state.save()
    log.info("daily digest sent")


ORDER = list(CATEGORIES)


def publish(tg: Telegram, state: State, alerts: list[Alert]) -> None:
    fresh = []
    for a in alerts:
        if state.recently_sent(a.dedup_key):
            log.info("dedup skip: %s %s (%s)", a.player, a.category, a.source)
            continue
        state.mark_sent(a.dedup_key)
        fresh.append(a)

    # The official report can change dozens of players at once: send it as one digest.
    report = sorted((a for a in fresh if a.source == "NBA Injury Report"),
                    key=lambda a: ORDER.index(a.category))
    for a in fresh:
        if a.source != "NBA Injury Report":
            tg.send(format_alert(a))
    if report:
        when = report[0].extra.get("report_time", "")
        header = f'📋 <b>Official NBA Injury Report</b> · {esc(when)} · <a href="{esc(report[0].url)}">PDF</a>'
        lines = [f"{CATEGORIES[a.category][0]} <b>{esc(a.player)}</b> — {esc(a.headline)}" for a in report]
        for msg in chunk(lines, header):
            tg.send(msg)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run_minutes = float(os.environ.get("RUN_MINUTES", "340"))
    deadline = time.time() + run_minutes * 60
    state = State()
    tg = Telegram()
    next_due = {m.NAME: 0.0 for m, _ in SOURCES}
    errors = {m.NAME: 0 for m, _ in SOURCES}

    while time.time() < deadline:
        for mod, interval in SOURCES:
            if time.time() < next_due[mod.NAME]:
                continue
            try:
                alerts = mod.poll(state)
                errors[mod.NAME] = 0
                if alerts:
                    log.info("%s: %d alerts", mod.NAME, len(alerts))
                    publish(tg, state, alerts)
            except Exception:
                errors[mod.NAME] += 1
                log.exception("%s poll failed (%d in a row)", mod.NAME, errors[mod.NAME])
            # Back off a failing source, up to 10 minutes.
            backoff = min(interval * (2 ** errors[mod.NAME]), 600) if errors[mod.NAME] else interval
            next_due[mod.NAME] = time.time() + backoff
            state.save()
        try:
            maybe_send_digest(tg, state)
        except Exception:
            log.exception("digest failed")
        if os.environ.get("ONCE") == "1":
            break
        time.sleep(max(1.0, min(next_due.values()) - time.time()))
    state.save()
    log.info("run finished")


if __name__ == "__main__":
    run()
