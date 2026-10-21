"""The slim index (layout 2, 2026-10-20: "slim down the index ... without losing functionality"; "we should not
be saving every image, we should be retrieving those images directly from the blockchain when they are needed").

Measured on a test machine's PEP Main index before: 9.6 GB, of which 6.25 GB was undo -- every block's undo held the whole of
every image still arriving, as hex, for 300 blocks -- plus a 2.8 GB write log and a 6.6 GB snapshot copy. Now a body
arriving in pieces is kept as its envelope's state and only kept itself while a rule could read it; ids are bytes;
content types are numbers; undo is packed; and an index of the old layout is converted in place, every row the same.
"""
import json
import random
import sqlite3

import pytest

from arcade import doginal_index as dx
from arcade import ord_rules, rawchain
from test_doginal_index import FUND, Chain, _envelope, _more, _push, _tx, _walker, small  # noqa: F401


# --------------------------------------------------------------------------- a piece at a time

def _parse_whole(scripts):
    """ord_rules.parse before 2026-10-20 (every piece's script, read from the first each time): the reference."""
    pushes = ord_rules.decode_push_datas(scripts[0])
    if pushes is None or len(pushes) < 3 or pushes[0] != b"ord":
        return "none", b"", b""
    npieces = ord_rules.push_number(pushes[1])
    if not npieces:
        return "none", b"", b""
    ctype, rest, body = pushes[2], pushes[3:], b""
    left = list(scripts)
    while True:
        while True:
            if npieces == 0:
                return "complete", ctype, body
            if len(rest) < 2:
                break
            nxt = ord_rules.push_number(rest[0])
            if nxt is None or nxt != npieces - 1:
                break
            body += rest[1]
            rest = rest[2:]
            npieces -= 1
        if len(left) <= 1:
            return "partial", ctype, body
        left = left[1:]
        rest = ord_rules.decode_push_datas(left[0])
        if rest is None or len(rest) < 2:
            return "none", b"", b""
        nxt = ord_rules.push_number(rest[0])
        if nxt is None or nxt != npieces - 1:
            return "none", b"", b""


def test_an_envelope_read_a_piece_at_a_time_reads_as_it_did_whole():
    rnd = random.Random(7)
    for _ in range(3000):
        total = rnd.randint(1, 6)
        count, scripts = total, []
        first = True
        while count > 0 and len(scripts) < 5:
            parts = []
            for _p in range(rnd.randint(0, 3)):
                if count == 0:
                    break
                count -= 1
                # now and then a countdown out of order, or a piece left without its data
                said = count if rnd.random() > .08 else rnd.randint(0, 7)
                parts.append((said, bytes(rnd.randrange(256) for _b in range(rnd.randint(0, 9)))))
            if first:
                s = _envelope(rnd.choice([b"text/plain", b"image/png", b"application/json"]), parts=parts,
                              lead=b"ord" if rnd.random() > .05 else b"orx", total=total)
                first = False
            else:
                s = _more(parts) if rnd.random() > .05 else _push(b"\x07")
            scripts.append(s)
        for k in range(1, len(scripts) + 1):
            assert ord_rules.parse(scripts[:k]) == _parse_whole(scripts[:k])


