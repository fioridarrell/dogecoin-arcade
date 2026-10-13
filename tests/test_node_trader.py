"""Two facilitating nodes trade PEP for DOGE with nothing but the Messenger and the chains
(arcade/node_trader.py; 2026-10-13: node-facilitated trades "just like a regular
lock script that the client side does").

Node B stands an order (2 DOGE for 3 PEP). Node A proposes against it. After that the
test only mines blocks and runs each node's message scanner, as the watcher does; every
step -- accept, lock, lock, claim (revealing the secret), claim -- is the traders' own.
"""

import pathlib
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_htlc import Key, doge                                        # noqa: E402,F401

from arcade import trade as T                                          # noqa: E402

COIN = 100_000_000


def _ctx(node, network):
    """A chain context pointed at a regtest node, named as the arcade names the chain."""
    from arcade.web.state import ChainContext

    class Pointed(ChainContext):
        def credentials(self):
            return node.rpc._creds

        @property
        def params(self):
            return node.params

    return Pointed(network=network, role="ledger", label=network, datadir=node.datadir)


@pytest.fixture
def market(tmp_path, regtest, doge):
    """Two nodes, each with its own home and messaging identity; PEP regtest carries the
    messages and one leg, Dogecoin regtest the other."""
    from arcade.messaging.keys import Identity
    from arcade.messaging.sender import funded_address
    from arcade.node_trader import NodeTrader
    from arcade.web.state import AppState, ChainContext

    for rpc in (regtest.rpc, doge.rpc):
        if float(rpc.call("getbalance")) < 50:
            rpc.call("generate", 101)
    traders = []
    for name in ("a", "b"):
        pep = _ctx(regtest, "regtest")
        state = AppState(home=tmp_path / name, messaging=pep,
                         ledger=ChainContext(network="main", role="ledger", label="Mainnet",
                                             datadir=pathlib.Path("/nonexistent")))
        state.identity = Identity.generate()
        # Pin the address this node's messages are paid from, as a live node's identity
        # address is: funded with ordinary change (never the mined coins a block pays).
        home = regtest.rpc.call("getnewaddress")
        regtest.rpc.call("sendtoaddress", home, 25)
        with state.store() as store:
            store.set_meta(f"identity_address:{pep.network}", home)
        traders.append(NodeTrader(state, {"regtest": pep, "doge": _ctx(doge, "doge")}, pep))
    regtest.rpc.call("generate", 1)
    return traders[0], traders[1], regtest.rpc, doge.rpc


#: Blocks are mined to an address outside every wallet here: a mined block pays bare
#: pay-to-pubkey, which no message may be funded from, and a wallet that kept them would
#: offer them first (as a live node that does not mine never has).
_AWAY = Key().address


def _mine(*rpcs):
    for rpc in rpcs:
        rpc.call("generatetoaddress", 1, _AWAY)


def _scan(trader):
    from arcade.messaging.scanner import Scanner
    st = trader.state
    with st.messaging.rpc() as node, st.store() as store:
        scanner = Scanner(node, st.messaging.params, store, identity=st.identity)
        scanner.scan()            # what was mined
        scanner.scan_mempool()    # and what is on its way, as the watcher reads both


def test_two_nodes_trade_pep_for_doge_over_the_messenger(market):
    a, b, pep, dg = market
    # B stands an order: it gives 2 DOGE (its own trade key) for 3 PEP.
    b.stand("order-dogepep", {"network": "doge", "kind": "coin", "amount": 2 * COIN},
            {"network": "regtest", "kind": "coin", "amount": 3 * COIN})
    give = a.leg("regtest", 3 * COIN, b.key.pubkey)             # A gives 3 PEP to B
    get = T.Leg("doge", "coin", "", 2 * COIN, b.key.pubkey.hex(), a.key.pubkey.hex())
    trade = a.propose(b.state.identity.public_bytes, "order-dogepep", give, get)

    log = []
    for _ in range(40):
        for t in (a, b):
            _scan(t)
            log.extend((("a" if t is a else "b"),) + r for r in t.tick())
        _mine(pep, dg)
        if a.book.get(trade.id).state == "done" and (b.book.get(trade.id) or trade).state == "done":
            break
    steps = [(who, step) for who, _, step, _ in log]
    assert a.book.get(trade.id).state == "done", log[-6:]
    assert b.book.get(trade.id).state == "done", log[-6:]
    assert ("a", T.LOCK) in steps and ("b", T.LOCK) in steps, "both locked"
    assert steps.index(("a", T.LOCK)) < steps.index(("b", T.LOCK)), "the initiator locked first"
    assert ("a", T.CLAIM) in steps and ("b", T.CLAIM) in steps, "both claimed"
    assert steps.index(("a", T.CLAIM)) < steps.index(("b", T.CLAIM)), \
        "the responder claimed only after the initiator revealed the secret"


def test_a_proposal_against_no_order_is_cancelled_and_costs_nothing(market):
    a, b, pep, dg = market
    give = a.leg("regtest", 3 * COIN, b.key.pubkey)
    get = T.Leg("doge", "coin", "", 2 * COIN, b.key.pubkey.hex(), a.key.pubkey.hex())
    trade = a.propose(b.state.identity.public_bytes, "order-nonexist", give, get)
    for _ in range(3):
        for t in (a, b):
            _scan(t)
            t.tick()
        _mine(pep)
    assert a.book.get(trade.id).state == "aborted", "A walks away; it never locked"
    assert b.book.get(trade.id) is None, "B never took it on"


