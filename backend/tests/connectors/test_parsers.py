"""Parser tests for the data connectors (no network)."""

from datetime import date

import pytest

from app.connectors import football_data as fd
from app.connectors import fpl
from app.connectors import polymarket as pm
from app.connectors import understat as us
from app.connectors.team_names import to_canonical

UNDERSTAT = {
    "teams": {},
    "players": [{"id": "1", "player_name": "Mohamed Salah", "games": "38", "time": "3392",
                 "xG": "27.7", "xA": "15.9", "npxG": "20.9", "xGChain": "48.5",
                 "position": "F M", "team_title": "Liverpool"}],
    "dates": [
        {"id": "1", "isResult": True, "datetime": "2024-08-16 19:00:00",
         "h": {"title": "Manchester United"}, "a": {"title": "Fulham"},
         "goals": {"h": "1", "a": "0"}, "xG": {"h": "2.04", "a": "0.42"}},
        {"id": "2", "isResult": False, "datetime": "2024-08-17 14:00:00",
         "h": {"title": "Arsenal"}, "a": {"title": "Wolverhampton Wanderers"},
         "goals": {"h": None, "a": None}, "xG": {"h": None, "a": None}},
    ],
}


def test_understat_matches():
    ms = us.parse_matches(UNDERSTAT, "EPL", 2024)
    assert ms[0].home_goals == 1 and ms[0].home_xg == pytest.approx(2.04)
    assert not ms[1].is_result and ms[1].home_xg is None
    p = us.parse_players(UNDERSTAT)[0]
    assert p["mpg"] == pytest.approx(3392 / 38)


def test_current_season():
    assert us.current_season(date(2026, 10, 23)) == 2026
    assert us.current_season(date(2027, 3, 1)) == 2026


CSV = """﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,PSCH,PSCD,PSCA,PC>2.5,PC<2.5,AHCh,PCAHH,PCAHA
E0,16/08/2024,20:00,Man United,Fulham,1,0,1.60,4.20,5.50,1.90,1.95,-0.75,1.93,1.97
E0,17/08/2024,15:00,Ipswich,Liverpool,,,,,,,,,,
"""


def test_football_data_csv():
    rows = fd.parse_csv(CSV)
    assert len(rows) == 1
    r = rows[0]
    assert r.date == date(2024, 8, 16) and r.close_home == 1.60 and r.ah_line == -0.75
    q = fd.devig(r.close_home, r.close_draw, r.close_away)
    assert sum(q) == pytest.approx(1.0)


@pytest.mark.parametrize("name,expected", [
    ("Man United", "Manchester United"),
    ("Nott'm Forest", "Nottingham Forest"),
    ("Ath Madrid", "Atletico Madrid"),
    ("RC Deportivo A Coruña", "Deportivo La Coruna"),
    ("Ipswich Town FC", "Ipswich"),
    ("Deportivo Alavés", "Alaves"),
])
def test_team_names(name, expected):
    assert to_canonical(name, {expected, "Arsenal", "Real Madrid"}) == expected


def test_team_names_token_fallback():
    assert to_canonical("Brentford FC", {"Brentford", "Burnley"}) == "Brentford"
    assert to_canonical("Unknown United", {"Brentford"}) is None


EVENTS = [
    {"id": "1", "slug": "epl-ips-not-2026-10-23", "title": "Ipswich Town FC vs. Nottingham Forest FC",
     "closed": False, "markets": [
        {"sportsMarketType": "moneyline", "groupItemTitle": "Ipswich Town FC",
         "question": "Will Ipswich Town FC win on 2026-10-23?", "outcomePrices": '["0.295","0.705"]',
         "clobTokenIds": '["a","b"]', "gameStartTime": "2026-10-23 19:00:00+00",
         "liquidityNum": 100000},
        {"sportsMarketType": None, "groupItemTitle": "Draw (Ipswich Town FC vs. Nottingham Forest FC)",
         "question": "Will Ipswich Town FC vs. Nottingham Forest FC end in a draw?",
         "outcomePrices": '["0.275","0.725"]', "clobTokenIds": '["c","d"]'},
        {"sportsMarketType": "moneyline", "groupItemTitle": "Nottingham Forest FC",
         "question": "Will Nottingham Forest FC win on 2026-10-23?",
         "outcomePrices": '["0.425","0.575"]', "clobTokenIds": '["e","f"]'},
    ]},
    {"id": "2", "slug": "epl-ips-not-2026-10-23-more-markets", "title": "x", "markets": [
        {"sportsMarketType": "spreads", "question": "Spread: Nottingham Forest FC (-1.5)",
         "line": -1.5, "outcomes": '["Nottingham Forest FC","Ipswich Town FC"]',
         "outcomePrices": '["0.2","0.8"]', "clobTokenIds": '["g","h"]'},
        {"sportsMarketType": "totals", "question": "Ipswich Town FC vs. Nottingham Forest FC: O/U 2.5",
         "line": 2.5, "outcomePrices": '["0.55","0.45"]', "clobTokenIds": '["i","j"]'},
        {"sportsMarketType": "both_teams_to_score", "question": "x: Both Teams to Score",
         "outcomePrices": '["0.6","0.4"]', "clobTokenIds": '["k","l"]'},
    ]},
    {"id": "3", "slug": "epl-ips-not-2026-10-23-total-corners", "title": "y", "markets": []},
]


def test_polymarket_parse():
    [mm] = pm.parse_match_events("EPL", EVENTS)
    assert mm.home == "Ipswich Town FC" and mm.kickoff.hour == 19
    assert mm.get("home_win").yes_price == 0.295
    assert mm.get("draw").yes_price == 0.275  # inferred from question text
    assert mm.get("away_win").yes_price == 0.425
    ah = mm.get("asian_handicap")
    # Forest −1.5 at 0.2 → home (Ipswich) +1.5 at 0.8, YES = Ipswich
    assert ah.line == 1.5 and ah.yes_price == 0.8 and ah.yes_token == "h"
    assert mm.get("total", 2.5).yes_price == 0.55
    assert mm.get("btts").yes_price == 0.6


def test_fpl_parse():
    raw = {
        "teams": [{"id": 1, "name": "Arsenal"}],
        "elements": [
            {"id": 7, "first_name": "Bukayo", "second_name": "Saka", "team": 1, "element_type": 3,
             "status": "d", "chance_of_playing_next_round": 50, "news": "Thigh",
             "minutes": 900, "starts": 10, "expected_goals": "3.0", "expected_assists": "2.0",
             "expected_goals_conceded": "9.0"},
            {"id": 8, "first_name": "Ben", "second_name": "White", "team": 1, "element_type": 2,
             "status": "a", "chance_of_playing_next_round": None, "minutes": 0, "starts": 0},
        ],
    }
    absences, metrics = fpl.parse_players(raw)
    assert absences == [{"player_id": "7", "player_name": "Bukayo Saka", "team": "Arsenal",
                         "status": "QUESTIONABLE", "reason": "Thigh", "position": "MID"}]
    assert metrics["Bukayo Saka"]["XGI90"] == pytest.approx(0.5)
    assert metrics["Bukayo Saka"]["ON_XGA90"] == pytest.approx(0.9)
