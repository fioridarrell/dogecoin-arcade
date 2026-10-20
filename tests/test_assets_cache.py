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
