"""
Lineup optimizer per README section 6 / spec section 6.
"""
import itertools
import numpy as np
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from . import models as m
from .projections import project_player, ProjectionResult
from .simulation import simulate_lineup_score, matchup_win_probability, DEFAULT_SIMS

ROSTER_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}


def _best_lineup_for_objective(players_by_pos: Dict[str, List[dict]], objective: str) -> List[dict]:
    """
    objective: 'median' (favorite -> prefer floor/consistency) or
               'ceiling' (underdog -> prefer upside)
    Greedy fill by required slots, then best remaining FLEX-eligible player.
    Greedy is adequate here because roster sizes are small (~15) and slot
    constraints are simple; an ILP solver is the natural upgrade for exotic
    scoring systems / superflex.
    """
    key = "floor" if objective == "safe" else ("ceiling" if objective == "upside" else "median")
    lineup = []
    used_ids = set()
    for pos, count in [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1), ("DST", 1), ("K", 1)]:
        pool = sorted([p for p in players_by_pos.get(pos, []) if p["player_id"] not in used_ids],
                       key=lambda x: -x[key])
        for slot_i in range(count):
            if slot_i < len(pool):
                chosen = pool[slot_i]
                lineup.append({**chosen, "slot": f"{pos}{slot_i + 1}" if count > 1 else pos})
                used_ids.add(chosen["player_id"])
    flex_pool = sorted(
        [p for pos in FLEX_ELIGIBLE for p in players_by_pos.get(pos, []) if p["player_id"] not in used_ids],
        key=lambda x: -x[key]
    )
    if flex_pool:
        lineup.append({**flex_pool[0], "slot": "FLEX"})
        used_ids.add(flex_pool[0]["player_id"])
    return lineup


def optimize_lineup(db: Session, team_id: int, season: int, week: int,
                     opponent_team_id: Optional[int] = None, n_sims: int = DEFAULT_SIMS) -> dict:
    roster_player_ids = [row.player_id for row in
                          db.query(m.Lineup.player_id).filter(m.Lineup.team_id == team_id).distinct()]
    if not roster_player_ids:
        # fall back to any players rostered via a transaction/draft pick for this team, else empty
        roster_player_ids = [row.player_id for row in
                              db.query(m.DraftPick.player_id).filter(m.DraftPick.team_id == team_id,
                                                                      m.DraftPick.player_id.isnot(None))]

    players = db.query(m.Player).filter(m.Player.id.in_(roster_player_ids)).all()
    if not players:
        return {"error": "No rostered players found for this team_id. Draft or assign players first."}

    projections: Dict[int, ProjectionResult] = {}
    players_by_pos: Dict[str, List[dict]] = {}
    for p in players:
        proj = project_player(db, p.id, season, week, n_sims=n_sims)
        projections[p.id] = proj
        players_by_pos.setdefault(p.position, []).append({
            "player_id": p.id, "name": p.full_name, "position": p.position, "status": p.status,
            "median": proj.median, "floor": proj.floor, "ceiling": proj.ceiling,
            "confidence": proj.confidence,
        })

    # determine favorite/underdog framing if opponent given
    objective = "median"
    win_prob_context = None
    if opponent_team_id:
        opp_roster_ids = [row.player_id for row in
                           db.query(m.Lineup.player_id).filter(m.Lineup.team_id == opponent_team_id).distinct()]
        opp_players = db.query(m.Player).filter(m.Player.id.in_(opp_roster_ids)).all()
        opp_by_pos: Dict[str, List[dict]] = {}
        opp_projs = {}
        for p in opp_players:
            proj = project_player(db, p.id, season, week, n_sims=n_sims)
            opp_projs[p.id] = proj
            opp_by_pos.setdefault(p.position, []).append({
                "player_id": p.id, "name": p.full_name, "position": p.position,
                "median": proj.median, "floor": proj.floor, "ceiling": proj.ceiling,
            })
        my_median_lineup = _best_lineup_for_objective(players_by_pos, "median")
        opp_median_lineup = _best_lineup_for_objective(opp_by_pos, "median")
        my_totals = simulate_lineup_score({row["player_id"]: projections[row["player_id"]] for row in my_median_lineup}, n_sims)
        opp_totals = simulate_lineup_score({row["player_id"]: opp_projs[row["player_id"]] for row in opp_median_lineup}, n_sims)
        win_prob_context = matchup_win_probability(my_totals, opp_totals)
        objective = "safe" if win_prob_context["team_a_win_prob"] > 0.6 else (
            "upside" if win_prob_context["team_a_win_prob"] < 0.4 else "median")

    lineup = _best_lineup_for_objective(players_by_pos, objective)
    lineup_totals = simulate_lineup_score({row["player_id"]: projections[row["player_id"]] for row in lineup}, n_sims)

    bench = [row for pos in players_by_pos for row in players_by_pos[pos]
             if row["player_id"] not in {r["player_id"] for r in lineup}]

    explanation_bits = []
    if objective == "safe":
        explanation_bits.append("You're favored — prioritizing floor/consistency over upside.")
    elif objective == "upside":
        explanation_bits.append("You're an underdog — prioritizing ceiling and volatility over safety.")
    else:
        explanation_bits.append("Close matchup or no opponent given — optimizing for median expected points.")

    late_swap_notes = []
    for row in lineup:
        if row["slot"] == "FLEX" and row["position"] != "RB":
            pass  # informational hook only in MVP; real kickoff-time data needed to warn properly
    late_swap_notes.append(
        "Reminder: avoid locking early-window games into FLEX when a viable late-window alternative "
        "exists on your bench, to preserve late-swap flexibility (requires real kickoff-time data to enforce automatically)."
    )

    return {
        "lineup": lineup,
        "bench": bench,
        "projected_score_median": round(float(np.median(lineup_totals)), 2),
        "projected_score_floor_p10": round(float(np.percentile(lineup_totals, 10)), 2),
        "projected_score_ceiling_p90": round(float(np.percentile(lineup_totals, 90)), 2),
        "win_probability_vs_opponent": win_prob_context,
        "objective_used": objective,
        "why_this_lineup": " ".join(explanation_bits),
        "notes": late_swap_notes,
        "confidence": "Moderate",
        "model_version": "ensemble-v0.1-mvp",
    }
