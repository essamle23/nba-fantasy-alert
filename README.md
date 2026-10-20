# nba-fantasy-alert

Pushes fantasy-relevant NBA player news (injuries, status changes, lineup and
roster moves) to a Telegram channel as fast as free sources allow.

## Sources (v1)

| Source | Poll | What triggers an alert |
|---|---|---|
| RotoWire NBA RSS | 30s | New item classified as operational (feed only holds ~5 items) |
| ESPN injuries JSON (unofficial) | 60s | Status change, new entry, removal |
| Official NBA injury report PDF | 2m | Player status change, or dropped off the report before tip |
| ESPN box scores | 3m | **Buy signal** when a game goes final (see below) |

**Buy signals** (`bot/sources/breakouts.py`): a player outside Sleeper's top 130 who
played 20+ min and scored 28+ fantasy points (Yahoo points scoring) or 5+ stocks.
🔥 STRONG BUY = a top-150 teammate sat and he started or played 30+ min ·
✅ BUY = a top-150 teammate sat · 👀 WATCH = no such context but 35+ FP.

**Daily briefing** (`bot/digest.py`) at 07:00 KST (`DIGEST_HOUR_KST`): next games'
official report, notable Out / day-to-day players (Sleeper top 200), and last night's buy signals.

The official report is sent as one digest per update; other alerts are one message each.
The same player + category is sent at most once per 2 hours across sources.

## Categories

🔴 OUT · 🟠 DOUBTFUL · 🟡 QUESTIONABLE · 🟢 AVAILABLE · 🔵 LINEUP · 🟣 TRANSACTION · ⚪ UPDATE

Rules live in `bot/classify.py`; add cases to `tests/test_bot.py` when tuning.

## Running

GitHub Actions (`.github/workflows/poll.yml`) runs one ~5h40m polling job at a time
and chains the next one; an hourly cron restarts the chain if it breaks. State is kept
on the `state` branch. The repo must be public for Actions minutes to be free.

Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Without them the bot runs in
dry-run mode and prints messages to the job log.

Local:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
.venv/bin/python -m pytest -q
ONCE=1 .venv/bin/python -m bot.main   # one poll cycle, dry-run unless secrets are set
```

The first poll of each source only records current state, so a fresh deploy doesn't flood the channel.
