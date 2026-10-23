"""
Cross-league team strengths — one Poisson model over every domestic league
and every UEFA match, so a Portuguese side and a Premier League side share a
scale.

  log λ_home = μ + HA[comp] + A[L(h)] + α[h] − D[L(a)] − δ[a]
  log λ_away = μ            + A[L(a)] + α[a] − D[L(h)] − δ[h]

  A[L], D[L]: league attack / defence (identified only through European
              matches between leagues)
  α, δ:       team deviations from their league
  HA[comp]:   home advantage per competition (each league, UEFA separately);
              zero at neutral venues

Fitted by weighted maximum likelihood with L2 shrinkage (team deviations →
league, league effects → 0), weights decaying with match age.  The Maher /
Dixon-Coles family, with a league layer so thinly-linked leagues borrow
strength.  Goals only — xG is not available for most of these leagues.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from scipy.optimize import minimize

from app.connectors.espn import EUROPEAN_COMPETITIONS, EspnMatch

OTHER_LEAGUE = "other"   # teams seen only in UEFA matches (leagues ESPN lacks)
EURO_GROUP = "uefa"


@dataclass
class GlobalConfig:
    # Tuned by scripts/ucl_backtest.py on 2024-25 UCL (walk-forward 1X2 log loss).
    half_life_days: float = 365.0
    team_ridge: float = 2.0       # weighted-match equivalents pulling α, δ → 0
    league_ridge: float = 0.3
    euro_weight: float = 1.0      # European matches weighted like domestic ones
    window_days: int = 1100
    min_weight: float = 0.02


@dataclass
class GlobalRatings:
    as_of: date
    mu: float
    home_adv: dict[str, float]
    league_att: dict[str, float]
    league_def: dict[str, float]
    team_att: dict[str, float]                 # α (team deviation)
    team_def: dict[str, float]                 # δ
    team_league: dict[str, str]
    team_name: dict[str, str] = field(default_factory=dict)
    team_matches: dict[str, float] = field(default_factory=dict)  # weighted matches

    def attack(self, team: str) -> float:
        lg = self.team_league.get(team, OTHER_LEAGUE)
        return self.league_att.get(lg, 0.0) + self.team_att.get(team, 0.0)

    def defence(self, team: str) -> float:
        lg = self.team_league.get(team, OTHER_LEAGUE)
        return self.league_def.get(lg, 0.0) + self.team_def.get(team, 0.0)

    def expected_goals(
        self, home: str, away: str, competition: str = EURO_GROUP, neutral: bool = False,
    ) -> tuple[float, float]:
        ha = 0.0 if neutral else self.home_adv.get(competition, self.home_adv.get(EURO_GROUP, 0.2))
        lh = math.exp(self.mu + ha + self.attack(home) - self.defence(away))
        la = math.exp(self.mu + self.attack(away) - self.defence(home))
        return lh, la

    def strength(self, team: str) -> float:
        """Net log-strength (attack + defence); handy for ranking."""
        return self.attack(team) + self.defence(team)


def _comp_group(m: EspnMatch) -> str:
    return EURO_GROUP if m.competition in EUROPEAN_COMPETITIONS else m.competition


def _team_leagues(matches: list[EspnMatch]) -> dict[str, str]:
    """Each team's most recent domestic league; UEFA-only teams → OTHER_LEAGUE."""
    league: dict[str, str] = {}
    for m in sorted(matches, key=lambda x: x.kickoff):
        if m.competition not in EUROPEAN_COMPETITIONS:
            league[m.home_id] = m.competition
            league[m.away_id] = m.competition
    for m in matches:
        for t in (m.home_id, m.away_id):
            league.setdefault(t, OTHER_LEAGUE)
    return league


