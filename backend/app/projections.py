"""
Projection methodology per README section 6.

This is a transparent, explainable heuristic ensemble — NOT dressed up as a
trained ML model, because no real historical data is loaded to train one without
leaking future information. See train_gbm_from_history() for the upgrade path.
"""
from dataclasses import dataclass, field
from typing import Optional
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy import desc
from . import models as m

RECENCY_WEIGHTS = np.array([0.35, 0.25, 0.17, 0.12, 0.07, 0.04])

POSITION_VARIANCE = {"QB": 0.28, "RB": 0.38, "WR": 0.42, "TE": 0.45, "K": 0.35, "DST": 0.40}


@dataclass
class ProjectionResult:
    player_id: int
    median: float
    mean: float
    floor: float
    ceiling: float
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    prob_over_10: float
    prob_over_15: float
    prob_over_20: float
    prob_over_25: float
    volatility_score: float
    consistency_score: float
    opportunity_score: float
    matchup_score: float
    role_security_score: float
    breakout_probability: float
    bust_probability: float
    confidence: str
    assumptions: list
    missing_or_uncertain: list
    samples: Optional[np.ndarray] = field(default=None, repr=False)  # for correlated sims


def _recency_weighted_avg(points: list[float]) -> float:
    pts = points[-6:][::-1]  # most recent first, up to 6
    if not pts:
        return 0.0
    w = RECENCY_WEIGHTS[:len(pts)]
    w = w / w.sum()
    return float(np.dot(pts, w))


def _usage_trend_slope(values: list[float]) -> float:
    vals = [v for v in values[-4:] if v is not None]
    if len(vals) < 2:
        return 0.0
    x = np.arange(len(vals))
    slope = np.polyfit(x, vals, 1)[0]
    return float(slope)