def test_the_switch_off_means_the_node_does_nothing(market):
    a, b, pep, dg = market
    b.enabled = lambda: False
    assert b.tick() == [], "node-facilitated trades off: the node trader is idle"


def _gold(trader, holder, units):
    """A token both nodes' ledgers know, with `units` of it at `holder` (as test_escrow)."""
    from test_account_tokens import _balance, _token
    for st in {id(t.state): t.state for t in trader}.values():
        _token(st, property_id=150, name="Gold")
        _balance(st, holder, 150, units)
    return 150


def _ledgers(*traders):
    for t in traders:
        t.state.token_index(t.state.messaging).sync()


def test_two_nodes_trade_a_token_for_doge(market, monkeypatch):
    """B gives 40 Gold on PEP regtest for A's 2 DOGE: the token goes into a lock with the
    coin that pays its way out, and the ledger -- not a message -- says it is there."""
    a, b, pep, dg = market
    home = pep.call("getnewaddress")
    pep.call("sendtoaddress", home, 5)
    monkeypatch.setattr(b.state, "home_address",
                        lambda chain: home if chain.network == "regtest" else "", raising=False)
    pid = _gold((a, b), home, 100 * COIN)
    _mine(pep)
    _ledgers(a, b)
    b.stand("order-gold", {"network": "regtest", "kind": "token", "asset": str(pid), "amount": 40 * COIN},
            {"network": "doge", "kind": "coin", "amount": 2 * COIN})
    give = a.leg("doge", 2 * COIN, b.key.pubkey)
    get = T.Leg("regtest", "token", str(pid), 40 * COIN, b.key.pubkey.hex(), a.key.pubkey.hex())
    trade = a.propose(b.state.identity.public_bytes, "order-gold", give, get)
    log = []
    for _ in range(40):
        for t in (a, b):
            _scan(t)
            _ledgers(t)
            log.extend((("a" if t is a else "b"),) + r for r in t.tick())
        _mine(pep, dg)
        if a.book.get(trade.id).state == "done" and (b.book.get(trade.id) or trade).state == "done":
            break
    assert a.book.get(trade.id).state == "done", log[-6:]
    assert b.book.get(trade.id).state == "done", log[-6:]
    _ledgers(a, b)
    index = a.state.token_index(a.state.messaging)
    claimed_to = a._address("regtest")
    assert int(index.balance(claimed_to, pid)) == 40 * COIN, "A holds the Gold"
    assert int(index.balance(home, pid)) == 60 * COIN, "B's home kept the rest"


def test_a_token_lock_the_ledger_has_not_read_yet_is_waited_for_not_walked_from(market):
    """A lagging index must not make a good token lock look wrong (asset_at -> None)."""
    from arcade import htlc as H
    a, b, pep, dg = market
    _, h = H.make_secret()
    leg = T.Leg("regtest", "token", "150", 40, Key().pub.hex(), Key().pub.hex())
    index = a.state.token_index(a.state.messaging)
    _ledgers(a)
    with index.open() as db:
        tip = int(db.tip()["height"])
    assert T.asset_at(index, leg, "nAddressXXXXXXXXXXXXXXXXXXXXXXXXXXX", tip + 5) is None
    assert T.asset_at(index, leg, "nAddressXXXXXXXXXXXXXXXXXXXXXXXXXXX", None) is None
    assert T.asset_at(index, leg, "nAddressXXXXXXXXXXXXXXXXXXXXXXXXXXX", tip) is False


def test_two_nodes_trade_an_nft_for_doge(market, monkeypatch):
    from test_escrow import _piece
    a, b, pep, dg = market
    home = pep.call("getnewaddress")
    pep.call("sendtoaddress", home, 5)
    monkeypatch.setattr(b.state, "home_address",
                        lambda chain: home if chain.network == "regtest" else "", raising=False)
    sword = "b1" * 32
    _mine(pep)
    _ledgers(a, b)
    for t in (a, b):
        _piece(t.state, sword, home)
    give = a.leg("doge", 2 * COIN, b.key.pubkey)
    get = T.Leg("regtest", "nft", sword, 1, b.key.pubkey.hex(), a.key.pubkey.hex())
    b.stand("order-sword", {"network": "regtest", "kind": "nft", "asset": sword, "amount": 1},
            {"network": "doge", "kind": "coin", "amount": 2 * COIN})
    trade = a.propose(b.state.identity.public_bytes, "order-sword", give, get)
    log = []
    for _ in range(40):
        for t in (a, b):
            _scan(t)
            _ledgers(t)
            log.extend((("a" if t is a else "b"),) + r for r in t.tick())
        _mine(pep, dg)
        if a.book.get(trade.id).state == "done" and (b.book.get(trade.id) or trade).state == "done":
            break
    assert a.book.get(trade.id).state == "done", log[-6:]
    assert b.book.get(trade.id).state == "done", log[-6:]
    _ledgers(a)
    assert a.state.token_index(a.state.messaging).inscription(sword)["owner"] == a._address("regtest")
