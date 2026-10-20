import json
from datetime import datetime
from pathlib import Path

import pytest

from bot.classify import classify
from bot.sources import espn, nba_report, rotowire
from bot.state import State
from bot.telegram import ET

FIX = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("headline,body,expected", [
    ("Ruled out Tuesday", "", "OUT"),
    ("Won't play Monday", "", "OUT"),
    ("Out for season", "", "OUT"),
    ("Questionable for Tuesday", "", "QUESTIONABLE"),
    ("Doubtful for Wednesday", "", "DOUBTFUL"),
    ("Available to play", "", "AVAILABLE"),
    ("Not listed on injury report", "", "AVAILABLE"),
    ("Ready for season opener", "", "AVAILABLE"),
    ("Practices Monday", "Hart (ankle) isn't listed on the injury report ahead of Tuesday's game.", "AVAILABLE"),
    ("Starting Monday", "", "LINEUP"),
    ("Coming off bench", "", "LINEUP"),
    ("Traded to Lakers", "", "MOVE"),
    ("Team option picked up", "", None),
    ("Takes part in practice Monday", "Adebayo (groin) participated in Monday's practice.", "UPDATE"),
    ("Scores 30 in win", "Doncic had 30 points and 8 assists.", None),
    ("Works out with team", "", None),
    ("Update", "Beal (knee) will not be ready for the start of the regular season.", "OUT"),
    ("Update", "Vanderbilt (undisclosed) isn't expected to play in Friday's game.", "OUT"),
    ("Update", "Butler (knee) could return sometime between January and February.", "OUT"),
    ("Update", "Carter is without a timetable for a return.", "OUT"),
    ("Update", "Curry (ankle) is expected to play in Wednesday's season opener.", "AVAILABLE"),
    ("Update", "Smith (ankle) has been cleared to play Tuesday.", "AVAILABLE"),
    ("Posts double-double", "Smith totaled 14 points (6-9 FG) and 10 rebounds (career high).", None),
    ("Goes through shootaround", "Jones (left hamstring) went through shootaround.", "UPDATE"),
])
def test_classify(headline, body, expected):
    assert classify(headline, body) == expected


def test_parse_injury_pdf():
    report_time, rows = nba_report.parse_pdf((FIX / "injury_report_sample.pdf").read_bytes())
    assert report_time == "10/19/26 06:15 PM"
    by = {r["player"]: r for r in rows}
    assert by["Devin Carter"]["team"] == "Boston Celtics"
    assert by["Devin Carter"]["status"] == "Out"
    assert by["Dominick Barlow"]["reason"] == "Low Back Stress Reaction"
    assert by["Dominick Barlow"]["matchup"] == "PHI@NYK"
    assert by["Ariel Hukporti"]["reason"] == "Right Achilles Tendon Rupture/Tear"
    assert by["Taurean Prince"]["reason"] == "Personal Reasons"
    assert by["Tobias Harris"]["team"] == "San Antonio Spurs"
    assert by["Tobias Harris"]["matchup"] == "OKC@SAS"
    # page 2 row carries team/game forward from page 1
    assert by["Jayden Quaintance"]["time"].startswith("09:30")


def test_report_diff(tmp_path):
    _, rows = nba_report.parse_pdf((FIX / "injury_report_sample.pdf").read_bytes())
    st = State(str(tmp_path / "s.json"))
    now = datetime(2026, 10, 20, 12, 0, tzinfo=ET)
    assert nba_report.diff(st, rows, now, "u1", "t1") == []  # seeding is silent

    changed = [dict(r) for r in rows]
    for r in changed:
        if r["player"] == "Adem Bona":
            r["status"] = "Out"
    changed = [r for r in changed if r["player"] != "Anfernee Simons"]
    alerts = nba_report.diff(st, changed, now, "u2", "t2")
    got = {(a.player, a.category) for a in alerts}
    assert got == {("Adem Bona", "OUT"), ("Anfernee Simons", "AVAILABLE")}


def test_rotowire_seed_then_new(tmp_path):
    xml = (FIX / "rotowire_sample.xml").read_text()
    items = rotowire.parse(xml)
    assert items[0]["player"] == "Bam Adebayo"
    assert "Visit RotoWire" not in items[0]["detail"]
    assert "//basketball" not in items[0]["url"]


def test_espn_parse():
    data = json.loads((FIX / "espn_sample.json").read_text())
    players = espn.parse(data)
    assert len(players) > 20
    p = next(iter(players.values()))
    assert p["status"] and p["player"] and p["team"]


def test_breakouts_analyze(monkeypatch):
    from bot import ranks
    from bot.sources import breakouts

    # Fake Sleeper ranks: Quickley/Barrett/Poeltl are key starters, everyone else low-profile.
    key = {"Immanuel Quickley": 60, "RJ Barrett": 90, "Jakob Poeltl": 110}
    monkeypatch.setattr(ranks, "lookup", lambda name="", espn_id="": (
        {"rank": key[name], "team": "TOR", "pos": "", "sleeper_id": ""} if name in key
        else {"rank": 400, "team": "", "pos": "G", "sleeper_id": "x"}))
    summary = json.loads((FIX / "espn_summary_tor_det.json").read_text())
    cands = {c["player"]: c for c in breakouts.analyze(summary)}
    smith = cands["Malachi Smith"]
    assert smith["tier"] == "STRONG BUY"
    assert set(smith["key_out"]) == set(key)
    assert smith["fp"] >= breakouts.FP_BREAKOUT and not smith["started"]
    assert all(c["fp"] >= breakouts.FP_BREAKOUT or c["tier"] != "WATCH" for c in cands.values())


def test_fantasy_points():
    from bot.sources.breakouts import fantasy_points
    assert fantasy_points({"pts": 20, "reb": 10, "ast": 5, "stl": 1, "blk": 1, "to": 2}) == 20 + 12 + 7.5 + 3 + 3 - 2