def project_player(db: Session, player_id: int, season: int, week: int,
                    n_sims: int = 2000) -> ProjectionResult:
    player = db.query(m.Player).get(player_id)
    assumptions = []
    missing = []

    history = (db.query(m.WeeklyStat)
               .filter(m.WeeklyStat.player_id == player_id, m.WeeklyStat.season <= season)
               .filter(~((m.WeeklyStat.season == season) & (m.WeeklyStat.week >= week)))
               .order_by(m.WeeklyStat.season, m.WeeklyStat.week)
               .all())

    if not history:
        missing.append("No historical weekly stats found for this player; using positional replacement baseline.")
        base_by_pos = {"QB": 14, "RB": 8, "WR": 7, "TE": 5, "K": 6, "DST": 6}
        base = base_by_pos.get(player.position, 6)
        pts_hist = [base] * 3
        target_hist, carry_hist = [0.0] * 3, [0.0] * 3
    else:
        pts_hist = [h.fantasy_points_ppr or 0.0 for h in history]
        target_hist = [h.target_share for h in history if h.target_share is not None]
        carry_hist = [h.rush_share for h in history if h.rush_share is not None]
        base = _recency_weighted_avg(pts_hist)

    # 1. base rate (already computed above from recency-weighted avg, or replacement baseline)
    # 2. usage trend adjustment
    usage_slope = 0.0
    if target_hist:
        usage_slope += _usage_trend_slope(target_hist) * 20
    if carry_hist:
        usage_slope += _usage_trend_slope(carry_hist) * 15
    if not target_hist and not carry_hist:
        missing.append("No target/carry share history available; usage-trend adjustment set to neutral.")

    # 3. opponent adjustment: current game's implied total vs league-average ~22.5
    game = (db.query(m.Game)
            .filter(m.Game.season == season, m.Game.week == week)
            .filter((m.Game.home_team_id == player.nfl_team_id) | (m.Game.away_team_id == player.nfl_team_id))
            .first())
    if game:
        implied = game.home_implied_total if game.home_team_id == player.nfl_team_id else game.away_implied_total
        market_factor = implied / 22.5
        assumptions.append(f"Team implied total for this matchup: {implied} points (market_factor={market_factor:.2f}).")
    else:
        market_factor = 1.0
        missing.append("No scheduled game / betting-market data found for this week; market adjustment set to neutral (1.0x).")

    # 4. injury status dampener
    injury_factor = 1.0
    if player.status in ("Questionable",):
        injury_factor = 0.85
        assumptions.append("Player listed Questionable; applying a 15% downside dampener to the median.")
    elif player.status in ("Doubtful",):
        injury_factor = 0.4
        assumptions.append("Player listed Doubtful; applying a 60% downside dampener to the median.")
    elif player.status in ("Out", "IR", "Suspended"):
        injury_factor = 0.02
        assumptions.append(f"Player listed {player.status}; projection reflects near-zero expected involvement.")

    adj_median = max(0.1, (base + usage_slope) * market_factor * injury_factor)

    # Monte Carlo distribution: lognormal-ish shape for skewed fantasy scoring
    var_coef = POSITION_VARIANCE.get(player.position, 0.4)
    sigma = np.sqrt(np.log(1 + var_coef ** 2))
    mu = np.log(max(adj_median, 0.1)) - 0.5 * sigma ** 2
    samples = np.random.lognormal(mean=mu, sigma=sigma, size=n_sims)
    if injury_factor < 1.0:
        # additional chance of a total-zero outcome (leaves game / inactive)
        zero_mask = np.random.random(n_sims) < (1 - injury_factor) * 0.5
        samples[zero_mask] = 0.0

    p10, p25, p50, p75, p90 = np.percentile(samples, [10, 25, 50, 75, 90])
    mean_val = float(np.mean(samples))
    consistency_score = float(max(0, 1 - (np.std(samples) / max(mean_val, 0.1))))
    volatility_score = float(1 - consistency_score)

    role_security_score = 1.0
    if player.depth_chart_rank and player.depth_chart_rank > 1:
        role_security_score = max(0.1, 1 - 0.25 * (player.depth_chart_rank - 1))
        assumptions.append(f"Depth-chart rank {player.depth_chart_rank} at {player.position}; role-security reduced accordingly.")

    breakout_probability = float(np.clip(0.5 * max(0, usage_slope) / 5 + 0.05, 0, 0.6))
    bust_probability = float(np.clip(0.3 + (1 - role_security_score) * 0.3 + (1 - injury_factor) * 0.3, 0, 0.9))

    n_missing = len(missing)
    if n_missing == 0 and len(history) >= 6:
        confidence = "High"
    elif n_missing == 0:
        confidence = "Moderate"
    elif n_missing == 1:
        confidence = "Low"
    else:
        confidence = "Very Low"

    return ProjectionResult(
        player_id=player_id,
        median=round(float(p50), 2), mean=round(mean_val, 2),
        floor=round(float(p10), 2), ceiling=round(float(p90), 2),
        p10=round(float(p10), 2), p25=round(float(p25), 2), p50=round(float(p50), 2),
        p75=round(float(p75), 2), p90=round(float(p90), 2),
        prob_over_10=round(float(np.mean(samples >= 10)), 3),
        prob_over_15=round(float(np.mean(samples >= 15)), 3),
        prob_over_20=round(float(np.mean(samples >= 20)), 3),
        prob_over_25=round(float(np.mean(samples >= 25)), 3),
        volatility_score=round(volatility_score, 3),
        consistency_score=round(consistency_score, 3),
        opportunity_score=round(float(np.clip(0.5 + usage_slope / 10, 0, 1)), 3),
        matchup_score=round(float(np.clip(market_factor - 0.5, 0, 1.5) / 1.5), 3),
        role_security_score=round(role_security_score, 3),
        breakout_probability=round(breakout_probability, 3),
        bust_probability=round(bust_probability, 3),
        confidence=confidence,
        assumptions=assumptions or ["Standard ensemble applied with no unusual overrides."],
        missing_or_uncertain=missing or ["None identified for this player-week."],
        samples=samples,
    )


def explain_projection(player: m.Player, proj: ProjectionResult, opponent_note: str = "") -> str:
    return (
        f"{player.full_name} ({player.position}, {player.status}): median {proj.median} PPR pts "
        f"(floor {proj.floor} / ceiling {proj.ceiling}), {int(proj.prob_over_15 * 100)}% probability of "
        f"15+ points. Role security {int(proj.role_security_score * 100)}%, opportunity score "
        f"{int(proj.opportunity_score * 100)}%. {opponent_note} Confidence: {proj.confidence}."
    )


def train_gbm_from_history():
    """
    Upgrade hook: once real multi-season nflverse data is loaded, replace the
    base-rate/usage/opponent blend above with a walk-forward-validated
    LightGBM/XGBoost regressor here. Keep the Monte Carlo distribution step —
    predict a residual-variance model alongside the point estimate so
    percentile outputs remain honest rather than a single fixed-width band.
    Not implemented in the MVP: no real historical data is loaded yet, and
    training on mock data would produce a model that looks real but isn't.
    """
    raise NotImplementedError("Wire real historical data before enabling model training.")
