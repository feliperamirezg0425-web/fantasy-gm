from datetime import datetime
from typing import List, Optional, Dict
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import models as m
from .db import get_db, init_db
from .projections import project_player, explain_projection
from .draft import build_big_board, recommend_pick
from .lineup import optimize_lineup
from .waiver import recommend_waiver_adds
from .trade import analyze_trade
from .simulation import simulate_remaining_season
from .news import ingest_mock_event
from .live_engine import process_pending_events

app = FastAPI(title="Fantasy GM API", version="0.1.0-mvp")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

MODEL_VERSION = "ensemble-v0.1-mvp"


def envelope(recommendation, confidence="Moderate", data_source="mock",
             assumptions=None, missing=None):
    return {
        "recommendation": recommendation,
        "confidence": confidence,
        "model_version": MODEL_VERSION,
        "data_source": data_source,
        "data_timestamp": datetime.utcnow().isoformat() + "Z",
        "assumptions": assumptions or [],
        "missing_or_uncertain": missing or [],
        "disclaimer": "Entertainment and decision-support only. No outcome is guaranteed.",
    }


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/league/{league_id}/dashboard")
def dashboard(league_id: int, team_id: int, season: int, week: int, db: Session = Depends(get_db)):
    team = db.query(m.Team).get(team_id)
    if not team:
        raise HTTPException(404, "team not found")

    lineup_result = optimize_lineup(db, team_id, season, week)
    notifications = (db.query(m.Notification)
                      .filter(m.Notification.team_id == team_id)
                      .order_by(m.Notification.urgency_score.desc())
                      .limit(10).all())
    urgent = [{"headline": n.headline, "body": n.body, "urgency_score": n.urgency_score,
               "created_at": n.created_at.isoformat()} for n in notifications]

    return envelope({
        "team_name": team.name,
        "projected_lineup_score_median": lineup_result.get("projected_score_median"),
        "recommended_lineup": lineup_result.get("lineup"),
        "urgent_alerts": urgent,
        "next_lineup_deadline_note": "Deadline tracking requires real game-kickoff data; not wired in mock mode.",
    }, confidence=lineup_result.get("confidence", "Moderate"),
        assumptions=["Dashboard aggregates the lineup optimizer + top 10 notifications by urgency."])


@app.get("/api/player/{player_id}/projection")
def player_projection(player_id: int, season: int, week: int, db: Session = Depends(get_db)):
    player = db.query(m.Player).get(player_id)
    if not player:
        raise HTTPException(404, "player not found")
    proj = project_player(db, player_id, season, week)
    explanation = explain_projection(player, proj)
    return envelope({
        "player": {"id": player.id, "name": player.full_name, "position": player.position,
                    "status": player.status},
        "median": proj.median, "mean": proj.mean, "floor": proj.floor, "ceiling": proj.ceiling,
        "percentiles": {"p10": proj.p10, "p25": proj.p25, "p50": proj.p50, "p75": proj.p75, "p90": proj.p90},
        "probabilities": {"over_10": proj.prob_over_10, "over_15": proj.prob_over_15,
                            "over_20": proj.prob_over_20, "over_25": proj.prob_over_25},
        "scores": {"volatility": proj.volatility_score, "consistency": proj.consistency_score,
                    "opportunity": proj.opportunity_score, "matchup": proj.matchup_score,
                    "role_security": proj.role_security_score,
                    "breakout_probability": proj.breakout_probability, "bust_probability": proj.bust_probability},
        "explanation": explanation,
    }, confidence=proj.confidence, assumptions=proj.assumptions, missing=proj.missing_or_uncertain)


@app.get("/api/draft/{league_id}/big-board")
def draft_big_board(league_id: int, season: int, week: int, num_teams: int = 12, db: Session = Depends(get_db)):
    board = build_big_board(db, season, week, num_teams)
    return envelope(board, confidence="Moderate",
                     assumptions=["Replacement baseline computed from players ranked just outside the "
                                  "typical starting pool for a league of this size."])


class DraftPickRequest(BaseModel):
    league_id: int
    season: int
    week: int
    available_player_ids: List[int]
    roster_player_ids: List[int]
    team_id: int
    picks_until_next_turn: int
    num_teams: int = 12


