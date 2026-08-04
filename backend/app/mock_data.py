"""
Generates statistically plausible SYNTHETIC data so the app is fully runnable
without any paid/licensed API credentials.

Every row this module writes gets data_source = "mock" so nothing here is ever
confused with real player news or stats downstream (spec section 17 rule against
fabricating information — the fix is not "never generate test data", it's "never
label test data as real").

Swap this module out for real adapters in app/adapters/ when you have API keys;
nothing else in the codebase needs to change (see README section 4).
"""
import random
from datetime import datetime, timedelta
import numpy as np
from sqlalchemy.orm import Session
from . import models as m

random.seed(42)
np.random.seed(42)

NFL_TEAMS = [
    ("BUF", "Buffalo Bills", "AFC", "East", 12), ("MIA", "Miami Dolphins", "AFC", "East", 6),
    ("NE", "New England Patriots", "AFC", "East", 14), ("NYJ", "New York Jets", "AFC", "East", 12),
    ("BAL", "Baltimore Ravens", "AFC", "North", 14), ("CIN", "Cincinnati Bengals", "AFC", "North", 12),
    ("CLE", "Cleveland Browns", "AFC", "North", 10), ("PIT", "Pittsburgh Steelers", "AFC", "North", 9),
    ("HOU", "Houston Texans", "AFC", "South", 14), ("IND", "Indianapolis Colts", "AFC", "South", 11),
    ("JAX", "Jacksonville Jaguars", "AFC", "South", 12), ("TEN", "Tennessee Titans", "AFC", "South", 5),
    ("DEN", "Denver Broncos", "AFC", "West", 12), ("KC", "Kansas City Chiefs", "AFC", "West", 10),
    ("LV", "Las Vegas Raiders", "AFC", "West", 10), ("LAC", "Los Angeles Chargers", "AFC", "West", 8),
    ("DAL", "Dallas Cowboys", "NFC", "East", 7), ("NYG", "New York Giants", "NFC", "East", 11),
    ("PHI", "Philadelphia Eagles", "NFC", "East", 9), ("WAS", "Washington Commanders", "NFC", "East", 12),
    ("CHI", "Chicago Bears", "NFC", "North", 5), ("DET", "Detroit Lions", "NFC", "North", 8),
    ("GB", "Green Bay Packers", "NFC", "North", 10), ("MIN", "Minnesota Vikings", "NFC", "North", 6),
    ("ATL", "Atlanta Falcons", "NFC", "South", 11), ("CAR", "Carolina Panthers", "NFC", "South", 7),
    ("NO", "New Orleans Saints", "NFC", "South", 11), ("TB", "Tampa Bay Buccaneers", "NFC", "South", 9),
    ("ARI", "Arizona Cardinals", "NFC", "West", 8), ("LAR", "Los Angeles Rams", "NFC", "West", 8),
    ("SF", "San Francisco 49ers", "NFC", "West", 9), ("SEA", "Seattle Seahawks", "NFC", "West", 10),
]

FIRST = ["Jordan", "Marcus", "Devon", "Tyler", "Malik", "Chris", "Jalen", "Trevor", "Josh", "Cade",
         "Amari", "Dante", "Xavier", "Brock", "Elijah", "Cooper", "Jamal", "Nate", "Rashaad", "Kellen"]
LAST = ["Carter", "Reed", "Thompson", "Hayes", "Brooks", "Mitchell", "Coleman", "Wallace", "Foster",
        "Sanders", "Griffin", "Bennett", "Hicks", "Warren", "Freeman", "Chandler", "Boyd", "Sims"]


def rand_name(used):
    for _ in range(50):
        n = f"{random.choice(FIRST)} {random.choice(LAST)}"
        if n not in used:
            used.add(n)
            return n
    return f"{random.choice(FIRST)} {random.choice(LAST)} Jr."


