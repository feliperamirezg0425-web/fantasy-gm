"""
Phase 5 (spec section 18): unit tests for the core math, run against an
in-memory SQLite DB seeded with mock data. Run with: pytest -q
"""
import os
import sys
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.models import Base
from app import mock_data
from app.projections import project_player
from app.draft import build_big_board, STARTERS_BY_POS
from app.lineup import optimize_lineup, _best_lineup_for_objective, FLEX_ELIGIBLE
from app import models as m


@pytest.fixture(scope="module")
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    mock_data.seed(session)
    yield session
    session.close()


def test_projection_percentiles_are_monotonic(db):
    player = db.query(m.Player).filter(m.Player.position == "WR").first()
    proj = project_player(db, player.id, 2026, 1, n_sims=1000)
    assert proj.p10 <= proj.p25 <= proj.p50 <= proj.p75 <= proj.p90
    assert proj.floor <= proj.ceiling
    assert 0 <= proj.prob_over_25 <= proj.prob_over_20 <= proj.prob_over_15 <= proj.prob_over_10 <= 1


def test_projection_never_negative(db):
    player = db.query(m.Player).filter(m.Player.position == "RB").first()
    proj = project_player(db, player.id, 2026, 1, n_sims=500)
    assert proj.floor >= 0
    assert proj.median >= 0


def test_out_status_dampens_projection_below_active(db):
    player = db.query(m.Player).filter(m.Player.position == "RB").first()
    player.status = "Active"
    active_proj = project_player(db, player.id, 2026, 1, n_sims=2000)
    player.status = "Out"
    out_proj = project_player(db, player.id, 2026, 1, n_sims=2000)
    assert out_proj.median < active_proj.median


def test_vorp_baseline_is_lower_than_top_players(db):
    board = build_big_board(db, 2026, 1, num_teams=12, n_sims=200)
    top_vorp = board["board"][0]["vorp"]
    bottom_vorp = board["board"][-1]["vorp"]
    assert top_vorp >= bottom_vorp
    # replacement baseline should exist for every starting position
    for pos in STARTERS_BY_POS:
        assert pos in board["replacement_baseline"]


def test_lineup_fills_flex_with_eligible_position_only(db):
    players_by_pos = {
        "QB": [{"player_id": 1, "position": "QB", "median": 18, "floor": 12, "ceiling": 24}],
        "RB": [{"player_id": 2, "position": "RB", "median": 15, "floor": 8, "ceiling": 22},
               {"player_id": 3, "position": "RB", "median": 10, "floor": 5, "ceiling": 16},
               {"player_id": 4, "position": "RB", "median": 9, "floor": 4, "ceiling": 15}],
        "WR": [{"player_id": 5, "position": "WR", "median": 14, "floor": 7, "ceiling": 20},
               {"player_id": 6, "position": "WR", "median": 11, "floor": 6, "ceiling": 17}],
        "TE": [{"player_id": 7, "position": "TE", "median": 8, "floor": 3, "ceiling": 13}],
        "DST": [{"player_id": 8, "position": "DST", "median": 7, "floor": 2, "ceiling": 12}],
        "K": [{"player_id": 9, "position": "K", "median": 6, "floor": 3, "ceiling": 9}],
    }
    lineup = _best_lineup_for_objective(players_by_pos, "median")
    flex_row = next(r for r in lineup if r["slot"] == "FLEX")
    assert flex_row["position"] in FLEX_ELIGIBLE
    # the 3rd-best RB (player 4) should fill FLEX since RB1/RB2 already used players 2 and 3
    assert flex_row["player_id"] == 4


def test_lineup_never_duplicates_a_player_across_slots(db):
    players_by_pos = {
        "QB": [{"player_id": 1, "position": "QB", "median": 18, "floor": 12, "ceiling": 24}],
        "RB": [{"player_id": 2, "position": "RB", "median": 15, "floor": 8, "ceiling": 22}],
        "WR": [{"player_id": 5, "position": "WR", "median": 14, "floor": 7, "ceiling": 20},
               {"player_id": 6, "position": "WR", "median": 11, "floor": 6, "ceiling": 17}],
        "TE": [{"player_id": 7, "position": "TE", "median": 8, "floor": 3, "ceiling": 13}],
        "DST": [{"player_id": 8, "position": "DST", "median": 7, "floor": 2, "ceiling": 12}],
        "K": [{"player_id": 9, "position": "K", "median": 6, "floor": 3, "ceiling": 9}],
    }
    lineup = _best_lineup_for_objective(players_by_pos, "median")
    ids = [r["player_id"] for r in lineup]
    assert len(ids) == len(set(ids))