def test_a_body_in_pieces_is_let_go_of_unless_a_rule_could_read_it(tmp_path):
    """An image's pieces are never kept (its content is read from the chain when wanted); a name's text is; and what the
    index says of each is what it said when every piece was kept."""
    chain = Chain()
    img = _tx([(FUND, 0, _envelope(b"image/png", parts=((2, b"\x89PNG" + b"P" * 200),), total=3))], [100_000], tag=b"i")
    name = _tx([(FUND, 1, _envelope(b"text/plain", parts=((1, b"pup"),), total=2))], [100_000], tag=b"n")
    js = _tx([(FUND, 2, _envelope(b"image/svg+xml", parts=((1, b' {"p":"x"'),), total=2))], [100_000], tag=b"j")
    chain.add([img, name, js])
    index = _walker(tmp_path, chain)
    index.walker.step(chain, dx.BASE, budget=60)
    rows = {r[0]: r[1:] for r in index.store.q("SELECT txid, ctype, left, body, size, pieces FROM pending")}
    assert rows[img[1]] == (b"image/png", 2, None, 204, 1), "the image: its state and size, no body"
    assert rows[name[1]][2] == b"pup", "a name's text is kept"
    assert rows[js[1]][2] == b' {"p":"x"', "a body that may yet be a JSON \"p\" is kept"
    img2 = _tx([(img[1], 0, _more(((1, b"Q" * 200),)))], [100_000], tag=b"i2")
    name2 = _tx([(name[1], 0, _more(((0, b".doge"),)))], [100_000], tag=b"n2")
    js2 = _tx([(js[1], 0, _more(((0, b',"op":"y"}'),)))], [100_000], tag=b"j2")
    chain.add([img2, name2, js2])
    img3 = _tx([(img2[1], 0, _more(((0, b"R" * 100),)))], [100_000], tag=b"i3")
    chain.add([img3])
    index.walker.step(chain, dx.BASE + 2, budget=60)
    assert index.store.q("SELECT COUNT(*) FROM pending")[0][0] == 0
    got = {w["id"]: w for w in (index.where(f"{t[1]}i0") for t in (img, name, js))}
    assert got[f"{img[1]}i0"]["size"] == 504 and got[f"{img[1]}i0"]["protocol"] is None
    assert got[f"{img[1]}i0"]["content_type"] == "image/png"
    assert got[f"{js[1]}i0"]["protocol"] == "x", "read from the body kept"
    assert index.name("pup.doge")["readings"]["spec"]["winner"]["inscription"] == f"{name[1]}i0"
    assert index.store.q("SELECT pieces FROM insc WHERE tx=?", (bytes.fromhex(img[1]),))[0][0] == 3


def test_undo_is_packed_and_still_walks_back(tmp_path):
    chain = Chain()
    img = _tx([(FUND, 0, _envelope(b"image/png", parts=((1, b"P" * 200),), total=2))], [100_000], tag=b"i")
    chain.add([img])
    index = _walker(tmp_path, chain)
    index.walker.step(chain, dx.BASE, budget=60)
    before = _dump(index)
    chain.add([_tx([(img[1], 0, _more(((0, b"Q" * 200),)))], [100_000], tag=b"i2")])
    index.walker.step(chain, dx.BASE + 1, budget=60)
    doc = index.store.q("SELECT doc FROM undo WHERE height=?", (dx.BASE + 1,))[0][0]
    assert isinstance(doc, bytes) and b"P" * 50 not in doc and len(doc) < 300
    index.walker.rollback(dx.BASE + 1)
    assert _dump(index) == before


# --------------------------------------------------------------------------- converting an index of layout 1

LAYOUT_1 = """
    CREATE TABLE insc (rid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, number INTEGER NOT NULL,
                       height INTEGER NOT NULL, txpos INTEGER NOT NULL, ctype TEXT NOT NULL, proto TEXT, size INTEGER,
                       pieces INTEGER NOT NULL DEFAULT 1, burned INTEGER);
    CREATE INDEX insc_number ON insc (number);
    CREATE INDEX insc_ctype ON insc (ctype, number);
    CREATE INDEX insc_proto ON insc (proto, number);
    CREATE TABLE at (rid INTEGER PRIMARY KEY, txid TEXT NOT NULL, vout INTEGER NOT NULL, off INTEGER NOT NULL,
                     height INTEGER NOT NULL);
    CREATE INDEX at_out ON at (txid, vout);
    CREATE TABLE pending (txid TEXT PRIMARY KEY, scripts TEXT NOT NULL, height INTEGER NOT NULL, origin TEXT NOT NULL);
    CREATE TABLE undo (height INTEGER PRIMARY KEY, doc TEXT NOT NULL);
"""


