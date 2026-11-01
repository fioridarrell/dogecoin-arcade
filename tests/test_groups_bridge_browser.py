"""arcade.groups: an inscribed app opens the player's group chats through the viewer, and never holds a key."""

import json
import pathlib
import sys
import time

import pytest

pytest.importorskip("selenium", reason="browser tests need selenium: pip install .[dev]")
from selenium.webdriver.common.by import By                          # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import browsers                                                       # noqa: E402
from test_claim_browser import _free_port                            # noqa: E402
from test_contacts_browser import _signed_in                         # noqa: E402
from test_realtime_rejoin_browser import Server                      # noqa: E402
from test_realtime_web import make_state                             # noqa: E402
from test_trade_browser import _inscribe                             # noqa: E402

PAGE_ID, CARD_ID = "c0" * 32, "c1" * 32
PAGE = b"""<!doctype html><meta charset="utf-8"><title>Chats</title><body>
<script src="/r/groups.js"></script>
<input id="job"><button id="go">go</button><div id="out"></div>
<script>
document.getElementById('go').onclick = function () {
  var job = JSON.parse(document.getElementById('job').value), g = arcade.groups;
  var call = job.op === 'list' ? g.list() : job.op === 'read' ? g.read(job.id)
           : job.op === 'seal' ? g.seal(job.id, job.text) : g.open(job.id, job.sealed);
  call.then(function (r) { done(job.slot, {value: r}); }, function (e) { done(job.slot, {error: String(e.message || e)}); });
};
function done(slot, said) {
  var p = document.createElement('pre'); p.id = 'r-' + slot; p.textContent = JSON.stringify(said);
  document.getElementById('out').appendChild(p);
}
</script>"""
#: A group this browser holds, and one line said in it: what Messages would have kept.
SEED = """
const done = arguments[0];
(async () => {
  const mail = await import("/messaging.js");
  const secret = "11".repeat(32), pub = "22".repeat(32);
  await mail.groups();
  const dbs = await indexedDB.databases();
  const name = (dbs.find((d) => d.name.startsWith("arcade-messages")) || {}).name;
  const db = await new Promise((ok, no) => { const r = indexedDB.open(name); r.onsuccess = () => ok(r.result); r.onerror = no; });
  const names = [...db.objectStoreNames];
  const tx = db.transaction(names, "readwrite");
  tx.objectStore("marks").put({id: "aabb", root: "aabb", name: "Canoe club", secret, public: pub, current: true, created: 1,
                               members: [{tag: "wave", key: "33".repeat(32), address: "nWave"}, {tag: "", key: "44".repeat(32), address: "nMe"}]}, "group:aabb");
  tx.objectStore("mail").put({txid: "ee".repeat(32), when: 100, height: 0, from_address: "nWave", sender: "33".repeat(32),
                              peer: "group:aabb", group: "aabb", body: Array.from(new TextEncoder().encode("bay route is done")).map((b) => b.toString(16).padStart(2, "0")).join("")});
  await new Promise((ok) => { tx.oncomplete = ok; });
  done(true);
})().catch((e) => done(String(e)));
"""


@pytest.fixture(scope="module")
def browser():
    driver = browsers.launch()
    yield driver
    driver.quit()


@pytest.fixture
def base(tmp_path, no_nodes):
    from arcade.web.app import create_app

    state = make_state(tmp_path, "node")
    creator = "n" + "G" * 33
    _inscribe(state, PAGE_ID, 1, creator, "text/html", PAGE, said={"app": {"name": "Chats", "groups": True}})
    _inscribe(state, CARD_ID, 2, creator, "text/html", PAGE)
    port = _free_port()
    server = Server(create_app(state), port)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.stop()


def _view(browser, base, page=PAGE_ID):
    browsers.allow_wallet(browser, base, page)
    browser.get(f"{base}/inscriptions/{page}/view")
    frame = browser.find_element(By.CSS_SELECTOR, ".inscription-frame")
    browser.switch_to.frame(frame)
    for _ in range(120):
        if browser.execute_script("return !!document.getElementById('go')"):
            break
        time.sleep(0.25)
    browser.switch_to.default_content()
    return frame


def _call(browser, frame, slot, **job):
    browser.switch_to.frame(frame)
    try:
        browser.execute_script("document.getElementById('job').value = arguments[0];"
                               "document.getElementById('go').click();", json.dumps({"slot": slot, **job}))
        end = time.time() + 30
        while time.time() < end:
            got = browser.execute_script("var p = document.getElementById(arguments[0]); return p && p.textContent;", f"r-{slot}")
            if got:
                return json.loads(got)
            time.sleep(0.1)
        raise AssertionError(f"no answer to {job}")
    finally:
        browser.switch_to.default_content()


def test_nobody_signed_in_and_a_page_with_no_card_get_no_group_chats(browser, base):
    browser.get(f"{base}/join/keys")
    browser.delete_all_cookies()
    frame = _view(browser, base)
    said = _call(browser, frame, "guest", op="list")["error"]
    assert "sign in" in said or "operator" in said, said
    _signed_in(browser, base)
    frame = _view(browser, base, CARD_ID)
    assert "no card" in _call(browser, frame, "stranger", op="list")["error"]


def test_an_app_reads_a_group_only_with_its_switch_on_and_never_gets_a_key(browser, base):
    _signed_in(browser, base)
    assert browser.execute_async_script(SEED) is True
    frame = _view(browser, base)
    assert "off" in _call(browser, frame, "off", op="list")["error"], "the switch starts off"
    browser.execute_script("localStorage.setItem(arguments[0], 'yes')", f"arcade.groups.allow:{PAGE_ID}")
    frame = _view(browser, base)
    said = _call(browser, frame, "list", op="list")["value"]
    assert [g["id"] for g in said["groups"]] == ["aabb"], said
    group = said["groups"][0]
    assert group["name"] == "Canoe club" and len(group["room"]) == 32
    assert "11" * 32 not in json.dumps(said) and "secret" not in json.dumps(said), "no key reaches the app"
    lines = _call(browser, frame, "read", op="read", id="aabb")["value"]
    assert [(l["from"], l["text"]) for l in lines] == [("nWave", "bay route is done")], lines
    sealed = _call(browser, frame, "seal", op="seal", id="aabb", text="hello now")["value"]
    assert "hello now" not in sealed
    assert _call(browser, frame, "open", op="open", id="aabb", sealed=sealed)["value"] == "hello now"
    assert _call(browser, frame, "bad", op="open", id="aabb", sealed="bm90IHNlYWxlZA==")["value"] is None
    assert "not a group" in _call(browser, frame, "none", op="read2", id="zz")["error"]


def test_the_seal_and_the_keys_stay_with_the_viewer():
    """Pinned where no browser runs: what an app is told about a group."""
    js = (pathlib.Path(__file__).resolve().parent.parent / "arcade/web/templates/messaging_js.js").read_text()
    told = js[js.index("export function groupForApp"):js.index("export async function groupLines")]
    assert "secret" not in told and ".key" not in told, "no key material leaves for an app"
    door = (pathlib.Path(__file__).resolve().parent.parent / "arcade/web/templates/_page_doors.js.html").read_text()
    body = door[door.index("function groupsOp"):door.index("function contactsDoor")]
    assert "arcadeAsk(" in body and "declined" in body, "making a group asks the player"
    assert "groups: 'viewer'" in door, "registered, or the message is dropped"
