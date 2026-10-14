import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db import make_engine

BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def database():
    if os.environ.get("BUILDPILOT_TEST_POSTGRES") != "1":
        pytest.skip("Real PostgreSQL tests require BUILDPILOT_TEST_POSTGRES=1")
    schema = "bptest_" + uuid4().hex
    admin = make_engine()
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = make_engine(schema=schema)
    try:
        with engine.begin() as connection:
            config = Config(str(BACKEND / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