def _scripts(chain, txid, origin):
    """Input 0's script of every piece of a body, first to latest (what layout 1 kept)."""
    out = []
    while True:
        tx = rawchain.read_tx(chain.txs[txid], txid)
        out.append(tx.vin[0]["script"].hex())
        if txid == origin:
            return list(reversed(out))
        txid = tx.vin[0]["txid"]


def _to_layout_1(index, chain, path):
    """The same index as layout 1 had it: a copy of `index` with its walk tables written the old way."""
    src = sqlite3.connect(index.store.path)
    src.execute("VACUUM INTO ?", (str(path),))
    src.close()
    db = sqlite3.connect(path, isolation_level=None)
    cts = dict(db.execute("SELECT ct, ctype FROM ctypes"))

    def old_row(table, row):
        if row is None:
            return None
        if table == "insc":
            return {"rid": row["rid"], "id": dx.iid_of(row["tx"], row["n"]), "number": row["number"],
                    "height": row["height"], "txpos": row["txpos"], "ctype": cts[row["ct"]], "proto": row["proto"],
                    "size": row["size"], "pieces": row["pieces"], "burned": row["burned"]}
        if table == "at":
            return {"rid": row["rid"], "txid": bytes(row["tx"]).hex(), "vout": row["vout"], "off": row["off"],
                    "height": row["height"]}
        if table == "pending":
            return {"txid": row["txid"], "scripts": json.dumps(_scripts(chain, row["txid"], row["origin"])),
                    "height": row["height"], "origin": row["origin"]}
        return row

    def rows(table):
        cur = db.execute(f"SELECT * FROM {table}")
        return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]

    keep = {t: rows(t) for t in ("insc", "at", "pending")}
    undo = [(h, dx._unpack(d)) for h, d in db.execute("SELECT height, doc FROM undo")]
    for t in ("insc", "at", "pending", "undo", "ctypes"):
        db.execute(f"DROP TABLE {t}")
    db.executescript(LAYOUT_1)
    for t, rs in keep.items():
        for r in rs:
            o = old_row(t, r)
            db.execute(f"INSERT INTO {t} ({','.join(o)}) VALUES ({','.join('?' * len(o))})", tuple(o.values()))
    for h, doc in undo:
        doc["rows"] = [[t, key, old_row(t, old)] for t, key, old in doc["rows"]]
        db.execute("INSERT INTO undo VALUES (?,?)", (h, json.dumps(doc)))
    db.execute("DELETE FROM meta WHERE k='layout'")
    db.close()


def _dump(index):
    q = index.store.q
    cts = dict(q("SELECT ct, ctype FROM ctypes"))
    out = {t: sorted(q(f"SELECT * FROM {t}")) for t in ("at", "pending", "tok", "bal", "xfer", "names", "block")}
    out["insc"] = sorted((r[0], r[1], r[2], r[3], r[4], r[5], cts[r[6]]) + tuple(r[7:]) for r in q("SELECT * FROM insc"))
    out["undo"] = {h: _undo_text(dx._unpack(d), cts) for h, d in q("SELECT height, doc FROM undo")}
    out["meta"] = {k: v for k, v in q("SELECT k, v FROM meta")}
    return out


def _undo_text(doc, cts):
    """An undo record with content types as text (their numbers differ between two indexes)."""
    for _t, _k, row in doc["rows"]:
        if row and "ct" in row:
            row["ct"] = cts[row["ct"]]
    return doc


def test_an_index_of_layout_1_is_converted_in_place_and_walks_on_the_same(tmp_path):
    chain = Chain()
    names = _tx([(FUND, 0, _envelope(b"text/plain", parts=((0, b"kit.doge"),)))], [100_000], tag=b"n")
    img = _tx([(FUND, 1, _envelope(b"image/png", parts=((2, b"P" * 200),), total=3))], [100_000], tag=b"i")
    chain.add([names, img])
    moved = _tx([(names[1], 0, b""), (FUND, 2, b"")], [60_000, 50_000], tag=b"m")
    img2 = _tx([(img[1], 0, _more(((1, b"Q" * 200),)))], [100_000], tag=b"i2")
    chain.add([moved, img2])
    chain.add([])
    walked = _walker(tmp_path, chain)
    walked.walker.step(chain, dx.BASE + 2, budget=60)
    assert walked.store.q("SELECT COUNT(*) FROM pending")[0][0] == 1, "the image still arriving"

    _to_layout_1(walked, chain, tmp_path / "old.sqlite")
    old = dx.DoginalIndex(tmp_path / "old.sqlite", lambda: chain, lambda: "walk")
    assert old.store.old_layout and old.where(f"{names[1]}i0") is None, "nothing read until it is converted"
    assert old.status()["upgrading"].startswith("slimming the index")
    old.step()                                                   # the index thread converts it
    assert not old.store.old_layout and old.store.meta("layout") == dx.LAYOUT
    assert _dump(old) == _dump(walked), "every row as a walk of the new layout has it"
    assert old.where(f"{names[1]}i0") == walked.where(f"{names[1]}i0")

    img3 = _tx([(img2[1], 0, _more(((0, b"R" * 200),)))], [100_000], tag=b"i3")
    chain.add([img3])
    chain.add([])
    for idx in (walked, old):
        idx.walker.tracked = None
        idx.walker.step(chain, dx.BASE + 4, budget=60)
    assert _dump(old) == _dump(walked), "and both walk on the same"
    assert old.where(f"{img[1]}i0")["size"] == 600
    old.walker.rollback(dx.BASE + 4)
    old.walker.rollback(dx.BASE + 3)
    walked.walker.rollback(dx.BASE + 4)
    walked.walker.rollback(dx.BASE + 3)
    assert _dump(old) == _dump(walked), "undo made by the conversion walks back the same"


def test_a_snapshot_of_another_layout_is_never_fetched_nor_kept(tmp_path):
    chain = Chain()
    chain.add([])
    index = _walker(tmp_path, chain)
    index.heard("peer", {"op": "hi", "rules": ord_rules.RULES, "sn": {"id": "a" * 64, "h": 9, "n": 1, "have": "all"}})
    assert "peer" not in index.snapshots.offers, "a layout-1 arcade's offer (sn) is not one this arcade can read"
    index.heard("peer", {"op": "hi", "rules": ord_rules.RULES, "sn2": {"id": "b" * 64, "h": 9, "n": 1, "have": "all"}})
    assert index.snapshots.offers["peer"]["id"] == "b" * 64
    snaps = index.snapshots
    snaps.manifest_path.write_text(json.dumps({"manifest": {"size": 3, "at": 0}, "id": "c" * 64}))
    snaps.snap_path.write_bytes(b"abc")
    fresh = dx.DoginalIndex(index.store.path, lambda: chain, lambda: "walk").snapshots
    assert fresh.made is None and not fresh.snap_path.exists(), "a layout-1 snapshot on disk is let go of"


@pytest.mark.parametrize("idle", [True, False])
def test_a_snapshot_nobody_fetches_from_is_let_go_of(tmp_path, monkeypatch, idle):
    from arcade import ord_snapshot
    chain = Chain()
    chain.add([])
    index = _walker(tmp_path, chain)
    index.walker.step(chain, dx.BASE, budget=60)
    made = index.snapshots.make()
    assert made["manifest"]["layout"] == dx.LAYOUT and index.snapshots.snap_path.exists()
    if idle:
        monkeypatch.setattr(ord_snapshot.time, "time", lambda: made["manifest"]["at"] + ord_snapshot.SNAP_IDLE + 5)
    index.snapshots.tidy()
    assert index.snapshots.snap_path.exists() is not idle
