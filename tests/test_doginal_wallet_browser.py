"""The Wallet's Doginals in a real browser: the page asks a (fake) Doge Soft about the account's own Dogecoin coins,
keeps each answer, shows the Doginal with a Send button, and the Send goes as far as the review that names the Doginal
and the recipient. And when Doge Soft cannot be reached the page says so (2026-10-14: "I want the client side to
access Doge soft"). Nothing here asks the real Doge Soft."""

import json
import pathlib
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("selenium", reason="browser tests need selenium: pip install .[dev]")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browsers                                                  # noqa: E402
from test_funding import _pubkey                                 # noqa: E402

from selenium.webdriver.common.by import By                      # noqa: E402

from arcade import doginals, funding                             # noqa: E402
from arcade.script import b58check_encode, hash160               # noqa: E402

T1, T3 = "11" * 32, "33" * 32
INS = T1 + "i0"
THEM = b58check_encode(30, hash160(_pubkey(int("32" * 32, 16))))
ANSWERS = {T1: {"txid": T1, "inscriptions": [{"vout": 0, "inscription_id": INS, "sat_offset": 0}]},
           T3: {"txid": T3, "inscriptions": []}}
#: A 1x1 PNG, for the preview.
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def dogesoft():
    """A Doge Soft that answers from ANSWERS and lets any origin read it, counting what it is asked."""
    asked = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            asked.append(self.path)
            if self.path.endswith("/content"):
                body, kind = PNG, "image/png"
            else:
                txid = self.path.rsplit("/", 1)[-1]
                body, kind = json.dumps(ANSWERS.get(txid, {"txid": txid, "inscriptions": []})).encode(), \
                    "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", _free_port()), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", asked
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def browser():
    driver = browsers.launch()
    yield driver
    driver.quit()


@pytest.fixture(scope="module")
def served(tmp_path_factory, dogesoft):
    import uvicorn
    from pathlib import Path
    from arcade.web.app import create_app
    from arcade.web.state import AppState, ChainContext

    state = AppState(
        home=tmp_path_factory.mktemp("dgwallet"),
        messaging=ChainContext(network="regtest", role="messaging", label="Testnet", datadir=Path("/nonexistent")),
        ledger=ChainContext(network="main", role="ledger", label="Mainnet", datadir=Path("/nonexistent")),
        doge=ChainContext(network="doge", role="ledger", label="Dogecoin", conf=Path("/nonexistent/dogecoin.conf")),
    )
    state._sync["doge"] = (time.time() + 86400, {"ready": True, "progress": 1.0, "online": True,
                                                 "blocks": 100, "headers": 100})
    real_base, real_guard = doginals.DOGESOFT, funding.GUARD
    doginals.DOGESOFT = dogesoft[0]
    app = create_app(state)
    # The node asks the fake Doge Soft the real way (2026-10-15: the Wallet's check goes through this node, since
    # Doge Soft sends no CORS header for the arcade), so `asked` counts the node's questions.
    funding.GUARD = doginals.Doginals(state.home / "dg.sqlite")
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error",
                                           timeout_graceful_shutdown=1.0))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        pytest.skip("test server did not start")
    yield f"http://127.0.0.1:{port}", state
    server.should_exit = True
    thread.join(timeout=5)
    doginals.DOGESOFT, funding.GUARD = real_base, real_guard


@pytest.fixture(scope="module")
def signed_in(browser, served):
    """An account made the way /join makes one, with two coins on its Dogecoin address: one carrying a Doginal."""
    base, state = served
    browser.get(f"{base}/join")
    browser.delete_all_cookies()
    result = browser.execute_async_script("""
        const done = arguments[2];
        import("/wallet.js").then(async (w) => {
          await w.signUp(arguments[0], arguments[1], {network: "regtest", version: 111});
          const me = await w.opened({network: "regtest", version: 111});
          await w.tellTheNode(me);
          const row = (await w.state()).chains.find((c) => c.network === "doge");
          done({ok: true, address: row && row.address});
        }).catch((e) => done({ok: false, error: String(e.message || e)}));
        """, "dgwallettester", "a password nobody needs to guess for this test")
    assert result.get("ok") and str(result.get("address", "")).startswith("D"), result
    with state.token_index(state.doge).open() as db:
        for txid, value in ((T1, 100_000), (T3, 300_000_000)):
            db.conn.execute("INSERT OR REPLACE INTO utxo(txid,vout,address,value,height) VALUES (?,?,?,?,?)",
                            (txid, 0, result["address"], value, 1))
        db.conn.commit()
    return result["address"]


