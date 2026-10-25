"""The shared walk across arcades (2026-10-25): every segment read by two arcades, canaries in every job, a
segment two readers disagree on read again here, the arcade that was wrong left out, and only what this chain says
is real filed. The operator: "Twice + canaries" -- "the odds of a bug happening and the odds of a spot check catching the
bug would be extremely low"."""
import contextlib
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from arcade import coinlist, utxos
from arcade.script import b58check_encode
from arcade.web import app as appmod
from arcade.web.app import create_app
from arcade.web.state import AppState, ChainContext


def _addr(params, seed, sh=False):
    h = hashlib.new("ripemd160", hashlib.sha256(seed.encode()).digest()).digest()
    return b58check_encode(params.scripthash_version if sh else params.pubkeyhash_version, h)


def _tx(n):
    return hashlib.sha256(str(n).encode()).hexdigest()


def _arcade(tmp_path):
    (tmp_path / "here").mkdir()
    st = AppState(home=tmp_path / "here",
                  messaging=ChainContext(network="regtest", role="messaging", label="Testnet",
                                         datadir=Path("/nonexistent")),
                  ledger=ChainContext(network="main", role="ledger", label="Mainnet", datadir=Path("/nonexistent")))
    TestClient(create_app(st))
    return st


def test_a_walk_is_read_twice_wrong_readers_rest_and_only_real_coins_are_filed(tmp_path, no_nodes, monkeypatch):
    here = _arcade(tmp_path)
    chain = here.messaging
    P = chain.params
    W1, W2 = _addr(P, "imported one"), _addr(P, "imported two")
    CAN_PKH, CAN_SH = _addr(P, "canary"), _addr(P, "canary sh", sh=True)
    HI = 999
    # the chain: the wallets' outputs, and outputs to addresses this arcade already watches (the canaries' pool)
    chain_outputs = [[_tx(i), 0, 1000 + i, h, a] for i, (h, a) in enumerate(
        [(900, W1), (700, W2), (450, W1), (120, W2), (30, W1)])]
    known = [[_tx(f"{a}{h}"), 1, 5000, h, a] for h in range(10, HI, 60) for a in (CAN_PKH, CAN_SH)]
    spent = {_tx(3)}                                    # W2's coin at 120 was spent since
    with contextlib.closing(here.token_index(chain).open()) as db:
        for a in (CAN_PKH, CAN_SH):
            utxos.watch(db, a, 0, why="test")
        for txid, vout, sats, h, a in known:
            db.conn.execute("INSERT INTO utxo VALUES (?,?,?,?,?)", (txid, vout, a, sats, h))

    def honest(top, bottom, addresses):
        return [m for m in chain_outputs + known if bottom <= m[3] <= top and m[4] in addresses]

    behaviour = {
        "self": honest,
        "good.example.com": honest,
        "liar.example.com": lambda t, b, a: [m for m in honest(t, b, a) if m[0] != _tx(2)],   # drops W1's coin at 450
        "blind.example.com": lambda t, b, a: [m for m in honest(t, b, a) if m[4] != CAN_SH],  # misses P2SH outputs
    }
    readers = ["good.example.com", "liar.example.com", "blind.example.com"]
    monkeypatch.setattr(type(chain), "status", lambda self: {"online": True})

    class Rpc:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def batch(self, calls):
            out = []
            for _, (txid, vout, mempool) in calls:
                m = next((m for m in chain_outputs if m[0] == txid and m[1] == vout), None)
                out.append(None if m is None or txid in spent else
                           {"confirmations": HI - m[3] + 1, "value": m[2] / 1e8, "scriptPubKey": {"addresses": [m[4]]}})
            return out

        def get_block_count(self):
            return HI
    monkeypatch.setattr(type(chain), "rpc", lambda self: Rpc())
    from arcade import coinwalk
    monkeypatch.setattr(coinwalk.Reader, "can", lambda self, c: {"files": True, "rpc": True})
    monkeypatch.setattr(coinwalk.Reader, "read",
                        lambda self, c, top, bottom, addresses, progress=None: behaviour["self"](top, bottom, addresses))

    jobs = {}

    class Answer:
        def __init__(self, body, code=200):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

    def ask(url, **kw):
        host = url.split("/")[2]
        if "/coins/walk/can" in url:
            return Answer({"files": True, "rpc": True})
        jid = url.rsplit("/", 1)[1]
        return Answer({"state": "done", "matches": jobs[jid]})

    def post(url, **kw):
        host, body = url.split("/")[2], kw["json"]
        jid = f"{host}-{len(jobs)}"
        jobs[jid] = behaviour[host](body["top"], body["bottom"], body["addresses"])
        return Answer({"id": jid})
    monkeypatch.setattr(appmod, "_ask_arcade", ask)
    monkeypatch.setattr(appmod, "_post_arcade", post)
    monkeypatch.setattr(appmod, "WALK_POLL", 0)
    with here.store() as store:
        for i, h in enumerate(readers):
            store.add_mesh_announcement(f"t{i}", here.messaging.network, f"n{i}", f"wss://{h}/mesh", 443, f"{i:02x}" * 32, 0, 0)

    got = here.shared_walk(chain, [W1, W2], 0, HI)
    assert set(got["readers"]) == {"self", *readers}
    assert got["verified"] == [[0, HI]], "every segment ends verified: agreed by two, or read again here"
    assert {r["segment"] for r in got["redone"]}, "the liar's and the blind reader's segments were read again"
    assert not got["failed"]
    assert got["filed"] == 4, "five coins found, one spent since: four filed, each checked on this chain"
    with contextlib.closing(here.token_index(chain).open()) as db:
        assert {c["txid"] for c in utxos.unspent(db, W1)} == {_tx(0), _tx(2), _tx(4)}, "the liar's dropped coin too"
        assert {c["txid"] for c in utxos.unspent(db, W2)} == {_tx(1)}
        assert coinlist.covers(coinlist.coverage(db, W1, HI), 0, HI)
    assert "liar.example.com" in here.walk_resting and "blind.example.com" in here.walk_resting
    assert "good.example.com" not in here.walk_resting, "the honest reader of a disagreement is not punished"


def test_segments_with_one_reader_are_read_once_with_canaries(tmp_path):
    jobs = coinlist.segments(0, 99, ["self"])
    assert jobs == [{"reader": "self", "top": 99, "bottom": 0, "segment": 0}]