@app.post("/api/draft/recommend-pick")
def draft_recommend_pick(req: DraftPickRequest, db: Session = Depends(get_db)):
    result = recommend_pick(db, req.league_id, req.season, req.week, req.available_player_ids,
                             req.roster_player_ids, req.team_id, req.picks_until_next_turn, req.num_teams)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return envelope(result, confidence=result.get("confidence", "Moderate"),
                     assumptions=result.get("assumptions", []))


@app.get("/api/lineup/optimize")
def lineup_optimize(team_id: int, season: int, week: int, opponent_team_id: Optional[int] = None,
                     db: Session = Depends(get_db)):
    result = optimize_lineup(db, team_id, season, week, opponent_team_id)
    if "error" in result:
        raise HTTPException(400, result["error"])
    return envelope(result, confidence=result.get("confidence", "Moderate"))


class WaiverRequest(BaseModel):
    team_id: int
    league_id: int
    season: int
    week: int
    free_agent_player_ids: List[int]
    faab_remaining: int = 100
    roster_needs: Optional[List[str]] = None
    playoff_weeks: Optional[List[int]] = None


@app.post("/api/waiver/recommend")
def waiver_recommend(req: WaiverRequest, db: Session = Depends(get_db)):
    result = recommend_waiver_adds(db, req.team_id, req.league_id, req.season, req.week,
                                    req.free_agent_player_ids, req.faab_remaining,
                                    req.roster_needs, req.playoff_weeks)
    return envelope(result, confidence=result.get("confidence", "Low"), assumptions=result.get("assumptions", []))


class TradeRequest(BaseModel):
    league_id: int
    season: int
    week: int
    team_assets: Dict[int, dict]   # {team_id: {"sends": [...], "receives": [...]}}


@app.post("/api/trade/analyze")
def trade_analyze(req: TradeRequest, db: Session = Depends(get_db)):
    # pydantic coerces dict keys with int annotation poorly over JSON; normalize here
    normalized = {int(k): v for k, v in req.team_assets.items()}
    result = analyze_trade(db, req.league_id, req.season, req.week, normalized)
    return envelope(result, confidence=result.get("confidence", "Moderate"), assumptions=result.get("assumptions", []))


@app.get("/api/league/{league_id}/season-simulation")
def season_simulation(league_id: int, playoff_spots: int = 6, n_sims: int = 2000, db: Session = Depends(get_db)):
    teams = db.query(m.Team).filter(m.Team.league_id == league_id).order_by(m.Team.id).all()
    if not teams:
        raise HTTPException(404, "no teams found for league")
    # MVP: no real season-to-date win/loss records loaded yet in mock mode, so
    # start everyone at 0 wins with a modest synthetic weekly win-prob spread
    # derived from roster starter value — a real deployment reads actual
    # standings + remaining schedule here instead.
    import numpy as np
    np.random.seed(league_id)
    current_wins = [0] * len(teams)
    remaining_win_probs = [list(np.clip(np.random.normal(0.5, 0.12, 10), 0.05, 0.95)) for _ in teams]
    sim = simulate_remaining_season(current_wins, remaining_win_probs, playoff_spots, n_sims)
    per_team = [{"team_id": t.id, "team_name": t.name,
                  "playoff_probability": sim["playoff_probability"][i],
                  "championship_probability_proxy": sim["championship_probability_proxy"][i]}
                 for i, t in enumerate(teams)]
    return envelope({"teams": per_team, "num_simulations": n_sims, "note": sim["note"]},
                     confidence="Low",
                     assumptions=["Uses synthetic starting win probabilities derived from a fixed random seed "
                                  "in mock mode — replace with real standings + schedule for production use."])


class MockNewsRequest(BaseModel):
    player_id: int
    event_type: str
    headline: str
    body: str = ""
    reliability_tier: str = "unknown"
    confirmed: bool = False


@app.post("/api/news/mock-event")
def news_mock_event(req: MockNewsRequest, db: Session = Depends(get_db)):
    """Testing helper: injects a mock news event so you can see the live-event
    workflow react to it. Not a real news feed."""
    event = ingest_mock_event(db, req.player_id, req.event_type, req.headline, req.body,
                               req.reliability_tier, req.confirmed)
    return envelope({"event_id": event.id, "status": "queued_for_processing"})


@app.post("/api/news/process-pending")
def news_process_pending(season: int, week: int, db: Session = Depends(get_db)):
    outcomes = process_pending_events(db, season, week)
    return envelope({"processed_events": outcomes}, confidence="Moderate")
