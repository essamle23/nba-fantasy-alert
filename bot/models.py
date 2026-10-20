from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# category -> (emoji, label). Order = display priority.
CATEGORIES = {
    "OUT": ("🔴", "OUT"),
    "DOUBTFUL": ("🟠", "DOUBTFUL"),
    "QUESTIONABLE": ("🟡", "QUESTIONABLE"),
    "AVAILABLE": ("🟢", "AVAILABLE"),
    "LINEUP": ("🔵", "LINEUP"),
    "MOVE": ("🟣", "TRANSACTION"),
    "UPDATE": ("⚪", "UPDATE"),
}


@dataclass
class Alert:
    source: str  # "RotoWire", "ESPN", "NBA Injury Report"
    player: str
    category: str  # key of CATEGORIES
    headline: str
    team: str = ""
    detail: str = ""
    url: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        return f"{normalize_name(self.player)}|{self.category}"


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", "", s.lower())
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return " ".join(s.split())
