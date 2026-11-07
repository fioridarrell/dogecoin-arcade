"""settings.json is never written from a read that failed (2026-10-31, issue #51): on a Windows install `auto_update:
false` disappeared, and updates turned themselves back on. settings() answers {} when the file cannot be read for a
moment, and set_setting wrote that back with its one key -- every other setting gone."""

import json
import pathlib
import threading
import types

import pytest

from arcade.web.state import AppState


def _state(home):
    me = types.SimpleNamespace(home=home, SETTINGS_FILE=AppState.SETTINGS_FILE, _settings_lock=threading.Lock())
    me.settings = lambda: AppState.settings(me)
    return me


def test_a_moment_the_file_cannot_be_read_loses_nothing(tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text(json.dumps({"auto_update": False, "core:main": "here"}))
    me = _state(tmp_path)
    real, misses = pathlib.Path.read_text, [2]

    def flaky(self, *a, **k):
        if self.name == "settings.json" and misses[0]:
            misses[0] -= 1
            raise PermissionError("in use by another process")
        return real(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", flaky)
    AppState.set_setting(me, "theme", "dark")
    assert json.loads(real(tmp_path / "settings.json")) == {"auto_update": False, "core:main": "here", "theme": "dark"}


def test_a_file_that_stays_unreadable_is_left_as_it_was(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"auto_update": False}))
    me = _state(tmp_path)

    def never(self, *a, **k):
        raise PermissionError("in use by another process")
    monkeypatch.setattr(pathlib.Path, "read_text", never)
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(OSError, match="was not saved"):
        AppState.set_setting(me, "theme", "dark")
    monkeypatch.undo()
    assert json.loads(path.read_text()) == {"auto_update": False}, "the file was not rewritten with one key"
