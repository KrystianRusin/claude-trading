import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import paths  # noqa: E402


@pytest.fixture
def state(tmp_path, monkeypatch):
    """A fresh state folder initialised from the defaults, plus a HALT path inside it."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(paths, "halt_file", lambda: tmp_path / "HALT")
    config.init_state()
    return tmp_path / "state"
