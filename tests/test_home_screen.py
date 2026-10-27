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
                                                       "genre": "calls", "minimized": {"timeout": 300}}}, height=100)
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
    assert one["timeout"] == 300, "the card's minimized timeout reaches the home screen"
    icon = app.get("/apps/store-icon.png")
    assert icon.status_code == 200 and icon.headers["content-type"] == "image/png"


def test_a_screen_never_arranged_starts_with_the_starred_apps_and_keeps_its_order(public):
    app, state = public
    assert _sign_in(app, SigningKey.generate(), headers=EDGE).status_code == 200
    star = f"app:{MAKER}:little-app"
    assert app.post("/account/favorites", headers=EDGE, json={"key": star, "on": True}).status_code == 200
    first = app.get("/account/homeapps", headers=EDGE).json()
    assert first == {"pages": None, "dock": None, "starred": [star], "saved": True}, "never arranged: the page seeds it"
    order = [star, "tab:/me/messages", "store", "tab:/feed"]
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": order}).json() == {"pages": [order], "dock": None,
                                                                                         "saved": True}, \
        "a flat list (before pages) is one page"
    two = [[star, {"id": "f1", "folder": "Games", "apps": ["tab:/feed", f"app:{MAKER}:other"]}], ["store"]]
    assert app.post("/account/homeapps", headers=EDGE, json={"pages": two}).json()["pages"] == two
    assert app.get("/account/homeapps", headers=EDGE).json()["pages"] == two
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": ["https://elsewhere"]}).status_code == 400
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": ["store"] * 200}).status_code == 400
    assert app.post("/account/homeapps", headers=EDGE, json={"pages": [["store"], ["store"]]}).status_code == 400, "once each"
    assert app.post("/account/homeapps", headers=EDGE,
                    json={"pages": [[{"id": "bad id!", "folder": "x", "apps": ["store"]}]]}).status_code == 400
    assert state.setting("homeapps:node") is None, "an account's screen is its own"


def test_a_stranger_keeps_the_screen_in_the_browser_and_the_operator_on_the_node(public):
    app, state = public
    assert app.get("/account/homeapps", headers=EDGE).json()["saved"] is False
    assert app.post("/account/homeapps", headers=EDGE, json={"apps": ["store"]}).status_code == 403
    assert app.post("/account/homeapps", headers=LOCAL, json={"apps": ["store"]}).status_code == 200
    assert state.setting("homeapps:node") == {"pages": [["store"]], "dock": None}


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