def _open_doginals(browser, base):
    browser.get(f"{base}/me/wallet")
    for _ in range(80):
        if browser.execute_script("return document.body.dataset.walletReady === 'yes'"):
            break
        time.sleep(0.25)
    browser.find_element(By.ID, "tab-nfts").click()
    doge = [b for b in browser.find_elements(By.CSS_SELECTOR, "#nets button") if b.text.strip().startswith("D")]
    assert doge, "the Dogecoin button"
    doge[0].click()


def _wait(browser, script, seconds=20):
    for _ in range(seconds * 4):
        got = browser.execute_script(script)
        if got:
            return got
        time.sleep(0.25)
    raise AssertionError(f"never: {script}\n{browser.find_element(By.ID, 'doginals').text}")


def test_the_wallet_finds_its_doginal_asks_once_and_sends_it_to_review(browser, served, signed_in, dogesoft):
    base, _ = served
    url, asked = dogesoft
    browser.execute_script("localStorage.clear()")
    _open_doginals(browser, base)
    _wait(browser, f'return document.querySelector("#doginals [data-doginal=\\"{INS}\\"]")')
    text = browser.execute_script('return document.getElementById("doginals").textContent')
    assert "Doginals" in text and INS in text
    src = browser.execute_script(f'return document.querySelector("#doginals [data-doginal=\\"{INS}\\"] img").src')
    assert src == f"{url}/api/explorer/inscriptions/{INS}/content"
    assert len(browser.find_elements(By.CSS_SELECTOR, "#doginals [data-doginal]")) == 1, "the clean coin is not one"
    tx_asks = [p for p in asked if "/api/explorer/tx/" in p]
    assert sorted(tx_asks) == sorted([f"/api/explorer/tx/{T1}", f"/api/explorer/tx/{T3}"])

    _open_doginals(browser, base)            # again: answered from this browser (and the node), Doge Soft not asked
    _wait(browser, f'return document.querySelector("#doginals [data-doginal=\\"{INS}\\"]")')
    assert [p for p in asked if "/api/explorer/tx/" in p] == tx_asks

    browser.find_element(By.CSS_SELECTOR, f'#doginals [data-doginal="{INS}"] button').click()
    to = _wait(browser, 'return document.getElementById("send-to")')
    to.send_keys(THEM)
    review = [b for b in browser.find_elements(By.CSS_SELECTOR, ".wl-sheet button.wl-go") if b.text == "Review"]
    review[0].click()
    sheet = _wait(browser, 'const s = document.querySelector(".wl-sheet");'
                           ' return s && s.textContent.includes("You\'re sending") && s.textContent')
    assert THEM in sheet and "Doginal" in sheet, sheet


def test_an_unreachable_doge_soft_is_said_plainly(browser, served, signed_in):
    base, _ = served
    browser.execute_script("localStorage.clear()")
    dead = f"http://127.0.0.1:{_free_port()}"
    real, real_guard = doginals.DOGESOFT, funding.GUARD
    doginals.DOGESOFT = dead
    # A node that has kept no answers yet: it asks, and Doge Soft is not there.
    funding.GUARD = doginals.Doginals(served[1].home / "dg-empty.sqlite")
    try:
        _open_doginals(browser, base)
        _wait(browser, 'return document.getElementById("doginals").textContent'
                       '.includes("Doginals can\'t be checked right now")')
        assert not browser.find_elements(By.CSS_SELECTOR, "#doginals [data-doginal]"), "nothing guessed"
    finally:
        doginals.DOGESOFT, funding.GUARD = real, real_guard
