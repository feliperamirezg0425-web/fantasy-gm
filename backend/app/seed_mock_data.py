"""
Run with: python -m app.seed_mock_data
Wipes and recreates the local SQLite DB, then populates it with mock league data.
"""
import os
from .db import engine, SessionLocal, init_db
from .models import Base
from .mock_data import seed


def main():
    db_path = "./fantasy_gm.db"
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db()
    db = SessionLocal()
    try:
        info = seed(db)
        print("Seeded mock league:")
        for k, v in info.items():
            print(f"  {k}: {v}")
        print("\nStart the API with: uvicorn app.main:app --reload")
        print(f"Try: /api/league/{info['league_id']}/dashboard?team_id={info['user_team_id']}&season=2026&week=1")
    finally:
        db.close()


if __name__ == "__main__":
    main()
