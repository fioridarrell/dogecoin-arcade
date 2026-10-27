"""The T-Deck builds and signs its own transactions (2026-10-26: "Can't the T-Deck build the transaction?"), and
asks for the coins it may spend -- never one carrying an inscription ("Is it going to protect ord coins from
spending?"). /account/coins?spendable=1 lists only those, and /account/broadcast sends only a transaction spending them."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client                          # noqa: F401,E402
from test_doginal_wallet import _doge_account, ME, T1, T3        # noqa: E402

from arcade import funding                                       # noqa: E402
from arcade.txbuild import p2pkh_script                          # noqa: E402


class _Core:
    def __init__(self, vins, sent):
        self.vins, self.sent = vins, sent

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def call(self, method, *params):
        if method == "decoderawtransaction":
            return {"vin": [{"txid": t, "vout": v} for t, v in self.vins],
                    "vout": [{"value": 2.99, "scriptPubKey": {"hex": p2pkh_script(ME).hex()}}]}
        if method == "sendrawtransaction":
            self.sent.append(params[0])
            return "ab" * 32
        raise AssertionError(method)


def _core(monkeypatch, doge, vins):
    sent = []
    monkeypatch.setattr(doge, "rpc", lambda: _Core(vins, sent), raising=False)
    return sent


def test_spendable_lists_only_coins_without_an_inscription(client, monkeypatch, tmp_path):
    app, state = client
    _doge_account(app, state, monkeypatch, tmp_path)
    every = app.get("/account/coins?chain=doge").json()["coins"]
    assert len(every) == 3
    free = app.get("/account/coins?chain=doge&spendable=1").json()["coins"]
    assert [(c["txid"], c["value"]) for c in free] == [(T3, 300_000_000)]


def test_a_coin_nobody_can_judge_is_left_out(client, monkeypatch, tmp_path):
    app, state = client
    _doge_account(app, state, monkeypatch, tmp_path, answers={})        # the chain cannot be read just now
    free = app.get("/account/coins?chain=doge&spendable=1").json()["coins"]
    assert all(c["value"] > 100_000 for c in free)                     # the small coins stay out


def test_broadcast_sends_a_transaction_of_its_own_clean_coins(client, monkeypatch, tmp_path):
    app, state = client
    doge, _ = _doge_account(app, state, monkeypatch, tmp_path)
    sent = _core(monkeypatch, doge, [(T3, 0)])
    said = app.post("/account/broadcast", json={"chain": "doge", "raw": "00ff"})
    assert said.status_code == 200, said.text
    assert said.json() == {"txid": "ab" * 32} and sent == ["00ff"]
    # remembered: the coin it spent is gone and its change is the account's, already spendable
    after = {(c["txid"], c["vout"]) for c in app.get("/account/coins?chain=doge&spendable=1").json()["coins"]}
    assert (T3, 0) not in after and ("ab" * 32, 0) in after


def test_broadcast_refuses_an_inscribed_coin(client, monkeypatch, tmp_path):
    app, state = client
    doge, _ = _doge_account(app, state, monkeypatch, tmp_path)
    sent = _core(monkeypatch, doge, [(T3, 0), (T1, 0)])
    said = app.post("/account/broadcast", json={"chain": "doge", "raw": "00ff"})
    assert said.status_code == 400 and "inscription" in said.json()["detail"] and sent == []


def test_broadcast_refuses_someone_elses_coin(client, monkeypatch, tmp_path):
    app, state = client
    doge, _ = _doge_account(app, state, monkeypatch, tmp_path)
    sent = _core(monkeypatch, doge, [("44" * 32, 0)])
    said = app.post("/account/broadcast", json={"chain": "doge", "raw": "00ff"})
    assert said.status_code == 400 and "not this account" in said.json()["detail"] and sent == []


def test_broadcast_is_a_public_post_for_signed_in_accounts():
    from arcade.web import door
    assert "/account/broadcast" in door.PUBLIC_POST
    assert funding.NOTE_COINS is not None
