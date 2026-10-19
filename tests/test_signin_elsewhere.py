"""Signing in with an account made on another arcade, without naming it (2026-10-19: "I'm still not able to
login with my account created on dogecoinarcade.com through pepecoinarcade.com ... I shouldn't have to enter a URL for
the other Arcade. It should just know automatically")."""
import json

from fastapi.testclient import TestClient

from arcade.web import app as appmod
from arcade.web.app import create_app
from arcade.web.state import AppState, ChainContext

BLOB = {"sealed": "c2VhbGVk", "salt": "c2FsdA==", "v": 1}


def _arcade(tmp_path, name):
    from pathlib import Path
    (tmp_path / name).mkdir()
    st = AppState(home=tmp_path / name,
                  messaging=ChainContext(network="regtest", role="messaging", label="Testnet",
                                         datadir=Path("/nonexistent")),
                  ledger=ChainContext(network="main", role="ledger", label="Mainnet", datadir=Path("/nonexistent")))
    return st, TestClient(create_app(st))


def test_a_name_made_on_another_arcade_is_found_there_by_itself(tmp_path, no_nodes, monkeypatch):
    home, home_client = _arcade(tmp_path, "home")
    here, here_client = _arcade(tmp_path, "here")
    home.vault().put("wanderer", "ab" * 32, "nWandererAddressXXXXXXXXXXXXXXXXXXX", json.dumps(BLOB))
    with here.store() as store:                       # the home arcade's mesh, as its announcement says it
        store.add_mesh_announcement("t1", here.messaging.network, "nHome", "wss://home.example.com/mesh", 443,
                                    "cd" * 32, 0, 0)

    class Answer:
        def __init__(self, r):
            self.status_code, self._r = r.status_code, r

        def json(self):
            return self._r.json()

    asked = []

    def get(url, timeout=0, headers=None):
        asked.append(url)
        assert url.startswith("https://home.example.com/signin/wanderer") and "here=1" in url
        return Answer(home_client.get(url.replace("https://home.example.com", "")))
    monkeypatch.setattr(appmod.requests, "get", get)

    said = here_client.get("/signin/wanderer").json()
    assert said["elsewhere"] is True and said["from"] == "https://home.example.com" and said["blob"] == BLOB
    assert said["address"] == "nWandererAddressXXXXXXXXXXXXXXXXXXX"
    assert home_client.get("/signin/nobody?here=1").status_code == 404, "an arcade asked never asks on"
    here_client.get("/signin/wanderer")
    assert len(asked) == 1, "kept a while, not asked again"
