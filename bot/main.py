"""Poll all sources in a loop until RUN_MINUTES elapse, then exit 0 so the workflow
can save state and dispatch the next run."""
from __future__ import annotations

import logging
import os
import time

from .models import CATEGORIES, Alert
from .sources import espn, nba_report, rotowire
from .state import State
from .telegram import Telegram, chunk, esc, format_alert

log = logging.getLogger("bot")

# (module, interval seconds)
SOURCES = [
    (rotowire, 30),
    (espn, 60),
    (nba_report, 120),
]
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
        if os.environ.get("ONCE") == "1":
            break
        time.sleep(max(1.0, min(next_due.values()) - time.time()))
    state.save()
    log.info("run finished")


if __name__ == "__main__":
    run()
