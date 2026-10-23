"""Team ratings as of a date — the football version of bk8's rating fetch +
season blend.

For a match on date d in season s:
  current: season-s matches played before d (no look-ahead)
  prior:   season s−1 full-season rating, or a promoted-team prior
  blend:   w = gp / (gp + K)  (season_blend.blend_weight)

Per-match value = xg_weight × xG + (1 − xg_weight) × goals, so the backtest
can decide how much actual goals add on top of xG.  Optional opponent
adjustment rescales each match by the opponent's strength (an early run of
fixtures against the top six otherwise looks like a bad defence).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.analytics.season_blend import blend_weight
from app.connectors.understat import MatchRecord


@dataclass
class RatingConfig:
    # Defaults tuned by scripts/tune.py on EPL + La Liga 2018-21 (walk-forward
    # 1X2 + O/U 2.5 log loss), checked on held-out 2022-25.
    k_blend: float = 16.0              # matches at which current season = 50%
    xg_weight: float = 0.7             # 1.0 = pure xG, 0.0 = pure goals
    promoted_xgd: float = -0.65        # promoted side vs league average, xGD/90
    promoted_split: float = 0.45       # share of the deficit on attack (rest on defence)
    prior_regression: float = 0.0      # extra pull of last season toward average
    opponent_adjust: bool = False      # tuned off: no gain once K is large
    opponent_iterations: int = 4
    league_level_k: float = 60.0       # matches at which current goal level = 50%


@dataclass
class LeagueRatings:
    """Ratings for every team in a league on one date."""
    as_of: date
    teams: dict[str, dict[str, float]] = field(default_factory=dict)
    league_xg: float = 1.375
    baseline_goals: float = 2.75
    home_advantage: float = 0.30
    level_weight: float = 0.0


def _value(xg: float | None, goals: int | None, w: float) -> float:
    if xg is None:
        return float(goals or 0)
    return w * xg + (1.0 - w) * float(goals or 0)


def _season_rows(
    matches: list[MatchRecord], cfg: RatingConfig,
) -> dict[str, list[tuple[float, float, str]]]:
    """team → [(for, against, opponent)] for played matches."""
    rows: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for m in matches:
        if not m.is_result:
            continue
        hv = _value(m.home_xg, m.home_goals, cfg.xg_weight)
        av = _value(m.away_xg, m.away_goals, cfg.xg_weight)
        rows[m.home].append((hv, av, m.away))
        rows[m.away].append((av, hv, m.home))
    return rows


def team_strengths(
    matches: list[MatchRecord], cfg: RatingConfig,
) -> tuple[dict[str, dict[str, float]], float]:
    """Per-team {xgf, xga, gp} per match from ``matches`` and the league mean.

    With opponent adjustment, each match's xG for is divided by the
    opponent's defensive factor (xga / mean) and xG against by the opponent's
    attacking factor, iterated a few times (a small SRS-style fixed point).
    """
    rows = _season_rows(matches, cfg)
    if not rows:
        return {}, 0.0
    raw = {
        t: {
            "xgf": sum(r[0] for r in rs) / len(rs),
            "xga": sum(r[1] for r in rs) / len(rs),
            "gp": float(len(rs)),
        }
        for t, rs in rows.items()
    }
    total_gp = sum(r["gp"] for r in raw.values())
    mean = sum(r["xgf"] * r["gp"] for r in raw.values()) / total_gp
    if not cfg.opponent_adjust or mean <= 0:
        return raw, mean

    adj = {t: dict(r) for t, r in raw.items()}
    for _ in range(cfg.opponent_iterations):
        new = {}
        for t, rs in rows.items():
            f = a = 0.0
            for v_for, v_against, opp in rs:
                o = adj.get(opp)
                def_factor = (o["xga"] / mean) if o else 1.0
                att_factor = (o["xgf"] / mean) if o else 1.0
                f += v_for / max(def_factor, 0.3)
                a += v_against / max(att_factor, 0.3)
            new[t] = {"xgf": f / len(rs), "xga": a / len(rs), "gp": float(len(rs))}
        # re-centre so the league mean is unchanged
        m_f = sum(r["xgf"] * r["gp"] for r in new.values()) / total_gp
        m_a = sum(r["xga"] * r["gp"] for r in new.values()) / total_gp
        for r in new.values():
            r["xgf"] *= mean / m_f
            r["xga"] *= mean / m_a
        adj = new
    return adj, mean


def _goal_level(matches: list[MatchRecord]) -> tuple[float, float, float] | None:
    played = [m for m in matches if m.is_result]
    if not played:
        return None
    hg = sum(m.home_goals or 0 for m in played)
    ag = sum(m.away_goals or 0 for m in played)
    n = len(played)
    return (hg + ag) / n, (hg - ag) / n, float(n)


def build_ratings(
    current_season: list[MatchRecord],
    prior_season: list[MatchRecord],
    as_of: date,
    cfg: RatingConfig | None = None,
    teams: set[str] | None = None,
) -> LeagueRatings:
    """Ratings for each team in ``current_season`` using matches before ``as_of``."""
    cfg = cfg or RatingConfig()
    teams = teams or {m.home for m in current_season} | {m.away for m in current_season}
    before = [m for m in current_season if m.date < as_of]

    cur, _ = team_strengths(before, cfg)
    prior, prior_mean = team_strengths(prior_season, cfg)
    prior_mean = prior_mean or 1.375

    out = LeagueRatings(as_of=as_of)
    for t in sorted(teams):
        p = prior.get(t)
        if p is not None:
            r = cfg.prior_regression
            p_xgf = (1 - r) * p["xgf"] + r * prior_mean
            p_xga = (1 - r) * p["xga"] + r * prior_mean
            source = "prior"
        else:
            p_xgf = prior_mean + cfg.promoted_xgd * cfg.promoted_split
            p_xga = prior_mean - cfg.promoted_xgd * (1 - cfg.promoted_split)
            source = "promoted"
        c = cur.get(t)
        gp = c["gp"] if c else 0.0
        w = blend_weight(gp, cfg.k_blend)
        xgf = w * c["xgf"] + (1 - w) * p_xgf if c else p_xgf
        xga = w * c["xga"] + (1 - w) * p_xga if c else p_xga
        out.teams[t] = {
            "xgf": round(xgf, 4), "xga": round(xga, 4), "xgd": round(xgf - xga, 4),
            "gp": gp, "current_weight": round(w, 3),
            "prior_source": 0.0 if source == "prior" else 1.0,
        }

    vals = [v for r in out.teams.values() for v in (r["xgf"], r["xga"])]
    out.league_xg = sum(vals) / len(vals) if vals else 1.375

    prior_lvl = _goal_level(prior_season) or (2.75, 0.30, 0.0)
    cur_lvl = _goal_level(before)
    if cur_lvl:
        w = cur_lvl[2] / (cur_lvl[2] + cfg.league_level_k)
        out.baseline_goals = w * cur_lvl[0] + (1 - w) * prior_lvl[0]
        out.home_advantage = w * cur_lvl[1] + (1 - w) * prior_lvl[1]
        out.level_weight = round(w, 3)
    else:
        out.baseline_goals, out.home_advantage = prior_lvl[0], prior_lvl[1]
    return out
