from __future__ import annotations
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import Base, get_session
from app.main import app
from app.models import EmissionFactor
from sqlalchemy.orm import declarative_base, sessionmaker
TEST_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    TEST_DATABASE_URL,
    poolclass = StaticPool,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
@pytest.fixture
def db_session():
    """
    Skapar en ny databas-session för varje test.
    Varje test får sin egen session (isolerar tester från varandra).
    """
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    car_factor = EmissionFactor(
        category = "travel",
        key = "car",
        unit = "km",
        co2e_per_unit = 0.2,
        source = "test",
        scope = "none"
        )
    db.add(car_factor)
    db.commit()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        
@pytest.fixture
def client(db_session):
    """
    Skapar en TestClient mot FastAPI men med DB-dependency override.
    Då använder API:t testdatabasen när tester körs.
    """
    def override_get_session():
        yield db_session
    app.dependency_overrides[get_session] = override_get_session
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()