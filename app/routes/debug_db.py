# app/routes/debug_db.py
from fastapi import APIRouter
from sqlalchemy import text, create_engine
import os

router = APIRouter(prefix="/debug", tags=["debug"])
engine = create_engine(os.environ["DATABASE_URL"], future=True)

@router.get("/db-info")
def db_info():
    with engine.begin() as c:
        row = c.execute(text("""
          SELECT current_database() AS db,
                 current_user AS usr,
                 inet_server_addr()::text AS host
        """)).mappings().first()
    return row
