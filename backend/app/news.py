"""
News ingestion + reliability scoring per spec section 3/17.
Real feeds (beat reporters, team wire, aggregators) plug in by writing rows into
news_events with a source_id pointing at a DataSource row carrying a
reliability_tier — nothing else here needs to change.
"""
from datetime import datetime
from sqlalchemy.orm import Session
from . import models as m

RELIABILITY_BY_TIER = {
    "primary": 0.95,       # team/beat reporter confirmation
    "aggregator": 0.6,     # fantasy news aggregators repeating a primary report
    "rumor": 0.25,         # single unconfirmed source
    "unknown": 0.4,
}


def score_reliability(source: m.DataSource, corroborating_sources: int = 0) -> float:
    base = RELIABILITY_BY_TIER.get(source.reliability_tier, 0.4)
    # each independent corroborating source nudges reliability up, capped at 0.98
    return round(min(0.98, base + corroborating_sources * 0.15), 3)


def ingest_mock_event(db: Session, player_id: int, event_type: str, headline: str, body: str,
                       reliability_tier: str = "unknown", confirmed: bool = False) -> m.NewsEvent:
    src = db.query(m.DataSource).filter(m.DataSource.name == "mock").first()
    event = m.NewsEvent(
        player_id=player_id, event_type=event_type, headline=headline, body=body,
        reliability_score=RELIABILITY_BY_TIER.get(reliability_tier, 0.4),
        confirmed=confirmed, source_id=src.id if src else None,
        occurred_at=datetime.utcnow(), processed=False,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
