from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fieldops.db")

# Render geeft soms postgres:// — SQLAlchemy 2.x wil postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # SQLAlchemy 2.1 gebruikt standaard psycopg 3; wij installeren psycopg2.
    # Houd de gewone PostgreSQL-URL beschikbaar voor andere gebruikers van DATABASE_URL.
    engine_url = DATABASE_URL
    if engine_url.startswith("postgresql://"):
        engine_url = engine_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(engine_url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
