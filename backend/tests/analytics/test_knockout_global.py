"""Tests for knockout-tie pricing and the cross-league global model."""

import random
from datetime import UTC, date, datetime, timedelta

import pytest

from app.analytics.global_ratings import OTHER_LEAGUE, GlobalConfig, fit_global
from app.analytics.knockout import live_second_leg, single_match_advance, two_leg_tie
from app.connectors.espn import EspnMatch


class TestKnockout:
    def test_equal_teams_coin_flip(self):
        r = two_leg_tie((1.4, 1.4), (1.4, 1.4))
        assert r["advance"] == pytest.approx(0.5, abs=1e-6)
        assert r["win_in_regulation"] + r["level_after_90"] < 1.0

    def test_stronger_team_advances_more_than_it_wins_a_match(self):
        tie = two_leg_tie((1.8, 1.0), (1.2, 1.5))
        assert tie["advance"] > 0.6
        parts = tie["win_in_regulation"] + tie["win_in_extra_time"]
        assert parts < tie["advance"] < parts + tie["penalties"] + 1e-9

    def test_first_leg_result_matters(self):
        up = two_leg_tie((1.4, 1.4), (1.4, 1.4), leg1_score=(3, 0))
        down = two_leg_tie((1.4, 1.4), (1.4, 1.4), leg1_score=(0, 3))
        assert up["advance"] > 0.95 and down["advance"] < 0.05

    def test_final_includes_extra_time(self):
        f = single_match_advance(1.3, 1.3)
        assert f["advance"] == pytest.approx(0.5, abs=1e-6)
        assert f["penalties"] > 0.05

    def test_live_second_leg(self):
        assert live_second_leg((1.4, 1.4), 1, 89) > 0.95
        assert live_second_leg((1.4, 1.4), 0, 0) == pytest.approx(0.5, abs=1e-6)
        assert live_second_leg((1.4, 1.4), -2, 80) < 0.03


def _synthetic(seed=1):
    """League 'strong' teams are 0.5 log-goals better than league 'weak'."""
    rng = random.Random(seed)
    import math
    strength = {f"S{i}": 0.5 + 0.1 * (i % 3) for i in range(6)}
    strength |= {f"W{i}": 0.0 + 0.1 * (i % 3) for i in range(6)}
    league = {t: ("strong" if t[0] == "S" else "weak") for t in strength}
    league["X1"] = None  # UEFA-only team
    strength["X1"] = 0.2
    matches, k = [], 0
    start = datetime(2025, 8, 1, tzinfo=UTC)

    def poisson(lam):
        limit, p, n = math.exp(-lam), 1.0, 0
        while True:
            p *= rng.random()
            if p < limit:
                return n
            n += 1

    def add(h, a, comp, day):
        nonlocal k
        k += 1
        lh = math.exp(0.25 + 0.2 + strength[h] - strength[a])
        la = math.exp(0.25 + strength[a] - strength[h])
        matches.append(EspnMatch(comp, str(k), start + timedelta(days=day), h, h, a, a,
                                 poisson(lh), poisson(la), False, "STATUS_FULL_TIME"))

    for day in range(0, 300, 3):
        for lg in ("strong", "weak"):
            ts = [t for t in league if league[t] == lg]
            h, a = rng.sample(ts, 2)
            add(h, a, "eng.1" if lg == "strong" else "por.1", day)
        s, w = rng.choice([t for t in league if league[t] == "strong"]), \
            rng.choice([t for t in league if league[t] == "weak"])
        add(s, w, "uefa.champions", day + 1) if day % 2 else add(w, s, "uefa.europa", day + 1)
        if day % 9 == 0:
            add("X1", rng.choice(list(league)[:12]), "uefa.europa.conf", day + 2)
    return matches


def test_global_model_recovers_league_gap():
    ms = _synthetic()
    r = fit_global(ms, date(2026, 7, 1), GlobalConfig(half_life_days=10000))
    gap = r.league_att["eng.1"] + r.league_def["eng.1"] - r.league_att["por.1"] - r.league_def["por.1"]
    assert gap == pytest.approx(1.0, abs=0.35)  # 0.5 attack + 0.5 defence
    assert r.team_league["X1"] == OTHER_LEAGUE
    lh, la = r.expected_goals("S0", "W0")
    assert lh > la


def test_global_model_no_lookahead():
    ms = _synthetic()
    cutoff = date(2025, 9, 1)
    r = fit_global(ms, cutoff)
    n_before = sum(1 for m in ms if m.kickoff.date() < cutoff)
    assert sum(r.team_matches.values()) / 2 <= n_before * 1.5 + 1e-6