def fit_global(
    matches: list[EspnMatch],
    as_of: date,
    cfg: GlobalConfig | None = None,
    warm_start: GlobalRatings | None = None,
) -> GlobalRatings:
    """Fit on results strictly before ``as_of`` (no look-ahead)."""
    cfg = cfg or GlobalConfig()
    played = [
        m for m in matches
        if m.is_result and m.kickoff.date() < as_of
        and (as_of - m.kickoff.date()).days <= cfg.window_days
    ]
    if not played:
        raise ValueError("no matches to fit")
    team_league = _team_leagues(played)
    teams = sorted(team_league)
    leagues = sorted(set(team_league.values()))
    groups = sorted({_comp_group(m) for m in played})
    ti = {t: i for i, t in enumerate(teams)}
    li = {lg: i for i, lg in enumerate(leagues)}
    gi = {g: i for i, g in enumerate(groups)}
    n_t, n_l, n_g = len(teams), len(leagues), len(groups)

    h = np.array([ti[m.home_id] for m in played])
    a = np.array([ti[m.away_id] for m in played])
    lh_idx = np.array([li[team_league[m.home_id]] for m in played])
    la_idx = np.array([li[team_league[m.away_id]] for m in played])
    g = np.array([gi[_comp_group(m)] for m in played])
    home_flag = np.array([0.0 if m.neutral else 1.0 for m in played])
    yh = np.array([float(m.home_goals or 0) for m in played])
    ya = np.array([float(m.away_goals or 0) for m in played])
    age = np.array([(as_of - m.kickoff.date()).days for m in played], dtype=float)
    w = np.power(0.5, age / cfg.half_life_days)
    w *= np.array([cfg.euro_weight if m.is_european else 1.0 for m in played])
    keep = w >= cfg.min_weight
    h, a, lh_idx, la_idx, g, home_flag, yh, ya, w = (
        x[keep] for x in (h, a, lh_idx, la_idx, g, home_flag, yh, ya, w)
    )

    # θ = [μ, HA(n_g), A(n_l), D(n_l), α(n_t), δ(n_t)]
    o_ha, o_att = 1, 1 + n_g
    o_def, o_al = o_att + n_l, o_att + 2 * n_l
    o_de = o_al + n_t
    n_par = o_de + n_t

    def unpack(th: np.ndarray):  # type: ignore[no-untyped-def]
        return (th[0], th[o_ha:o_att], th[o_att:o_def], th[o_def:o_al], th[o_al:o_de], th[o_de:])

    def nll(th: np.ndarray) -> tuple[float, np.ndarray]:
        mu, ha, lg_a, lg_d, al, de = unpack(th)
        eta_h = mu + ha[g] * home_flag + lg_a[lh_idx] + al[h] - lg_d[la_idx] - de[a]
        eta_a = mu + lg_a[la_idx] + al[a] - lg_d[lh_idx] - de[h]
        lam_h, lam_a = np.exp(eta_h), np.exp(eta_a)
        f = float(np.sum(w * (lam_h - yh * eta_h)) + np.sum(w * (lam_a - ya * eta_a)))
        f += 0.5 * cfg.team_ridge * float(al @ al + de @ de)
        f += 0.5 * cfg.league_ridge * float(lg_a @ lg_a + lg_d @ lg_d)
        rh, ra = w * (lam_h - yh), w * (lam_a - ya)   # d/dη
        grad = np.zeros(n_par)
        grad[0] = rh.sum() + ra.sum()
        np.add.at(grad, o_ha + g, rh * home_flag)
        np.add.at(grad, o_att + lh_idx, rh)
        np.add.at(grad, o_att + la_idx, ra)
        np.add.at(grad, o_def + la_idx, -rh)
        np.add.at(grad, o_def + lh_idx, -ra)
        np.add.at(grad, o_al + h, rh)
        np.add.at(grad, o_al + a, ra)
        np.add.at(grad, o_de + a, -rh)
        np.add.at(grad, o_de + h, -ra)
        grad[o_al:o_de] += cfg.team_ridge * al
        grad[o_de:] += cfg.team_ridge * de
        grad[o_att:o_def] += cfg.league_ridge * lg_a
        grad[o_def:o_al] += cfg.league_ridge * lg_d
        return f, grad

    th0 = np.zeros(n_par)
    th0[0] = math.log(max(float((yh * w).sum() + (ya * w).sum()) / (2 * w.sum()), 0.1))
    th0[o_ha:o_att] = 0.2
    if warm_start is not None:
        th0[0] = warm_start.mu
        for grp, i in gi.items():
            th0[o_ha + i] = warm_start.home_adv.get(grp, 0.2)
        for lg, i in li.items():
            th0[o_att + i] = warm_start.league_att.get(lg, 0.0)
            th0[o_def + i] = warm_start.league_def.get(lg, 0.0)
        for t, i in ti.items():
            th0[o_al + i] = warm_start.team_att.get(t, 0.0)
            th0[o_de + i] = warm_start.team_def.get(t, 0.0)

    res = minimize(nll, th0, jac=True, method="L-BFGS-B", options={"maxiter": 2000})
    mu, ha, lg_a, lg_d, al, de = unpack(res.x)

    names: dict[str, str] = {}
    for m in played:
        names[m.home_id], names[m.away_id] = m.home_name, m.away_name
    weight_by_team: Counter[str] = Counter()
    for hi_, ai_, wi in zip(h, a, w, strict=True):
        weight_by_team[teams[hi_]] += float(wi)
        weight_by_team[teams[ai_]] += float(wi)

    return GlobalRatings(
        as_of=as_of,
        mu=float(mu),
        home_adv={grp: float(ha[i]) for grp, i in gi.items()},
        league_att={lg: float(lg_a[i]) for lg, i in li.items()},
        league_def={lg: float(lg_d[i]) for lg, i in li.items()},
        team_att={t: float(al[i]) for t, i in ti.items()},
        team_def={t: float(de[i]) for t, i in ti.items()},
        team_league=team_league,
        team_name=names,
        team_matches=dict(weight_by_team),
    )
