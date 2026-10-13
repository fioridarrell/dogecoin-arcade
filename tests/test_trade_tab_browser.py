"""A browser tab takes another arcade's swap and settles it through lock scripts, end to end.

2026-10-13: trades "only available if they leave their tab open so that transaction
building is complete on the client side solely ... 100% trustless with lock scripts", carried
by "the Pepe coin test net node to node communication protocol".

Node B is a facilitating node (NodeTrader) standing an order: 2 DOGE for 3 PEP. A person signs
up on node A in a real Chromium tab, gets some PEP, and presses Take (wallet.takeOrder). From
then on the test only mines blocks, runs each node's scanner and index as its watcher would,
and lets each side take its own steps -- the tab with wallet.tradeTick, node B with tick().
The tab ends with B's DOGE, B with the tab's PEP, and nobody held anybody's coins.
"""

import pathlib
import sys
import threading
import time

import pytest

pytest.importorskip("playwright.sync_api", reason="needs playwright")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_htlc import Key, doge                                       # noqa: E402,F401
from test_htlc_browser import _free_port                              # noqa: E402
from test_node_trader import _ctx, _scan                              # noqa: E402

COIN = 100_000_000
_AWAY = Key().address


def _mine(*rpcs):
    for rpc in rpcs:
        rpc.call("generatetoaddress", 1, _AWAY)


@pytest.fixture
def world(tmp_path, regtest, doge, monkeypatch):
    import uvicorn
    from arcade.messaging.keys import Identity
    from arcade.messaging.sender import funded_address
    from arcade.node_trader import NodeTrader
    from arcade.web.app import create_app
    from arcade.web.state import AppState, ChainContext

    for rpc in (regtest.rpc, doge.rpc):
        if float(rpc.call("getbalance")) < 50:
            rpc.call("generate", 101)
    # Dogecoin counts as synced here (the test chain is the whole chain).
    real = AppState.chain_sync
    monkeypatch.setattr(AppState, "chain_sync", lambda self, c: {"ready": True, "progress": 1.0, "online": True}
                        if c.is_doge else real(self, c))
    pep_a = _ctx(regtest, "regtest")
    pep_a.role = "messaging"
    a = AppState(home=tmp_path / "a", messaging=pep_a,
                 ledger=ChainContext(network="main", role="ledger", label="Mainnet",
                                     datadir=pathlib.Path("/nonexistent")),
                 doge=_ctx(doge, "doge"))
    (tmp_path / "a").mkdir(parents=True, exist_ok=True)
    (tmp_path / "a" / "tokens-chain").write_text("regtest\n")
    a.identity = Identity.generate()
    # Node B: a facilitating node with its own home, identity and pinned message address.
    pep_b = _ctx(regtest, "regtest")
    b_state = AppState(home=tmp_path / "b", messaging=pep_b,
                       ledger=ChainContext(network="main", role="ledger", label="Mainnet",
                                           datadir=pathlib.Path("/nonexistent")))
    b_state.identity = Identity.generate()
    home = regtest.rpc.call("getnewaddress")
    regtest.rpc.call("sendtoaddress", home, 25)
    with b_state.store() as store:
        store.set_meta(f"identity_address:{pep_b.network}", home)
    b = NodeTrader(b_state, {"regtest": pep_b, "doge": _ctx(doge, "doge")}, pep_b)
    _mine(regtest.rpc)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(a), host="127.0.0.1", port=port,
                                           log_level="error", timeout_graceful_shutdown=1.0))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}", a, b, regtest.rpc, doge.rpc
    server.should_exit = True
    thread.join(timeout=5)


def _catch_up(state):
    from test_account_feed import _catch_up as feed_catch_up
    feed_catch_up(state)
    for chain in state.token_chains:
        try:
            state.token_index(chain).sync()
        except Exception:
            pass


def _scan_a(state):
    """Node A as its watcher would: the mail (candidates for its accounts) and the index."""
    from arcade.messaging.scanner import Scanner
    with state.messaging.rpc() as node, state.store() as store:
        scanner = Scanner(node, state.messaging.params, store, identity=state.identity)
        scanner.scan()
        scanner.scan_mempool()
    _catch_up(state)


def test_a_tab_takes_a_swap_and_both_sides_settle(world):
    from playwright.sync_api import sync_playwright
    base, a, b, pep, dg = world
    b.stand("order-dogepep", {"network": "doge", "kind": "coin", "amount": 2 * COIN},
            {"network": "regtest", "kind": "coin", "amount": 3 * COIN})
    order = {"id": "order-dogepep",
             "give": {"network": "doge", "kind": "coin", "asset": "", "amount": 2 * COIN},
             "get": {"network": "regtest", "kind": "coin", "asset": "", "amount": 3 * COIN},
             "trade_key": b.key.pubkey.hex(), "msg_key": b.state.identity.public_bytes.hex(),
             "node": "ab" * 32, "expires": int(time.time()) + 3600}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        tab = browser.new_page()
        tab.goto(f"{base}/join")
        tab.evaluate("async () => { window.w = await import('/wallet.js'); }")
        made = tab.evaluate("""async (net) => {
            const r = await w.signUp('swapper' + Math.floor(Math.random() * 1e6), 'a long enough password',
                                     {network: net.network, version: net.version});
            return {regtest: r.wallet.on.regtest.address, doge: r.wallet.on.doge && r.wallet.on.doge.address}; }""",
            {"network": "regtest", "version": 111})
        assert made["doge"], "the tab has a Dogecoin key (D main open, test chain synced)"
        pep.call("sendtoaddress", made["regtest"], 10)
        _mine(pep)
        _scan_a(a)
        trade = tab.evaluate("""async (order) => {
            const net = {network: 'regtest', version: 111};
            const wal = await w.opened(net);
            const t = await w.takeOrder(wal, order); return t.id; }""", order)
        steps = []
        for _ in range(45):
            _mine(pep, dg)
            _scan_a(a)
            _scan(b)
            for done in b.tick():
                steps.append(("b",) + done[1:2])
            _scan_a(a)
            got = tab.evaluate("""async (id) => {
                const wal = await w.opened({network: 'regtest', version: 111});
                const t = await w.tradeTick(wal, id); return [t.state, t.step || '', t.note || '']; }""", trade)
            steps.append(("tab", got[1] or got[0], got[2]))
            if got[0] == "done" and all(t.state == "done" for t in [b.book.get(trade)] if t):
                break
        browser.close()
    assert ("tab", "done", "I claimed their lock") in steps or any(s[0] == "tab" and s[1] == "done" for s in steps), steps[-8:]
    assert b.book.get(trade).state == "done", steps[-8:]
    tab_steps = [s[1] for s in steps if s[0] == "tab"]
    assert "lock" in tab_steps and "claim" in tab_steps, tab_steps