def test_nobody_signed_in_is_asked_to_sign_in_first(public):
    """2026-10-24: "PWA should ask for you to login right away"."""
    app, _state = public
    r = app.get("/home", headers=EDGE, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/join?next=/home"
    assert _sign_in(app, SigningKey.generate(), headers=EDGE).status_code == 200
    assert app.get("/home", headers=EDGE, follow_redirects=False).status_code == 200


def test_a_card_says_how_long_its_app_keeps_running_minimized():
    """2026-10-24: "App builders should be able to decide their apps do when they are minimized that should be
    stored in the game card ... five minutes before they time out. Other apps could have a different timeout or no
    timeout"."""
    def said(m):
        return gameslib.parse(json.dumps({"app": {"name": "X", "minimized": m}}))["minimized_timeout"]
    assert said({"timeout": 300}) == 300
    assert said(120) == 120, "a bare number is the timeout"
    assert said({"timeout": 0}) is None, "0 is no limit (2026-10-24)"
    assert said(None) == 900 and said({}) == 900, "not said: 15 minutes"
    assert said({"timeout": -5}) == 900 and said({"timeout": "300"}) == 900 and said({"timeout": True}) == 900
    assert said({"timeout": 10 ** 9}) == 86400, "a day at most"
    assert gameslib.parse(json.dumps({"app": {"name": "Y"}}))["minimized_timeout"] == 900


def test_the_app_is_told_when_it_is_minimized_restored_or_closing(client):
    app, _state = client
    shim = app.get("/r/app.js")
    assert shim.status_code == 200 and "javascript" in shim.headers["content-type"]
    assert "window.arcade.app" in shim.text and "arcade:app" in shim.text
    doors = (TEMPLATES / "_page_doors.js.html").read_text()
    assert "app: 'viewer'" in doors and "if (m.arcade === 'app') { appDoor(m, answer); return; }" in doors
    assert "e.source !== window.parent || e.origin !== location.origin" in doors, "only the home screen is heard"
    home = (TEMPLATES / "home.html").read_text()
    for state in ("minimized", "restored", "closing"):
        assert f"tell(w, '{state}')" in home
    assert "expire(w)" in home and "w.timeout === 0" not in home, "0 is no limit, never closed at once"


def test_the_installed_app_opens_on_the_apps(client):
    """2026-10-24: "The PWA should open directly to the apps"."""
    app, _state = client
    assert app.get("/manifest.webmanifest").json()["start_url"] == "/home"
    head = (TEMPLATES / "base.html").read_text().split("</head>")[0]
    assert "nav.redirectCount > 0" in head, "a start page the server redirected (an account's / to the Feed) lands home"


def test_a_picked_dark_theme_makes_the_iphone_status_bar_black(client):
    """2026-10-24: "The top of my screen where my clock and battery meters are showing up white, even though my
    PWA is black"."""
    app, _state = client
    bar = re.compile(r'name="apple-mobile-web-app-status-bar-style" content="([a-z-]+)"')
    assert bar.search(app.get("/feed").text).group(1) == "default"
    app.cookies.set("arcade_theme", "dark")
    assert bar.search(app.get("/feed").text).group(1) == "black"
    app.cookies.set("arcade_theme", "light")
    assert bar.search(app.get("/feed").text).group(1) == "default"


def test_the_red_counts_stay_fresh_on_the_home_screen(public):
    """2026-10-24: "Do we get a little red notification with the number for notifications and new messages?"."""
    app, _state = public
    assert door.public_path("/account/badges")
    assert _sign_in(app, SigningKey.generate(), headers=EDGE).status_code == 200
    by = app.get("/account/badges", headers=EDGE).json()
    assert {"/me/messages", "/me/notifications", "/feed"} <= set(by) and all(isinstance(v, int) for v in by.values())
    home = (TEMPLATES / "home.html").read_text()
    assert "fetch('/account/badges'" in home and "navigator.setAppBadge" in home


def test_pages_folders_and_back_on_the_home_screen():
    """2026-10-24: "several pages of apps ... put apps inside of a folder and just like on the homepage of an
    Apple phone"; "If you swipe back, you should go back to the last page you were on and your app should be minimized
    unless it's a page then it should just close"."""
    home = (TEMPLATES / "home.html").read_text()
    assert 'id="hs-pager"' in home and "scroll-snap-type: x mandatory" in home
    assert "function fold(id, ontoId)" in home and 'id="hs-folder"' in home
    assert "window.addEventListener('popstate'" in home and "history.pushState({hs: kind}" in home
    assert "if (w.item && w.item.kind === 'app') minimize(); else closeWin(w);" in home
    assert 'id="hs-edge"' in home, "an iPhone's installed app has no back swipe of its own"


def test_the_wallet_is_asked_for_as_the_app_opens_and_no_hint_text():
    """2026-10-24: "Make sure the login pops up as soon as the PWA loads"; "Remove the touch and hold icons to move
    Text"."""
    home = (TEMPLATES / "home.html").read_text()
    assert "while (!keys) keys = await arcadeUnlockSheet('', true);" in home and "askForWallet();" in home
    assert "Touch and hold an icon to move it." not in home


def test_holding_an_icon_opens_its_menu_and_no_robot_over_an_open_app():
    """2026-10-24: "When you Long click on one of the app icons, it should open a menu with edit Home Screen, open
    app, remove app, close app"; "when you have an app open, you should not see the robot helper"."""
    home = (TEMPLATES / "home.html").read_text()
    assert "press.t = setTimeout(fromFolder ? begin : showMenu, 450);" in home, "held: the menu, not a drag at once"
    for words in ("Edit Home Screen", "Remove from Home Screen", "'Close'", "'Open'"):
        assert words in home, words
    assert "if (!drag && press.menu) { if (Math.abs(dx) + Math.abs(dy) > 10) { hideMenu(); begin(); } return; }" in home, \
        "held and moved on: the menu goes and the icon is dragged"
    assert "body.hs-win-open #arc-bot" in home and "document.body.classList.toggle('hs-win-open', !!front)" in home
    base = (TEMPLATES / "base.html").read_text()
    assert "html.inshell body.fullscreen #arc-bot,html.inshell body.fullscreen #arc-bot-box{display:none !important}" in base


def test_notifications_are_a_bell_in_the_corner_and_nothing_is_selected():
    """2026-10-25: "Make notifications a little bell and upper right hand corner ... a red outline of a bell with
    the background color showing when there are no notifications when there are notifications, the bell should be fully
    red with the number of notifications shown in the middle of it"; "disable text select"; "The reshuffling of the apps
    quit working"."""
    home = (TEMPLATES / "home.html").read_text()
    assert 'id="hs-bell"' in home and ".hs-bell #hs-bell-shape { fill: none; stroke: #e5484d; }" in home
    assert ".hs-bell.on #hs-bell-shape { fill: #e5484d; }" in home and "b.classList.toggle('on', n > 0);" in home
    assert "function outOfGrid(k) { return k === bellKey || dock.indexOf(k) >= 0; }" in home, "the Notifications icon leaves the grid"
    assert "body.fullscreen, body.fullscreen * { -webkit-user-select: none; user-select: none;" in home
    assert "Math.abs(PX(e) - rest.x) + Math.abs(PY(e) - rest.y) < 14" in home, "a twitch is still resting"
    assert "if (drag || (press && press.menu)) e.preventDefault();" in home


def test_the_dock_is_kept_with_the_layout_and_the_helper_is_an_app(public):
    """2026-10-25: "apps in the dock should be able to be dragged out of the dock and any app should be able to be
    dragged into the dock. The robot helper should be another app when in the home screen"; on the pages opened from it,
    the robot stays "in the lower right hand corner"."""
    app, state = public
    assert _sign_in(app, SigningKey.generate(), headers=EDGE).status_code == 200
    pages, dock = [["store", "helper"]], ["tab:/feed", f"app:{MAKER}:little-app"]
    got = app.post("/account/homeapps", headers=EDGE, json={"pages": pages, "dock": dock}).json()
    assert got == {"pages": pages, "dock": dock, "saved": True}
    assert app.get("/account/homeapps", headers=EDGE).json()["dock"] == dock
    assert app.post("/account/homeapps", headers=EDGE, json={"pages": pages}).json()["dock"] == dock, "kept when unsaid"
    assert app.post("/account/homeapps", headers=EDGE, json={"pages": pages, "dock": ["x"] * 7}).status_code == 400
    home = (TEMPLATES / "home.html").read_text()
    assert "function dockPlace(slot)" in home and "$('hs-dock').addEventListener('pointerdown'" in home
    assert "byKey.helper = {key: 'helper', kind: 'helper', name: 'Helper'};" in home
    assert "body.fullscreen #arc-bot { display: none !important; }" in home
    base = (TEMPLATES / "base.html").read_text()
    assert "html.inshell body.fullscreen #arc-bot" in base and "html.inshell #arc-bot," not in base


def test_the_home_screen_is_always_portrait():
    """2026-10-25: "make it so the home screen on the PWA is always in portrait mode, regardless of the
    orientation of the phone". Android locks it; an iPhone (no lock from a page) has the page turned back a quarter
    while no app is in front, touches and boxes read in the turned frame; an app in front turns with the phone."""
    home = (TEMPLATES / "home.html").read_text()
    assert "so.lock('portrait')" in home and "if (front) {" in home and "so.unlock()" in home
    assert "html.turn90 body { transform: rotate(90deg) translateY(-100%); }" in home
    assert "html.turn-90 body { transform: rotate(-90deg) translateX(-100%); }" in home
    assert "want = a === 90 ? -90 : a === -90 ? 90 : 0;" in home
    assert "e.clientX" not in home.replace("loc(e.clientX, e.clientY)", ""), "every touch is read through loc()"
    assert ".getBoundingClientRect()" not in home.replace("var r = el.getBoundingClientRect();", ""), "every box through R()"
    assert "function paintRunning() {\n    orient();" in home


def test_any_inscription_can_be_the_home_screens_background(public):
    """2026-10-27: "Under the your arcade page on the PWA, you should be able to choose any inscription as the
    background to your home screen": kept per account on the node, picked on /me, drawn behind the icons on /home."""
    app, state = public
    assert door.public_path("/account/homebg") and door.public_path("/account/homebg", "POST")
    assert app.get("/account/homebg", headers=EDGE).json() == {"id": ""}
    assert app.post("/account/homebg", headers=EDGE, json={"id": "ab" * 32 + "i0"}).status_code == 403, "sign in first"
    assert _sign_in(app, SigningKey.generate(), headers=EDGE).status_code == 200
    iid = "AB" * 32 + "i3"
    assert app.post("/account/homebg", headers=EDGE, json={"id": iid}).json() == {"id": iid.lower()}
    assert app.get("/account/homebg", headers=EDGE).json() == {"id": iid.lower()}
    assert app.post("/account/homebg", headers=EDGE, json={"id": "javascript:alert(1)"}).status_code == 400
    assert app.post("/account/homebg", headers=EDGE, json={"id": ""}).json() == {"id": ""}
    me = (TEMPLATES / "me.html").read_text()
    home = (TEMPLATES / "home.html").read_text()
    assert 'id="homebg-pref"' in me and "drawBackground(" in home and "sandbox', 'allow-scripts'" in home


def test_the_installed_app_cannot_be_used_until_the_wallet_is_unlocked():
    """2026-10-27: "when the PWA opens the first thing that should open is the unlock wallet pop-up, and the
    PWA should not be usable until the Wallet is unlocked": the sheet is asked for first, has no Cancel, and comes back
    until the wallet opens."""
    home = (TEMPLATES / "home.html").read_text()
    base = (TEMPLATES / "base.html").read_text()
    assert "while (!keys) keys = await arcadeUnlockSheet('', true);" in home
    boot = home[home.index("  function boot() {"):]
    assert boot.index("askForWallet();") < boot.index("fetch('/account/homeapps'"), "asked before the icons load"
    assert "function arcadeUnlockSheet(why, required)" in base
    assert "if (e.target === back && !required) done(null);" in base
