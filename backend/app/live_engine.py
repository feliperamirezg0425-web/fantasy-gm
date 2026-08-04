"""
Live-event decision workflow per README section 8 / spec section 3.
In production this runs as a poll loop or webhook consumer feeding a
Celery/BullMQ queue; for the MVP, call process_pending_events() manually or on
a simple interval (e.g. an APScheduler job) — the processing logic itself does
not depend on how it's triggered.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from . import models as m
from .projections import project_player
from .lineup import optimize_lineup


def _find_affected_players(db: Session, event: m.NewsEvent) -> list[int]:
    affected = set()
    if event.player_id:
        affected.add(event.player_id)
        player = db.query(m.Player).get(event.player_id)
        if player and event.event_type in ("ruled_out", "questionable", "doubtful", "ir", "suspension"):
            # beneficiaries: next players on the same team's depth chart at same position
            beneficiaries = (db.query(m.DepthChart)
                              .filter(m.DepthChart.nfl_team_id == player.nfl_team_id,
                                      m.DepthChart.position == player.position,
                                      m.DepthChart.rank > player.depth_chart_rank)
                              .order_by(m.DepthChart.rank).limit(2).all())
            for b in beneficiaries:
                affected.add(b.player_id)
    if event.nfl_team_id and event.event_type in ("weather", "line_move"):
        teammates = db.query(m.Player.id).filter(m.Player.nfl_team_id == event.nfl_team_id).all()
        affected.update(row.id for row in teammates)
    return list(affected)


STATUS_BY_EVENT_TYPE = {
    "ruled_out": "Out",
    "questionable": "Questionable",
    "doubtful": "Doubtful",
    "ir": "IR",
    "suspension": "Suspended",
}


def process_pending_events(db: Session, season: int, week: int) -> list[dict]:
    pending = db.query(m.NewsEvent).filter(m.NewsEvent.processed == False).all()  # noqa: E712
    outcomes = []
    for event in pending:
        # apply the reported status to the player BEFORE projecting, so the
        # projection engine's injury dampener actually reflects the news
        if event.player_id and event.event_type in STATUS_BY_EVENT_TYPE and event.confirmed:
            player = db.query(m.Player).get(event.player_id)
            if player:
                player.status = STATUS_BY_EVENT_TYPE[event.event_type]
                db.flush()

        affected_ids = _find_affected_players(db, event)
        updated_projections = []
        for pid in affected_ids:
            proj = project_player(db, pid, season, week, n_sims=500)
            player = db.query(m.Player).get(pid)
            updated_projections.append({
                "player_id": pid, "name": player.full_name if player else None,
                "new_median": proj.median, "confidence": proj.confidence,
            })

        # find rosters (teams) containing affected players to flag for lineup re-check
        affected_team_ids = [row.team_id for row in
                              db.query(m.Lineup.team_id).filter(m.Lineup.player_id.in_(affected_ids)).distinct()]

        urgency = round(event.reliability_score * min(1.0, len(affected_ids) / 3), 3)
        notif_body = (f"{event.headline}. Affected players: "
                       f"{', '.join(p['name'] for p in updated_projections if p['name'])}. "
                       f"{'CONFIRMED' if event.confirmed else 'UNCONFIRMED — verify before acting'}.")
        for team_id in affected_team_ids:
            db.add(m.Notification(team_id=team_id, headline=event.headline, body=notif_body,
                                   urgency_score=urgency,
                                   related_player_id=affected_ids[0] if affected_ids else None))

        event.processed = True
        outcomes.append({
            "event_id": event.id,
            "event_type": event.event_type,
            "confirmed": event.confirmed,
            "reliability_score": event.reliability_score,
            "affected_players": updated_projections,
            "affected_team_ids": affected_team_ids,
            "urgency_score": urgency,
        })
    db.commit()
    return outcomes
