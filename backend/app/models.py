"""
Normalized schema per spec section 15.
SQLite for the MVP; swap DATABASE_URL to a Postgres DSN and this runs unchanged
(no SQLite-specific types are used).
"""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, JSON,
    UniqueConstraint, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def now():
    return datetime.utcnow()


class DataSource(Base):
    __tablename__ = "data_sources"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)          # e.g. "nflverse", "mock", "sleeper"
    kind = Column(String, nullable=False)                        # stats | injury | news | odds | weather | adp
    reliability_tier = Column(String, default="unknown")         # primary | aggregator | rumor | unknown
    created_at = Column(DateTime, default=now)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)                        # e.g. "ensemble-v0.1-mvp"
    description = Column(Text)
    trained_at = Column(DateTime)
    created_at = Column(DateTime, default=now)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, nullable=False)
    display_name = Column(String)
    hashed_password = Column(String, nullable=False)
    created_at = Column(DateTime, default=now)


class League(Base):
    __tablename__ = "leagues"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    owner_user_id = Column(Integer, ForeignKey("users.id"))
    num_teams = Column(Integer, default=12)
    created_at = Column(DateTime, default=now)
    settings = relationship("LeagueSettings", uselist=False, back_populates="league")
    teams = relationship("Team", back_populates="league")


class LeagueSettings(Base):
    __tablename__ = "league_settings"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), unique=True)
    scoring_json = Column(JSON)          # ppr, td points, bonuses, etc.
    roster_slots_json = Column(JSON)     # {"QB":1,"RB":2,"WR":2,"TE":1,"FLEX":1,"DST":1,"K":1,"BENCH":7}
    draft_format = Column(String, default="snake")
    waiver_type = Column(String, default="FAAB")   # FAAB | rolling | reverse_standings
    faab_budget = Column(Integer, default=100)
    keeper_rules_json = Column(JSON)
    playoff_weeks_json = Column(JSON)    # {"start":15,"end":17}
    league = relationship("League", back_populates="settings")


class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String, nullable=False)
    is_user_team = Column(Boolean, default=False)
    draft_position = Column(Integer)
    waiver_priority = Column(Integer)
    faab_remaining = Column(Integer)
    league = relationship("League", back_populates="teams")


class NFLTeam(Base):
    __tablename__ = "nfl_teams"
    id = Column(Integer, primary_key=True)
    abbreviation = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False)
    conference = Column(String)
    division = Column(String)
    bye_week = Column(Integer)


class Player(Base):
    __tablename__ = "players"
    id = Column(Integer, primary_key=True)
    full_name = Column(String, nullable=False)
    position = Column(String, nullable=False)     # QB RB WR TE DST K
    nfl_team_id = Column(Integer, ForeignKey("nfl_teams.id"))
    depth_chart_rank = Column(Integer)
    status = Column(String, default="Active")     # Active, Questionable, Doubtful, Out, IR, Suspended
    age = Column(Integer)
    experience_years = Column(Integer)
    external_ids_json = Column(JSON)               # {"sleeper_id":..., "espn_id":...}
    created_at = Column(DateTime, default=now)


class Game(Base):
    __tablename__ = "games"
    id = Column(Integer, primary_key=True)
    season = Column(Integer, nullable=False)
    week = Column(Integer, nullable=False)
    home_team_id = Column(Integer, ForeignKey("nfl_teams.id"))
    away_team_id = Column(Integer, ForeignKey("nfl_teams.id"))
    kickoff_at = Column(DateTime)
    total_line = Column(Float)          # betting market game total
    home_spread = Column(Float)
    home_implied_total = Column(Float)
    away_implied_total = Column(Float)
    weather_json = Column(JSON)         # {"temp_f":.., "wind_mph":.., "precip_pct":.., "dome":bool}
    final_home_score = Column(Integer)
    final_away_score = Column(Integer)
    __table_args__ = (Index("ix_games_season_week", "season", "week"),)


