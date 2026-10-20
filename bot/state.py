from __future__ import annotations

import json
import os
import time

STATE_PATH = os.environ.get("STATE_PATH", "state/state.json")
DEDUP_WINDOW_SEC = 2 * 3600


class State:
    def __init__(self, path: str = STATE_PATH):
        self.path = path
        try:
            with open(path) as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def section(self, name: str) -> dict:
        return self.data.setdefault(name, {})

    def is_seeded(self, name: str) -> bool:
        return bool(self.data.get(name, {}).get("_seeded"))

    def mark_seeded(self, name: str) -> None:
        self.section(name)["_seeded"] = True

    # --- cross-source dedup -------------------------------------------------
    def recently_sent(self, key: str) -> bool:
        ts = self.section("sent").get(key)
        return ts is not None and time.time() - ts < DEDUP_WINDOW_SEC

    def mark_sent(self, key: str) -> None:
        sent = self.section("sent")
        sent[key] = time.time()
        cutoff = time.time() - DEDUP_WINDOW_SEC
        for k in [k for k, v in sent.items() if v < cutoff]:
            del sent[k]

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
