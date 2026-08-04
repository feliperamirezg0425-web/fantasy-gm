"""
Waiver-wire assistant per README section (spec section 7).
"""
import numpy as np
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from . import models as m
from .projections import project_player


def _rest_of_season_value(db: Session, player_id: int, season: int, from_week: int, to_week: int) -> float:
    total = 0.0
    for wk in range(from_week, to_week + 1):
        proj = project_player(db, player_id, season, wk, n_sims=300)
        total += proj.median
    return round(total, 2)


def recommend_waiver_adds(db: Session, team_id: int, league_id: int, season: int, week: int,
                           free_agent_player_ids: List[int], faab_remaining: int,
                           roster_needs: Optional[List[str]] = None,
                           playoff_weeks: Optional[List[int]] = None) -> dict:
    roster_needs = roster_needs or []
    playoff_weeks = playoff_weeks or [15, 16, 17]
    candidates = db.query(m.Player).filter(m.Player.id.in_(free_agent_player_ids)).all()

    results = []
    for p in candidates:
        proj = project_player(db, p.id, season, week, n_sims=500)
        ros_value = _rest_of_season_value(db, p.id, season, week, min(week + 5, 17))
        playoff_value = 0.0
        for wk in playoff_weeks:
            playoff_value += project_player(db, p.id, season, wk, n_sims=200).median

        # crude "how likely is another manager to claim this player" proxy:
        # higher projected value + being a clear need for many rosters -> more contested
        contested_score = np.clip((proj.median - 6) / 10, 0.05, 0.95)
        need_boost = 0.15 if p.position in roster_needs else 0.0

        suggested_faab_pct = float(np.clip(0.05 + contested_score * 0.35 + need_boost, 0.02, 0.9))
        results.append({
            "player_id": p.id, "name": p.full_name, "position": p.position, "status": p.status,
            "current_week_projection": proj.median,
            "rest_of_season_value": ros_value,
            "playoff_weeks_value": round(playoff_value, 2),
            "role_security_score": proj.role_security_score,
            "breakout_probability": proj.breakout_probability,
            "estimated_claim_probability_by_others": round(float(contested_score), 3),
            "suggested_faab_bid": int(round(suggested_faab_pct * faab_remaining)),
            "aggressive_faab_bid": int(round(min(0.95, suggested_faab_pct * 1.6) * faab_remaining)),
            "conservative_faab_bid": int(round(max(0.01, suggested_faab_pct * 0.5) * faab_remaining)),
            "confidence": proj.confidence,
        })

    results.sort(key=lambda x: -x["rest_of_season_value"])

    # drop candidates: current roster players compared against best available replacement
    roster_ids = [row.player_id for row in db.query(m.Lineup.player_id).filter(m.Lineup.team_id == team_id).distinct()]
    roster_players = db.query(m.Player).filter(m.Player.id.in_(roster_ids)).all()
    drop_candidates = []
    for p in roster_players:
        ros_value = _rest_of_season_value(db, p.id, season, week, min(week + 5, 17))
        same_pos_best_fa = max([r for r in results if r["position"] == p.position],
                                key=lambda x: x["rest_of_season_value"], default=None)
        if same_pos_best_fa and same_pos_best_fa["rest_of_season_value"] > ros_value:
            drop_candidates.append({
                "player_id": p.id, "name": p.full_name, "position": p.position,
                "rest_of_season_value": ros_value,
                "better_alternative_available": same_pos_best_fa["name"],
                "alternative_ros_value": same_pos_best_fa["rest_of_season_value"],
            })
    drop_candidates.sort(key=lambda x: x["rest_of_season_value"])

    return {
        "recommended_adds": results[:15],
        "drop_candidates": drop_candidates[:10],
        "use_number_one_priority_recommended": bool(results and results[0]["estimated_claim_probability_by_others"] > 0.5),
        "assumptions": [
            "Claim probability and FAAB suggestions are heuristic proxies (projection level + roster-need overlap), "
            "not learned from real historical league FAAB data.",
            "Rest-of-season value sums the next 6 weeks of median projections; playoff value sums configured playoff weeks.",
        ],
        "confidence": "Low",
    }