class WeeklyStat(Base):
    __tablename__ = "weekly_stats"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    game_id = Column(Integer, ForeignKey("games.id"))
    season = Column(Integer)
    week = Column(Integer)
    snaps = Column(Integer)
    snap_pct = Column(Float)
    routes_run = Column(Integer)
    route_participation = Column(Float)
    targets = Column(Integer)
    target_share = Column(Float)
    air_yards = Column(Float)
    air_yard_share = Column(Float)
    receptions = Column(Integer)
    rec_yards = Column(Float)
    yac = Column(Float)
    carries = Column(Integer)
    rush_share = Column(Float)
    rush_yards = Column(Float)
    red_zone_touches = Column(Integer)
    goal_line_carries = Column(Integer)
    pass_attempts = Column(Integer)
    pass_yards = Column(Float)
    pass_tds = Column(Integer)
    interceptions = Column(Integer)
    rush_tds = Column(Integer)
    rec_tds = Column(Integer)
    fumbles_lost = Column(Integer)
    fantasy_points_ppr = Column(Float)
    data_source_id = Column(Integer, ForeignKey("data_sources.id"))
    __table_args__ = (Index("ix_weekly_stats_player_season_week", "player_id", "season", "week"),)


class AdvancedMetric(Base):
    __tablename__ = "advanced_metrics"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    season = Column(Integer)
    week = Column(Integer)
    expected_fantasy_points = Column(Float)
    fantasy_points_over_expected = Column(Float)
    weighted_opportunity = Column(Float)
    pass_rate_over_expected = Column(Float)     # team-level, joined for context
    offensive_line_grade = Column(Float)
    data_source_id = Column(Integer, ForeignKey("data_sources.id"))


class Injury(Base):
    __tablename__ = "injuries"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    body_part = Column(String)
    status = Column(String)             # Questionable/Doubtful/Out/IR
    reported_at = Column(DateTime, default=now)
    estimated_games_missed = Column(Integer)
    data_source_id = Column(Integer, ForeignKey("data_sources.id"))


class PracticeReport(Base):
    __tablename__ = "practice_reports"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    season = Column(Integer)
    week = Column(Integer)
    day = Column(String)                # Wed/Thu/Fri
    participation = Column(String)      # Full/Limited/DNP
    data_source_id = Column(Integer, ForeignKey("data_sources.id"))


class DepthChart(Base):
    __tablename__ = "depth_charts"
    id = Column(Integer, primary_key=True)
    nfl_team_id = Column(Integer, ForeignKey("nfl_teams.id"))
    position = Column(String)
    player_id = Column(Integer, ForeignKey("players.id"))
    rank = Column(Integer)
    effective_at = Column(DateTime, default=now)
    data_source_id = Column(Integer, ForeignKey("data_sources.id"))


class NewsEvent(Base):
    __tablename__ = "news_events"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    nfl_team_id = Column(Integer, ForeignKey("nfl_teams.id"), nullable=True)
    event_type = Column(String)          # ruled_out, questionable, promoted, ir, trade, suspension, weather, line_move
    headline = Column(String)
    body = Column(Text)
    reliability_score = Column(Float)    # 0-1
    confirmed = Column(Boolean, default=False)
    source_id = Column(Integer, ForeignKey("data_sources.id"))
    occurred_at = Column(DateTime, default=now)
    processed = Column(Boolean, default=False)


class Projection(Base):
    __tablename__ = "projections"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    season = Column(Integer)
    week = Column(Integer)
    model_version_id = Column(Integer, ForeignKey("model_versions.id"))
    median = Column(Float)
    mean = Column(Float)
    floor = Column(Float)
    ceiling = Column(Float)
    volatility_score = Column(Float)
    consistency_score = Column(Float)
    opportunity_score = Column(Float)
    matchup_score = Column(Float)
    role_security_score = Column(Float)
    breakout_probability = Column(Float)
    bust_probability = Column(Float)
    rest_of_season_value = Column(Float)
    confidence = Column(String)          # Very Low..Very High
    assumptions_json = Column(JSON)
    created_at = Column(DateTime, default=now)


class ProjectionDistribution(Base):
    __tablename__ = "projection_distributions"
    id = Column(Integer, primary_key=True)
    projection_id = Column(Integer, ForeignKey("projections.id"))
    p10 = Column(Float)
    p25 = Column(Float)
    p50 = Column(Float)
    p75 = Column(Float)
    p90 = Column(Float)
    prob_over_10 = Column(Float)
    prob_over_15 = Column(Float)
    prob_over_20 = Column(Float)
    prob_over_25 = Column(Float)
    prob_positional_rank_top12 = Column(Float)


