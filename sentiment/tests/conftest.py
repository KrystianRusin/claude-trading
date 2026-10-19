import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402
import paths  # noqa: E402


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A fresh data folder initialised from the defaults, plus a HALT path next to it."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(paths, "halt_file", lambda: tmp_path / "HALT")
    config.init_data()
    return tmp_path / "data"
