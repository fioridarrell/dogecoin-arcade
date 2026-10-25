"""The home screen (PWA only; 2026-10-24: "When in PWA the apps page should be the only page you use and each one
of the existing tabs should be turned into an icon"): /home, its icons kept per account on this node, and base.html
sending the installed app there while a browser tab keeps its tabs."""
import json
import pathlib
import re
import sys

from nacl.signing import SigningKey

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_web import app_state, client                          # noqa: F401,E402
from test_admin import EDGE, LOCAL, public                      # noqa: F401,E402
from test_auth_web import _sign_in                              # noqa: E402
from test_games import MAKER, _inscribe                         # noqa: E402

from arcade import games as gameslib                              # noqa: E402
from arcade.web import door                                       # noqa: E402

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "arcade/web/templates"


def test_the_door_lets_anybody_see_the_home_screen_and_an_account_keep_its_own_icons():
    assert door.public_path("/home") and door.public_path("/apps/store-icon.png")
    assert door.public_path("/account/homeapps")
    assert door.public_path("/account/homeapps", "POST")


def test_the_home_screen_has_every_tab_as_an_icon_and_the_apps_for_its_store(client):
    app, state = client
    _inscribe(state, "a1" * 32, 51, MAKER, {"app": {"name": "Call Me", "media": True, "contacts": True,
                                                       "genre": "calls"}}, height=100)
    page = app.get("/home")
    assert page.status_code == 200
    apps = json.loads(re.search(r"var APPS = (\[.*?\]);\n", page.text).group(1))
    tabs = json.loads(re.search(r"var TABS = (\[.*?\]);\n", page.text).group(1))
    hrefs = [t["href"] for t in tabs]
    assert "/feed" in hrefs and "/exchange" in hrefs and "/docs" in hrefs
    assert "/apps" not in hrefs, "the Apps tab is the home screen itself"
    one = [a for a in apps if a["name"] == "Call Me"][0]
    assert one["key"] == gameslib.app_key(MAKER, "Call Me")
    assert one["perms"] == ["contacts", "media"] and one["genre"] == "calls"
    assert one["open"] == f"/inscriptions/{one['page']}/full"
    icon = app.get("/apps/store-icon.png")
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"


def test_a_screen_never_arranged_starts_with_the_starred_apps_and_keeps_its_order(public):
    app, state = public
    assert _sign_in(app, SigningKey.generate(), headers=EDGE).status_code == 200
    star = f"app:{MAKER}:little-app"
    assert app.post("/account/favorites", headers=EDGE, json={"key": star, "on": True}).status_code == 200
    first = app.get("/account/homeapps", headers=EDGE).json()
    assert first == {"apps": None, "starred": [star], "saved": True}, "never arranged: the page seeds it"
    order = [star, "tab:/me/messages", "store", "tab:/feed"]
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": order}).json() == {"apps": order, "saved": True}
    assert app.get("/account/homeapps", headers=EDGE).json()["apps"] == order
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": ["https://elsewhere"]}).status_code == 400
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": ["store"] * 200}).status_code == 400
    assert state.setting("homeapps:node") is None, "an account's screen is its own"


def test_a_stranger_keeps_the_screen_in_the_browser_and_the_operator_on_the_node(public):
    app, state = public
    assert app.get("/account/homeapps", headers=EDGE).json()["saved"] is False
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": ["store"]}).status_code == 403
    assert app.post("/account/homeapps", headers=LOCAL, json={"apps": ["store"]}).status_code == 200
    assert state.setting("homeapps:node") == ["store"]


def test_only_the_installed_app_is_sent_to_the_home_screen():
    """A browser tab keeps its tabs (2026-10-24: "they already can have multiple tabs open. This should be a
    PWA build only"); a page inside the home screen hides its own."""
    base = (TEMPLATES / "base.html").read_text()
    head = base[:base.index("</head>")]
    assert "display-mode: standalone" in head and "location.replace(" in head
    assert "if (!installed || window.top !== window || location.pathname === '/home') return;" in head
    assert "html.inshell header{display:none !important}" in head
    home = (TEMPLATES / "home.html").read_text()
    assert "navigator.wakeLock.request('screen')" in home, "the screen stays awake while the app is open"