class Ranking(Base):
    __tablename__ = "rankings"
    id = Column(Integer, primary_key=True)
    player_id = Column(Integer, ForeignKey("players.id"))
    season = Column(Integer)
    week = Column(Integer)
    ranking_type = Column(String)   # ros, weekly, dynasty, adp
    rank = Column(Integer)
    source_id = Column(Integer, ForeignKey("data_sources.id"))


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    team_id = Column(Integer, ForeignKey("teams.id"))
    player_id = Column(Integer, ForeignKey("players.id"))
    txn_type = Column(String)       # add, drop, trade, draft
    occurred_at = Column(DateTime, default=now)


class DraftPick(Base):
    __tablename__ = "draft_picks"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    round = Column(Integer)
    pick_in_round = Column(Integer)
    overall_pick = Column(Integer)
    team_id = Column(Integer, ForeignKey("teams.id"))
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    picked_at = Column(DateTime, nullable=True)


class WaiverClaim(Base):
    __tablename__ = "waiver_claims"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    team_id = Column(Integer, ForeignKey("teams.id"))
    add_player_id = Column(Integer, ForeignKey("players.id"))
    drop_player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    faab_bid = Column(Integer, nullable=True)
    priority = Column(Integer, nullable=True)
    status = Column(String, default="pending")   # pending, won, lost
    submitted_at = Column(DateTime, default=now)


class Trade(Base):
    __tablename__ = "trades"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    proposed_by_team_id = Column(Integer, ForeignKey("teams.id"))
    status = Column(String, default="proposed")   # proposed, accepted, rejected, vetoed
    created_at = Column(DateTime, default=now)
    assets = relationship("TradeAsset", back_populates="trade")


class TradeAsset(Base):
    __tablename__ = "trade_assets"
    id = Column(Integer, primary_key=True)
    trade_id = Column(Integer, ForeignKey("trades.id"))
    from_team_id = Column(Integer, ForeignKey("teams.id"))
    to_team_id = Column(Integer, ForeignKey("teams.id"))
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    draft_pick_id = Column(Integer, ForeignKey("draft_picks.id"), nullable=True)
    trade = relationship("Trade", back_populates="assets")


class Lineup(Base):
    __tablename__ = "lineups"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    season = Column(Integer)
    week = Column(Integer)
    slot = Column(String)             # QB, RB1, RB2, WR1, WR2, TE, FLEX, DST, K, BENCH
    player_id = Column(Integer, ForeignKey("players.id"))
    locked = Column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("team_id", "season", "week", "slot", name="uq_lineup_slot"),)


class MatchupSimulation(Base):
    __tablename__ = "matchup_simulations"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    season = Column(Integer)
    week = Column(Integer)
    team_a_id = Column(Integer, ForeignKey("teams.id"))
    team_b_id = Column(Integer, ForeignKey("teams.id"))
    num_simulations = Column(Integer)
    team_a_win_prob = Column(Float)
    team_a_median_score = Column(Float)
    team_b_median_score = Column(Float)
    model_version_id = Column(Integer, ForeignKey("model_versions.id"))
    created_at = Column(DateTime, default=now)


class SeasonSimulation(Base):
    __tablename__ = "season_simulations"
    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"))
    team_id = Column(Integer, ForeignKey("teams.id"))
    season = Column(Integer)
    num_simulations = Column(Integer)
    playoff_probability = Column(Float)
    championship_probability = Column(Float)
    projected_final_wins_median = Column(Float)
    model_version_id = Column(Integer, ForeignKey("model_versions.id"))
    created_at = Column(DateTime, default=now)


class RecommendationLog(Base):
    __tablename__ = "recommendation_logs"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    endpoint = Column(String)
    request_json = Column(JSON)
    response_json = Column(JSON)
    model_version_id = Column(Integer, ForeignKey("model_versions.id"))
    created_at = Column(DateTime, default=now)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    headline = Column(String)
    body = Column(Text)
    urgency_score = Column(Float)          # 0-1, used to prevent spam
    related_player_id = Column(Integer, ForeignKey("players.id"), nullable=True)
    created_at = Column(DateTime, default=now)
    read = Column(Boolean, default=False)
