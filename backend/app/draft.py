"""
Draft-optimization methodology per README section 7.
"""
import numpy as np
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from . import models as m
from .projections import project_player

STARTERS_BY_POS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DST": 1, "K": 1}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}


class TendencyModel:
    """
    Opponent draft-behavior learning (spec section 5/9). Activates automatically
    once multi-season `draft_picks` history exists for this league; until then,
    falls back to ADP-only assumptions and says so explicitly.
    """
    def __init__(self, db: Session, league_id: int):
        self.db = db
        self.league_id = league_id
        history = db.query(m.DraftPick).filter(
            m.DraftPick.league_id == league_id, m.DraftPick.picked_at.isnot(None)
        ).count()
        self.has_history = history >= 24  # roughly one prior draft's worth

    def position_take_probability(self, team_id: int, position: str) -> float:
        if not self.has_history:
            return {"QB": 0.10, "RB": 0.30, "WR": 0.32, "TE": 0.10, "DST": 0.10, "K": 0.08}[position]
        picks = self.db.query(m.DraftPick).join(m.Player, m.DraftPick.player_id == m.Player.id).filter(
            m.DraftPick.team_id == team_id, m.DraftPick.picked_at.isnot(None)
        ).all()
        if not picks:
            return 0.2
        pos_counts = {}
        for pk in picks:
            pos_counts[pk.player.position] = pos_counts.get(pk.player.position, 0) + 1
        total = sum(pos_counts.values())
        return pos_counts.get(position, 0) / total if total else 0.2


def _replacement_baseline(ranked_by_pos: Dict[str, List[dict]], num_teams: int) -> Dict[str, float]:
    baseline = {}
    for pos, starters in STARTERS_BY_POS.items():
        idx = num_teams * starters  # first player just outside common starting pool
        pool = ranked_by_pos.get(pos, [])
        window = pool[idx: idx + 3] if len(pool) > idx else pool[-3:]
        baseline[pos] = float(np.mean([p["median"] for p in window])) if window else 0.0
    return baseline


def build_big_board(db: Session, season: int, week: int, num_teams: int = 12,
                     n_sims: int = 500) -> dict:
    players = db.query(m.Player).filter(m.Player.position.in_(list(STARTERS_BY_POS.keys()))).all()
    ranked_by_pos: Dict[str, List[dict]] = {}
    for p in players:
        proj = project_player(db, p.id, season, week, n_sims=n_sims)
        ranked_by_pos.setdefault(p.position, []).append({
            "player_id": p.id, "name": p.full_name, "position": p.position,
            "median": proj.median, "ceiling": proj.ceiling, "floor": proj.floor,
            "confidence": proj.confidence,
        })
    for pos in ranked_by_pos:
        ranked_by_pos[pos].sort(key=lambda x: -x["median"])

    baseline = _replacement_baseline(ranked_by_pos, num_teams)

    board = []
    for pos, plist in ranked_by_pos.items():
        for row in plist:
            row["vorp"] = round(row["median"] - baseline.get(pos, 0), 2)
            board.append(row)
    board.sort(key=lambda x: -x["vorp"])
    return {"board": board, "replacement_baseline": baseline}


def recommend_pick(db: Session, league_id: int, season: int, week: int,
                    available_player_ids: List[int], roster_player_ids: List[int],
                    team_id: int, picks_until_next_turn: int, num_teams: int = 12) -> dict:
    tendency = TendencyModel(db, league_id)
    roster = db.query(m.Player).filter(m.Player.id.in_(roster_player_ids)).all()
    roster_pos_counts = {}
    for p in roster:
        roster_pos_counts[p.position] = roster_pos_counts.get(p.position, 0) + 1

    candidates = db.query(m.Player).filter(m.Player.id.in_(available_player_ids)).all()
    board = build_big_board(db, season, week, num_teams)
    board_lookup = {row["player_id"]: row for row in board["board"]}

    scored = []
    for p in candidates:
        row = board_lookup.get(p.id)
        if not row:
            continue
        # probability this player survives to your next pick
        take_prob = tendency.position_take_probability(team_id, p.position)
        p_gone = 1 - (1 - take_prob) ** max(picks_until_next_turn, 0)
        survival_prob = round(1 - p_gone, 3)

        need_factor = 1.0
        starters_needed = STARTERS_BY_POS.get(p.position, 1)
        have = roster_pos_counts.get(p.position, 0)
        if have < starters_needed:
            need_factor = 1.15
        elif have >= starters_needed + 2:
            need_factor = 0.9

        scored.append({
            **row,
            "survival_prob_next_pick": survival_prob,
            "roster_need_adjusted_vorp": round(row["vorp"] * need_factor, 2),
        })

    if not scored:
        return {"error": "No valid candidates found among available_player_ids."}

    best_overall = max(scored, key=lambda x: x["roster_need_adjusted_vorp"])
    safest = max(scored, key=lambda x: (x["floor"], -x["vorp"] * 0))
    highest_upside = max(scored, key=lambda x: x["ceiling"])
    need_positions = [pos for pos, cnt in STARTERS_BY_POS.items() if roster_pos_counts.get(pos, 0) < cnt]
    best_need = None
    if need_positions:
        need_pool = [s for s in scored if s["position"] in need_positions]
        if need_pool:
            best_need = max(need_pool, key=lambda x: x["vorp"])
    likely_available_next = sorted(
        [s for s in scored if s["survival_prob_next_pick"] > 0.6],
        key=lambda x: -x["roster_need_adjusted_vorp"]
    )[:5]

    return {
        "best_overall": best_overall,
        "safest": safest,
        "highest_upside": highest_upside,
        "best_positional_need": best_need,
        "likely_available_at_next_pick": likely_available_next,
        "full_candidate_list": sorted(scored, key=lambda x: -x["roster_need_adjusted_vorp"])[:20],
        "confidence": "Moderate" if tendency.has_history else "Low",
        "assumptions": [
            "Opponent tendencies" + (" learned from league draft history." if tendency.has_history
                                      else " NOT available yet — using generic ADP-shaped position-take rates."),
            f"Survival probability model assumes {picks_until_next_turn} picks until your next turn.",
        ],
    }
