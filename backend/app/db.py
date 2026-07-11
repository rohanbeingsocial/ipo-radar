from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (register mappings)
    Base.metadata.create_all(bind=engine)
    _migrate(engine)


def _migrate(eng) -> None:
    """create_all never alters existing tables; add late columns here."""
    from sqlalchemy import inspect, text
    insp = inspect(eng)
    if "market_signals" not in insp.get_table_names():
        return
    have = {c["name"] for c in insp.get_columns("market_signals")}
    with eng.begin() as conn:
        for col in ("sub_bnii", "sub_snii", "day1_gain"):
            if col not in have:
                conn.execute(text(f"ALTER TABLE market_signals ADD COLUMN {col} FLOAT"))
