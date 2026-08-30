from __future__ import annotations

from pathlib import Path

import pytest

from wendeburg_calendar.db.repository import Repository

FIXTURES_DIR = Path(__file__).parent / "fixtures"
WENDEBURG_BASIC_FIXTURE = FIXTURES_DIR / "wendeburg_basic"
MULTI_SOURCE_FIXTURE = FIXTURES_DIR / "multi_source"


@pytest.fixture()
def repo(tmp_path):
    r = Repository.connect(tmp_path / "test.sqlite3")
    yield r
    r.close()