def seed(db: Session):
    used_names = set()

    # data sources + model version
    mock_src = m.DataSource(name="mock", kind="stats", reliability_tier="unknown")
    db.add(mock_src)
    db.flush()
    model_v = m.ModelVersion(name="ensemble-v0.1-mvp",
                              description="Recency-weighted + usage + opponent + market blend, MVP",
                              trained_at=datetime.utcnow())
    db.add(model_v)

    # NFL teams
    team_objs = {}
    for abbr, name, conf, div, bye in NFL_TEAMS:
        t = m.NFLTeam(abbreviation=abbr, name=name, conference=conf, division=div, bye_week=bye)
        db.add(t)
        db.flush()
        team_objs[abbr] = t

    # players: ~15 per team across positions relevant to fantasy
    position_counts = {"QB": 2, "RB": 3, "WR": 4, "TE": 2, "K": 1, "DST": 0}
    players = []
    for abbr, team in team_objs.items():
        for pos, count in position_counts.items():
            for rank in range(1, count + 1):
                p = m.Player(
                    full_name=rand_name(used_names),
                    position=pos,
                    nfl_team_id=team.id,
                    depth_chart_rank=rank,
                    status="Active",
                    age=random.randint(21, 33),
                    experience_years=random.randint(0, 11),
                    external_ids_json={"mock_id": f"{abbr}-{pos}-{rank}"},
                )
                db.add(p)
                players.append(p)
        # DST as a unit "player"
        dst = m.Player(full_name=f"{team.name} D/ST", position="DST", nfl_team_id=team.id,
                        depth_chart_rank=1, status="Active", external_ids_json={"mock_id": f"{abbr}-DST"})
        db.add(dst)
        players.append(dst)
    db.flush()

    # depth charts
    for p in players:
        if p.position != "DST":
            db.add(m.DepthChart(nfl_team_id=p.nfl_team_id, position=p.position, player_id=p.id,
                                 rank=p.depth_chart_rank, data_source_id=mock_src.id))

    # baseline talent factor per player (drives realistic point spread)
    talent = {}
    for p in players:
        base = {
            "QB": 18, "RB": 12, "WR": 11, "TE": 8, "K": 7, "DST": 7
        }[p.position]
        rank_penalty = (p.depth_chart_rank - 1) * {"QB": 8, "RB": 5, "WR": 4, "TE": 3, "K": 0, "DST": 0}[p.position]
        talent[p.id] = max(1.5, base - rank_penalty + np.random.normal(0, 2.5))

    # 5 historical seasons + current season through "latest available week"
    current_season = 2026
    seasons = list(range(current_season - 5, current_season + 1))
    games_by_season_week = {}

    abbrs = list(team_objs.keys())
    for season in seasons:
        weeks = 17 if season < current_season else 6   # "current season through latest available week"
        random.shuffle(abbrs)
        for week in range(1, weeks + 1):
            pairs = list(zip(abbrs[::2], abbrs[1::2]))
            for home_abbr, away_abbr in pairs:
                total = round(np.random.normal(45, 5), 1)
                spread = round(np.random.normal(0, 6), 1)
                home_implied = round(total / 2 - spread / 2, 1)
                away_implied = round(total / 2 + spread / 2, 1)
                g = m.Game(
                    season=season, week=week,
                    home_team_id=team_objs[home_abbr].id,
                    away_team_id=team_objs[away_abbr].id,
                    kickoff_at=datetime(season, 9, 1) + timedelta(weeks=week - 1),
                    total_line=total, home_spread=spread,
                    home_implied_total=home_implied, away_implied_total=away_implied,
                    weather_json={"temp_f": random.randint(28, 82), "wind_mph": random.randint(0, 22),
                                  "precip_pct": random.choice([0, 0, 0, 10, 30, 60]),
                                  "dome": random.random() < 0.3},
                )
                db.add(g)
                games_by_season_week.setdefault((season, week), []).append((g, home_abbr, away_abbr))
    db.flush()

    # weekly stats
    team_players = {}
    for p in players:
        team_players.setdefault(p.nfl_team_id, []).append(p)

    for (season, week), games in games_by_season_week.items():
        for g, home_abbr, away_abbr in games:
            g.final_home_score = max(0, int(np.random.normal(g.home_implied_total, 7)))
            g.final_away_score = max(0, int(np.random.normal(g.away_implied_total, 7)))
            for abbr, opp_implied in [(home_abbr, g.away_implied_total), (away_abbr, g.home_implied_total)]:
                team_id = team_objs[abbr].id
                for p in team_players.get(team_id, []):
                    tal = talent[p.id]
                    variance = {"QB": 6, "RB": 7, "WR": 8, "TE": 5, "K": 3, "DST": 4}[p.position]
                    injury_dinged = random.random() < 0.06
                    fp = max(0, np.random.normal(tal * (g.home_implied_total if abbr == home_abbr else g.away_implied_total) / 24,
                                                  variance))
                    if injury_dinged:
                        fp *= random.uniform(0.1, 0.5)
                        p.status = random.choice(["Questionable", "Doubtful", "Out"])

                    snap_pct = min(1.0, max(0.05, np.random.beta(2 + (3 - p.depth_chart_rank), 2)))
                    targets = int(max(0, np.random.poisson(6))) if p.position in ("WR", "TE", "RB") else 0
                    carries = int(max(0, np.random.poisson(10))) if p.position == "RB" else (
                        int(max(0, np.random.poisson(2))) if p.position == "QB" else 0)

                    ws = m.WeeklyStat(
                        player_id=p.id, game_id=g.id, season=season, week=week,
                        snaps=int(snap_pct * 65), snap_pct=round(snap_pct, 3),
                        routes_run=int(targets * random.uniform(1.4, 2.2)) if p.position in ("WR", "TE") else 0,
                        route_participation=round(min(1.0, snap_pct * random.uniform(0.85, 1.05)), 3) if p.position in ("WR", "TE") else None,
                        targets=targets, target_share=round(targets / 32, 3),
                        air_yards=round(targets * random.uniform(6, 11), 1),
                        air_yard_share=round(min(1.0, targets * random.uniform(0.03, 0.05)), 3),
                        receptions=int(targets * random.uniform(0.55, 0.8)),
                        rec_yards=round(targets * random.uniform(6, 11) * random.uniform(0.5, 0.9), 1),
                        yac=round(random.uniform(2, 6) * max(1, targets * 0.6), 1),
                        carries=carries, rush_share=round(min(1.0, carries / 24), 3),
                        rush_yards=round(carries * random.uniform(3.5, 5.2), 1),
                        red_zone_touches=int(np.random.poisson(0.6)),
                        goal_line_carries=int(np.random.poisson(0.2)) if p.position == "RB" else 0,
                        pass_attempts=int(max(0, np.random.poisson(33))) if p.position == "QB" else 0,
                        pass_yards=round(np.random.normal(245, 55), 1) if p.position == "QB" else 0,
                        pass_tds=int(max(0, np.random.poisson(1.6))) if p.position == "QB" else 0,
                        interceptions=int(max(0, np.random.poisson(0.7))) if p.position == "QB" else 0,
                        rush_tds=int(np.random.poisson(0.15)),
                        rec_tds=int(np.random.poisson(0.12)) if p.position in ("WR", "TE", "RB") else 0,
                        fumbles_lost=1 if random.random() < 0.03 else 0,
                        fantasy_points_ppr=round(fp, 2),
                        data_source_id=mock_src.id,
                    )
                    db.add(ws)

                    aem = m.AdvancedMetric(
                        player_id=p.id, season=season, week=week,
                        expected_fantasy_points=round(fp * random.uniform(0.85, 1.15), 2),
                        fantasy_points_over_expected=round(np.random.normal(0, 2.5), 2),
                        weighted_opportunity=round(targets * 1.5 + carries * 0.7, 1),
                        pass_rate_over_expected=round(np.random.normal(0, 0.05), 3),
                        offensive_line_grade=round(np.random.normal(70, 10), 1),
                        data_source_id=mock_src.id,
                    )
                    db.add(aem)

    db.commit()

    # a demo league: user's team + 11 opponents
    demo_user = m.User(email="demo@fantasygm.local", display_name="Demo Manager",
                        hashed_password="not-a-real-hash")
    db.add(demo_user)
    db.flush()

    league = m.League(name="The Foster Cup", owner_user_id=demo_user.id, num_teams=12)
    db.add(league)
    db.flush()

    settings = m.LeagueSettings(
        league_id=league.id,
        scoring_json={"ppr": 1.0, "pass_td": 4, "rush_td": 6, "rec_td": 6, "int": -2,
                       "fumble_lost": -2, "bonus_100_rush_rec_yards": 3, "bonus_300_pass_yards": 3},
        roster_slots_json={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "DST": 1, "K": 1, "BENCH": 7},
        draft_format="snake",
        waiver_type="FAAB",
        faab_budget=100,
        keeper_rules_json={"keepers_allowed": 0},
        playoff_weeks_json={"start": 15, "end": 17},
    )
    db.add(settings)

    team_names = ["Foster Freight", "Scaffold Squad", "Panama Ballers", "Tallahassee Titans",
                  "Gridiron Grifters", "Waiver Wire Warriors", "The Analytics", "Sunday Scaries",
                  "Boom or Bust", "Chain Movers", "Red Zone Raiders", "Late Round Legends"]
    teams = []
    for i, name in enumerate(team_names):
        t = m.Team(league_id=league.id, owner_user_id=demo_user.id if i == 0 else None,
                   name=name, is_user_team=(i == 0), draft_position=i + 1,
                   waiver_priority=i + 1, faab_remaining=100)
        db.add(t)
        teams.append(t)
    db.commit()

    return {
        "league_id": league.id,
        "user_team_id": teams[0].id,
        "num_players": len(players),
        "num_teams": len(teams),
        "seasons_loaded": seasons,
    }
