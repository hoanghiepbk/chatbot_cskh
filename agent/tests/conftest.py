"""Shared fixtures: load .env so DB-backed tests find local Supabase.

DB-backed tests (marked with `db`) skip automatically when Supabase env is absent
(e.g. CI) — FakeLLM tests never need network, DB or API keys.
"""

import os

import pytest

from app.config import load_dotenv_if_present

load_dotenv_if_present()

requires_db = pytest.mark.skipif(
    not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY")),
    reason="requires local Supabase (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY)",
)


@pytest.fixture(scope="session")
def supabase():
    from supabase import create_client

    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )
