"""Which collection roots a node uses (arcade/collection_registry.py): first to publish owns its items; only the same
publisher replaces; a root waits for its parts (2026-10-17)."""
import contextlib
import json
import sqlite3

from arcade import collection_json as C
from arcade.collection_registry import Registry

from test_end_to_end import chain                    # noqa: F401  (fixture)
from test_inscription_e2e import inscribing          # noqa: F401  (fixture)


class FakeIndex:
    """Just enough of LedgerIndex: an inscription table and the content of each."""

    def __init__(self, path):
        self.path, self.content, self.n = str(path), {}, 0
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE inscription (txid TEXT, number INTEGER, creator TEXT, content_type TEXT)")

    def add(self, body: dict, creator="nAlice", indexed=True) -> str:
        txid = f"{self.n:064x}"
        self.n += 1
        self.content[txid] = json.dumps(body).encode()
        if indexed:
            with sqlite3.connect(self.path) as c:
                c.execute("INSERT INTO inscription VALUES(?,?,?,?)", (txid, self.n, creator, C.CONTENT_TYPE))
        return txid

    def index_now(self, txid, creator="nAlice"):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO inscription VALUES(?,?,?,?)", (txid, int(txid, 16) + 1, creator, C.CONTENT_TYPE))

    @contextlib.contextmanager
    def open(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        yield type("DB", (), {"conn": conn})()
        conn.close()

    def inscription_content(self, txid):
        return ("application/json", self.content[txid]) if txid in self.content else None


def _publish(ix, ids, creator="nAlice", name="Dogs", replaces="", index_parts=True):
    items = [C.Item(id=i, number=n + 1, traits={"Fur": "Red"}) for n, i in enumerate(ids)]
    root, parts = C.build(name, "doge", items, ["Fur"], replaces=replaces, per_part=2)
    part_ids = [ix.add(p, creator, indexed=index_parts) for p in parts]
    return ix.add(C.with_parts(root, part_ids), creator), part_ids


IDS = [f"{0xaa00 + k:064x}i0" for k in range(5)]


def test_first_to_publish_owns_its_items(tmp_path):
    ix, reg = FakeIndex(tmp_path / "ix"), Registry(tmp_path / "r")
    first, _ = _publish(ix, IDS)
    second, _ = _publish(ix, IDS[2:], creator="nMallory", name="Fake Dogs")
    reg.scan(ix, "test")
    roots = {r["txid"]: r for r in reg.all_roots()}
    assert roots[first]["status"] == "active" and roots[second]["status"] == "claimed"
    assert [c["name"] for c in reg.active()] == ["Dogs"] and reg.item("doge", IDS[3])["number"] == 4


def test_only_the_publisher_replaces_it(tmp_path):
    ix, reg = FakeIndex(tmp_path / "ix"), Registry(tmp_path / "r")
    first, _ = _publish(ix, IDS)
    reg.scan(ix, "test")
    stranger, _ = _publish(ix, IDS[:4], creator="nMallory", replaces=first)
    fixed, _ = _publish(ix, IDS[:4], name="Dogs v2", replaces=first)
    reg.scan(ix, "test")
    roots = {r["txid"]: r for r in reg.all_roots()}
    assert roots[stranger]["status"] == "refused" and "only the address" in roots[stranger]["reason"]
    assert roots[fixed]["status"] == "active" and roots[first]["status"] == "replaced"
    assert [c["name"] for c in reg.active()] == ["Dogs v2"] and reg.item("doge", IDS[4]) is None, "let go"


def test_a_root_waits_for_its_parts(tmp_path):
    ix, reg = FakeIndex(tmp_path / "ix"), Registry(tmp_path / "r")
    root, parts = _publish(ix, IDS, index_parts=False)
    held = {p: ix.content.pop(p) for p in parts}             # the parts' blocks not read yet
    reg.scan(ix, "test")
    assert reg.all_roots()[0]["status"] == "waiting"
    ix.content.update(held)
    reg.scan(ix, "test")
    assert reg.root(root)["status"] == "active" and reg.active()[0]["count"] == 5


def test_a_collection_published_on_a_chain_appears_by_itself(tmp_path, inscribing):   # noqa: F811
    """The real path: parts then root inscribed through arcade.publish on regtest, indexed, read by the registry."""
    import secrets
    from arcade import publish
    from arcade.ledger import LedgerIndex
    from test_inscription_e2e import _sync

    node, alice, bob, params, follower, path = inscribing
    key = publish.Key(secrets.randbelow(publish.N - 1) + 1)
    node.rpc.call("sendtoaddress", key.address(params), 60)
    node.generate(1)
    pub = publish.Publisher(node.rpc, params, key, tmp_path / "journal")
    items = [C.Item(id=i, number=n + 1, traits={"Fur": "Red"}) for n, i in enumerate(IDS)]
    root, parts = C.build("Chain Dogs", "doge", items, ["Fur"], per_part=2)
    compact = lambda v: json.dumps(v, separators=(",", ":")).encode()          # noqa: E731
    ids = [pub.inscribe(compact(p), C.CONTENT_TYPE, name="part.json", while_waiting=lambda: node.generate(1))["id"]
           for p in parts]
    top = pub.inscribe(compact(C.with_parts(root, ids)), C.CONTENT_TYPE, name="root.json",
                       while_waiting=lambda: node.generate(1))["id"]
    _sync(node, follower)
    reg = Registry(tmp_path / "r")
    reg.scan(LedgerIndex(path, params, rpc_factory=lambda: node.rpc), params.name)
    assert reg.root(top)["status"] == "active" and reg.root(top)["publisher"] == key.address(params)
    assert reg.active()[0]["count"] == 5 and reg.item("doge", IDS[4])["name"] == "Chain Dogs"
