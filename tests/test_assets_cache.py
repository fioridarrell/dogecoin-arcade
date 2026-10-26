"""The scripts and the stylesheet every page loads, kept by the browser (2026-10-20: "measure the load times
... offer solutions"; every tab switch downloaded ~250 KB of the same code). Asked for by a fingerprinted address that
is kept for good; any change to any of them changes every address; pages themselves are never cached."""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client  # noqa: F401,E402

LOCAL = {"host": "127.0.0.1:8420"}
NAMES = ("/wallet.js", "/coins.js", "/signin.js", "/messaging.js", "/qrscan.js", "/base.css")


def test_pages_ask_for_every_asset_by_its_fingerprint_and_keep_none_themselves(client):
    app, state = client
    page = app.get("/join", headers=LOCAL)
    assert page.headers["cache-control"].startswith("no-store"), "a page is never kept"
    html = page.text
    marks = set(re.findall(r'/(?:wallet|coins|signin|messaging|qrscan)\.js\?v=([0-9a-f]{12})', html))
    marks |= set(re.findall(r'/base\.css\?v=([0-9a-f]{12})', html))
    assert len(marks) == 1, marks
    for name in NAMES:
        for q in ('"', "'", "`"):
            assert f"{q}{name}{q}" not in html, f"{name} asked for without its fingerprint"
    assert '<link rel="stylesheet" href="/base.css?v=' in html
    assert ":root{" not in html, "the shared style is no longer in every page"


def test_the_fingerprinted_address_is_kept_and_nothing_else_is(client):
    app, state = client
    html = app.get("/join", headers=LOCAL).text
    mark = re.search(r'/wallet\.js\?v=([0-9a-f]{12})', html).group(1)
    for name in NAMES:
        kept = app.get(f"{name}?v={mark}", headers=LOCAL)
        assert kept.status_code == 200 and kept.headers["cache-control"] == "public, max-age=31536000, immutable", name
        assert app.get(name, headers=LOCAL).headers["cache-control"] == "no-store", name
        assert app.get(f"{name}?v=000000000000", headers=LOCAL).headers["cache-control"] == "no-store", name
    assert app.get(f"/base.css?v={mark}", headers=LOCAL).headers["content-type"].startswith("text/css")
    wallet = app.get(f"/wallet.js?v={mark}", headers=LOCAL).text
    assert f'"/coins.js?v={mark}"' in wallet and f'"/signin.js?v={mark}"' in wallet, \
        "what a script imports carries the same fingerprint: one copy of each module per page"


def test_the_public_door_lets_the_stylesheet_through():
    from arcade.web import door
    assert "/base.css" in door.PUBLIC_PAGES


def test_one_account_asking_at_once_is_answered_from_one_working_out(client, monkeypatch):
    """/account asked from several parts of a page at the same moment works the answer out once (2026-10-20: 22 times
    a minute for one phone, ~1.3 s each on .25). Nothing is kept afterwards: the next ask works it out again."""
    import threading
    import time
    from nacl.signing import SigningKey
    from test_auth_web import _sign_in
    app, state = client
    assert _sign_in(app, SigningKey.generate(), headers=LOCAL).status_code == 200
    register = state.accounts()
    real, calls = register.room, []

    def slow_room(*a, **kw):                       # once per working out, at its end
        calls.append(1)
        time.sleep(0.4)
        return real(*a, **kw)
    monkeypatch.setattr(register, "room", slow_room)
    monkeypatch.setattr(type(state), "accounts", lambda self: register)
    said = []

    def ask():
        said.append(app.get("/account", headers=LOCAL).json())
    threads = [threading.Thread(target=ask) for _ in range(5)]
    for t in threads:
        t.start()
        time.sleep(0.02)
    for t in threads:
        t.join(10)
    assert len(said) == 5 and all(s["pubkey"] == said[0]["pubkey"] for s in said)
    assert len(calls) == 1, f"worked out {len(calls)} times for 5 asks at once"
    app.get("/account", headers=LOCAL)
    assert len(calls) == 2, "nothing kept afterwards"


def test_a_signed_in_page_preloads_the_wallets_whole_module_graph(client):
    """The Creator tab's load (2026-10-26): wallet.js -> signin/coins -> secp256k1/ripemd160 -> their helpers
    were five round trips in a row. A signed-in page names the whole graph up front, fingerprinted like every import,
    so the browser fetches it at once; a page nobody is signed in to does not."""
    from nacl.signing import SigningKey
    from test_auth_web import _sign_in
    app, state = client
    assert "modulepreload" not in app.get("/create", headers=LOCAL).text
    assert _sign_in(app, SigningKey.generate(), headers=LOCAL).status_code == 200
    html = app.get("/create", headers=LOCAL).text
    mark = re.search(r'/wallet\.js\?v=([0-9a-f]{12})', html).group(1)
    for name in ("/wallet.js", "/signin.js", "/coins.js"):
        assert f'<link rel="modulepreload" href="{name}?v={mark}">' in html, name
    root = pathlib.Path(__file__).resolve().parent.parent / "arcade/web/vendor"
    for f in ("noble-secp256k1.js", "hashes/ripemd160.js", "hashes/_md.js", "hashes/utils.js", "hashes/_assert.js",
              "hashes/crypto.js"):
        assert f'<link rel="modulepreload" href="/vendor/{f}">' in html, f
        assert (root / f).exists(), f
    # every module the graph imports is preloaded: nothing left to find one hop later
    imports = set()
    for f in ("hashes/ripemd160.js", "hashes/_md.js", "hashes/utils.js", "hashes/_assert.js", "noble-secp256k1.js"):
        for m in re.findall(r"from '\./([\w.]+)'", (root / f).read_text()):
            imports.add("hashes/" + m)
    for f in imports:
        assert f'href="/vendor/{f}"' in html, f
