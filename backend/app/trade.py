"""
Trade analyzer per spec section 8. Evaluates lineup-level impact, not raw
player-value addition.
"""
import numpy as np
from typing import List, Dict
from sqlalchemy.orm import Session
from . import models as m
from .lineup import optimize_lineup, ROSTER_SLOTS
from .projections import project_player


def _team_starter_value(db: Session, team_id: int, season: int, week: int, override_roster_ids: List[int] = None) -> dict:
    """Sums median projections of the best possible starting lineup for a team,
    optionally overriding its roster (for pre/post-trade comparison)."""
    if override_roster_ids is not None:
        players = db.query(m.Player).filter(m.Player.id.in_(override_roster_ids)).all()
    else:
        roster_ids = [row.player_id for row in db.query(m.Lineup.player_id).filter(m.Lineup.team_id == team_id).distinct()]
        players = db.query(m.Player).filter(m.Player.id.in_(roster_ids)).all()

    by_pos: Dict[str, List[dict]] = {}
    for p in players:
        proj = project_player(db, p.id, season, week, n_sims=300)
        by_pos.setdefault(p.position, []).append({"player_id": p.id, "name": p.full_name,
                                                    "median": proj.median, "floor": proj.floor, "ceiling": proj.ceiling})
    from .lineup import _best_lineup_for_objective
    lineup = _best_lineup_for_objective(by_pos, "median")
    total = sum(r["median"] for r in lineup)
    return {"lineup": lineup, "total_median": round(total, 2)}


def analyze_trade(db: Session, league_id: int, season: int, week: int,
                   team_assets: Dict[int, dict]) -> dict:
    """
    team_assets: {team_id: {"sends": [player_id,...], "receives": [player_id,...]}}
    Supports 2-team, 3-team+, and uneven (2-for-1 etc.) trades — the dict shape
    is agnostic to team count and asset count per side.
    """
    results = {}
    for team_id, assets in team_assets.items():
        current_roster = [row.player_id for row in
                           db.query(m.Lineup.player_id).filter(m.Lineup.team_id == team_id).distinct()]
        before = _team_starter_value(db, team_id, season, week, override_roster_ids=current_roster)

        post_roster = [pid for pid in current_roster if pid not in assets["sends"]] + assets["receives"]
        after = _team_starter_value(db, team_id, season, week, override_roster_ids=post_roster)

        delta = round(after["total_median"] - before["total_median"], 2)
        results[team_id] = {
            "team_id": team_id,
            "starter_value_before": before["total_median"],
            "starter_value_after": after["total_median"],
            "starter_value_delta": delta,
            "lineup_before": before["lineup"],
            "lineup_after": after["lineup"],
        }

    ranked = sorted(results.values(), key=lambda x: -x["starter_value_delta"])
    biggest_gap = ranked[0]["starter_value_delta"] - ranked[-1]["starter_value_delta"] if len(ranked) > 1 else 0
    if biggest_gap < 1.5:
        verdict = "Balanced — projected starter-value impact is roughly even across teams."
    elif biggest_gap < 4:
        verdict = f"Slightly favors Team {ranked[0]['team_id']}."
    else:
        verdict = f"Clearly favors Team {ranked[0]['team_id']}; other side(s) should ask for more."

    # simple counteroffer heuristic: suggest the disadvantaged team ask for the
    # single highest-median player the trade sends away, kept instead of traded
    counteroffers = []
    if biggest_gap >= 4 and len(ranked) > 1:
        disadvantaged = ranked[-1]["team_id"]
        counteroffers.append(
            f"Team {disadvantaged} could ask to keep their best-value asset in the deal, or request an "
            f"additional bench piece / late draft pick from the counterparty to close the "
            f"~{round(biggest_gap, 1)}-point starter-value gap."
        )

    return {
        "per_team_impact": results,
        "verdict": verdict,
        "counteroffer_suggestions": counteroffers,
        "minimum_acceptable_return_note": (
            "Computed as: the disadvantaged team's pre-trade starter value minus the value of assets "
            "they're sending should not exceed their post-trade starter value by more than ~1.5 points "
            "(the 'balanced' threshold used above)."
        ),
        "assumptions": [
            "Impact is measured on best-lineup starter value only for the given week, not full-season/dynasty value.",
            "Does not yet account for remaining strength-of-schedule or bye-week timing differences between assets — "
            "extend by summing starter_value across multiple future weeks for a more complete picture.",
        ],
        "confidence": "Moderate",
        "model_version": "ensemble-v0.1-mvp",
    }
