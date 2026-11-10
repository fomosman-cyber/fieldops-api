"""PostgreSQL-engine gebruikt de driver die in requirements.txt staat."""

import runpy

import pytest


@pytest.mark.parametrize("schema", ["postgres", "postgresql", "postgresql+psycopg2"])
def test_postgres_engine_gebruikt_geinstalleerde_driver(monkeypatch, schema):
    monkeypatch.setenv("DATABASE_URL", f"{schema}://test:geen-geheim@voorbeeld.invalid/fieldops?sslmode=require")
    module = runpy.run_module("database")
    engine = module["engine"]
    try:
        assert engine.dialect.driver == "psycopg2"
        assert engine.url.database == "fieldops"
        assert engine.url.query["sslmode"] == "require"
    finally:
        engine.dispose()
