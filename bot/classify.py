"""Rule-based classification of free-text player news into fantasy-relevant categories.

Returns None for news that doesn't change how a fantasy manager should act
(contract options, awards, quotes, highlights...).
"""
from __future__ import annotations

import re

_DAY = r"(today|tonight|monday|tuesday|wednesday|thursday|friday|saturday|sunday|vs\.?|against|for)"

# Checked in order; first match wins. Patterns run on the headline first, then the body.
RULES = [
    ("MOVE", r"\b(traded|acquired|waived|released|claimed off|signs|signed|signing|two-way|"
             r"assigned to|recalled|buyout|suspended|suspension)\b"),
    ("OUT", r"\b(ruled out|won't play|will not play|not playing|out for|out indefinitely|"
            r"out (again )?" + _DAY + r"|sidelined|will miss|misses|to miss|season-ending|"
            r"shut down|inactive|won't suit up|^out\b|(remains|will be|is|still) out\b|"
            r"(without|no) (a |any )?(definitive |clear |firm )?timetable|"
            r"underwent surgery|undergo surgery|re-evaluated in|"
            r"(won't|will not|isn't|is not|not) (be )?(ready|expected to (play|suit up)|cleared)|"
            r"not yet (been )?cleared|(could|potential|possible|targeting a) return (sometime|between|in|by|around)|"
            r"(weeks?|months?) away)"),
    ("DOUBTFUL", r"\bdoubtful\b"),
    ("QUESTIONABLE", r"\b(questionable|game-time|gtd|uncertain|day-to-day|iffy|in doubt|"
                     r"hopeful to play|hopes to play|optimistic)\b"),
    ("AVAILABLE", r"\b(available|(has been|was|been|gets|got) cleared|cleared to (play|return)|"
                  r"will play|good to go|returns? to (action|the lineup|play)|will return|set to return|"
                  r"makes? (his )?return|making (his )?return|back in (the )?lineup|"
                  r"probable|active|upgraded|expected to play|set to play|plans to play|"
                  r"ready (for|to)|off (the )?injury report|in lineup|"
                  r"(not|isn't|wasn't|won't be) (listed |included )?on (the |tonight's |\w+'s )?injury report)\b"),
    ("LINEUP", r"\b(start|starts|starting|starter|bench|off the bench|minutes restriction|"
               r"minutes limit|minutes cap|rotation|dnp|coach's decision|rest(ing)?)\b"),
    ("UPDATE", r"\b(mri|x-rays?|re-evaluat\w*|surgery|practice[sd]?|sprain\w*|strain\w*|"
               r"fracture\w*|tear|torn|injur\w*|soreness|illness|concussion|"
               r"health and safety)\b"),
]
_COMPILED = [(cat, re.compile(p, re.I)) for cat, p in RULES]

# News that looks operational by keyword but isn't.
_NOISE = re.compile(r"\b(team option|player option|extension|contract|award|named|"
                    r"podcast|mock draft|jersey)\b", re.I)

# "Adebayo (groin) participated..." — a body part in parentheses marks injury context.
_BODY = (r"ankle|knee|foot|feet|toe|heel|achilles|calf|shin|hamstring|quad\w*|groin|hip|"
         r"back|spine|oblique|abdom\w*|rib|chest|shoulder|elbow|wrist|hand|finger|thumb|"
         r"neck|head|concussion|eye|nose|face|jaw|illness|personal|rest|leg|arm|thigh|"
         r"pelvis|glute|adductor|core|lumbar|plantar|undisclosed|conditioning|suspension")
_INJURY_PAREN = re.compile(r"\((left |right |lower |upper )?(" + _BODY + r")\b[^)]{0,20}\)", re.I)


def _match(text: str, cats: set[str]) -> str | None:
    for cat, rx in _COMPILED:
        if cat in cats and rx.search(text):
            return cat
    return None


STRONG = {"MOVE", "OUT", "DOUBTFUL", "QUESTIONABLE", "AVAILABLE", "LINEUP"}


def classify(headline: str, body: str = "") -> str | None:
    if _NOISE.search(headline) and not re.search(r"traded|waived|released", headline, re.I):
        return None
    # RotoWire's first sentence is about the subject player; later sentences often
    # mention teammates ("with Jones sidelined..."), so only the first one may decide status.
    first = re.split(r"(?<=[.!?])\s+", body.strip(), maxsplit=1)[0] if body else ""
    return (_match(headline, STRONG)
            or _match(first, STRONG - {"LINEUP"})
            or _match(headline, {"UPDATE"})
            or _match(first, {"UPDATE"})
            or (_match(body, {"LINEUP", "UPDATE"}) if _INJURY_PAREN.search(body) else None)
            or ("UPDATE" if _INJURY_PAREN.search(body) else None))


def from_status(status: str) -> str | None:
    """Map a structured status (NBA report / ESPN) to a category."""
    s = status.strip().lower()
    if s in ("out", "suspension", "suspended", "out for season"):
        return "OUT"
    if s == "doubtful":
        return "DOUBTFUL"
    if s in ("questionable", "day-to-day", "game time decision", "gtd"):
        return "QUESTIONABLE"
    if s in ("probable", "available", "active"):
        return "AVAILABLE"
    return None
