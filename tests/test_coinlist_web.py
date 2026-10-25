"""Coin lists between arcades (2026-10-25): every holder's list merged, each coin checked here, coverage
carried. The operator: "any of the current arcade accounts that are held by more than one arcade. Like @apple the UTXO's
and balances cross arcade need to be reconciled." """
import contextlib
import json

from fastapi.testclient import TestClient

from arcade import coinlist, utxos
from arcade.web import app as appmod
from arcade.web.app import create_app
from arcade.web.state import AppState, ChainContext

BLOB = {"sealed": "c2VhbGVk", "salt": "c2FsdA==", "v": 1}
ADDR = "nWandererAddressXXXXXXXXXXXXXXXXXXX"
PUB = "ab" * 32


def _arcade(tmp_path, name):
    from pathlib import Path
    (tmp_path / name).mkdir()
    st = AppState(home=tmp_path / name,
                  messaging=ChainContext(network="regtest", role="messaging", label="Testnet",
                                         datadir=Path("/nonexistent")),
                  ledger=ChainContext(network="main", role="ledger", label="Mainnet", datadir=Path("/nonexistent")))
    return st, TestClient(create_app(st))


def _holds(st, since):
    st.vault().put("wanderer", PUB, ADDR, json.dumps(BLOB))
    st.set_setting(f"address:{PUB}", ADDR)
    with contextlib.closing(st.token_index(st.messaging).open()) as db:
        utxos.watch(db, ADDR, since, why="test")
        db.conn.commit()


class _Rpc:            # this arcade's own chain: aa is real, bb is spent, cc pays somebody else
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def call(self, method, txid, vout, mempool):
        return {"aa" * 32: {"confirmations": 3, "value": 5, "scriptPubKey": {"addresses": [ADDR]}},
                "cc" * 32: {"confirmations": 3, "value": 9, "scriptPubKey": {"addresses": ["nSomebodyElse"]}}
                }.get(txid)

    def batch(self, calls):
        return [self.call(m, *p) for m, p in calls]

    def get_block_count(self):
        return 100


def test_the_public_list_has_every_address_coverage_and_coins(tmp_path, no_nodes):
    home, client = _arcade(tmp_path, "home")
    _holds(home, 10)
    with contextlib.closing(home.token_index(home.messaging).open()) as db:
        db.conn.execute("INSERT INTO utxo VALUES (?,?,?,?,?)", ("aa" * 32, 0, ADDR, 5 * 10**8, 20))
        coinlist.mark_covered(db, ADDR, [(0, 9)], "test")
        db.conn.commit()
    said = client.get("/signin/wanderer/coins").json()
    assert said["v"] == coinlist.VERSION
    mine = said["lists"][home.messaging.network][ADDR]
    assert mine["coins"] == [["aa" * 32, 0, 5 * 10**8, 20, ""]]
    assert mine["covered"][0][0] == 0, "watching from 10 plus the range read before it"
    assert said["chains"][home.messaging.network]["address"] == ADDR, "the old shape stays for older arcades"


def test_two_holders_reconcile_coins_checked_and_coverage_merged(tmp_path, no_nodes, monkeypatch):
    home, home_client = _arcade(tmp_path, "home")
    here, _ = _arcade(tmp_path, "here")
    _holds(home, 10)
    _holds(here, 50)                                    # here started watching later, and missed what came before
    with contextlib.closing(home.token_index(home.messaging).open()) as db:
        for txid in ("aa" * 32, "bb" * 32, "cc" * 32):
            db.conn.execute("INSERT INTO utxo VALUES (?,?,?,?,?)", (txid, 0, ADDR, 5 * 10**8, 20))
        coinlist.mark_covered(db, ADDR, [(0, 100)], "test")     # (a test index reads no blocks: its tip is 0)
        db.conn.commit()

    class Answer:
        status_code = 200

        def json(self):
            return home_client.get("/signin/wanderer/coins").json()
    monkeypatch.setattr(appmod.requests, "get", lambda url, timeout=0, headers=None, **kw: Answer())
    monkeypatch.setattr(appmod, "_host_is_public", lambda host: True)
    monkeypatch.setattr(type(here.messaging), "rpc", lambda self: _Rpc())

    got = here.reconcile_coins("wanderer", ["home.example.com"])
    assert got["asked"] == ["home.example.com"] and got["filed"] == 1, "only the coin this chain says is real and theirs"
    with contextlib.closing(here.token_index(here.messaging).open()) as db:
        assert [c["txid"] for c in utxos.unspent(db, ADDR)] == ["aa" * 32]
        tip = 100
        assert coinlist.covers(coinlist.coverage(db, ADDR, tip), 0, tip), "home vouched for 0..; here watched from 50"


def test_a_holder_on_older_code_still_gives_its_coins(tmp_path, no_nodes, monkeypatch):
    here, _ = _arcade(tmp_path, "here")
    _holds(here, 50)

    class Old:
        status_code = 200

        def json(self):
            return {"tag": "wanderer", "chains": {here.messaging.network: {
                "address": ADDR, "since": 10, "coins": [{"txid": "aa" * 32, "vout": 0, "value": 5, "height": 20}],
                "held": []}}}
    monkeypatch.setattr(appmod.requests, "get", lambda url, timeout=0, headers=None, **kw: Old())
    monkeypatch.setattr(appmod, "_host_is_public", lambda host: True)
    monkeypatch.setattr(type(here.messaging), "rpc", lambda self: _Rpc())
    assert here.reconcile_coins("wanderer", ["old.example.com"])["filed"] == 1
    with contextlib.closing(here.token_index(here.messaging).open()) as db:
        assert coinlist.coverage(db, ADDR, 100) == [(50, 100)], "an older arcade vouches for nothing"
