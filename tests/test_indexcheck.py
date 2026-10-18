"""Arcades comparing indexes over the mesh (2026-10-17): equal indexes agree; one row read
differently is found at its block, with both sides' rows to show."""
from __future__ import annotations

import contextlib
import sqlite3
import types

from arcade import index_digest
from arcade.mesh.indexcheck import IndexCheck

from test_mesh import addr, fast, mk, run, started, until  # noqa: F401

FLOOR = 1000


class FakeIndex:
    """An index file with the consensus tables, filled with one protocol transaction every 7th block."""

    def __init__(self, path, tip=1500, odd=None):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        from arcade.state import SCHEMA
        self.conn.executescript(SCHEMA)
        self.tip = tip
        for h in range(FLOOR, tip + 1, 7):
            valid = 0 if h == odd else 1                   # one arcade judged one transaction differently
            self.conn.execute("INSERT INTO arcade_tx (txid, block_height, position, encoding_class, sender, "
                              "payload_hex, valid) VALUES (?, ?, 1, 'B', 'Psender', 'ab', ?)", (f"{h:064x}", h, valid))
            if (h - FLOOR) % 49 == 0:
                self.conn.execute("INSERT INTO tag VALUES (?, ?, ?, ?, 2)", (f"name{h}", f"P{h}", f"{h:064x}", h))
        self.conn.commit()

    @contextlib.contextmanager
    def open(self):
        yield types.SimpleNamespace(conn=self.conn)

    def indexed_height(self):
        return self.tip


def _check(index):
    fp = index_digest.Fingerprints(index, FLOOR)
    return IndexCheck(lambda: {"test": fp})


def test_equal_indexes_agree_and_a_row_read_differently_is_found_at_its_block(tmp_path):
    a_ix = FakeIndex(tmp_path / "a.sqlite")
    b_ix = FakeIndex(tmp_path / "b.sqlite", tip=1460)               # behind: compared at its checkpoint
    c_ix = FakeIndex(tmp_path / "c.sqlite", odd=1238)

    async def go():
        a, b, c = mk(), mk(), mk()
        ia, ib, ic = _check(a_ix), _check(b_ix), _check(c_ix)
        for n, ext in ((a, ia), (b, ib), (c, ic)):
            n.add_extension(ext)
        await started(a, b, c)
        try:
            a.add_address(*addr(b)); a.add_address(*addr(c))
            await until(lambda: len(ia.results.get("test", {})) == 2, what="a compared with both")
            with_b, with_c = ia.results["test"][b.node_id], ia.results["test"][c.node_id]
            assert with_b["agree_upto"] == 1400 and "differs_at" not in with_b, "b's checkpoint, 1460 - 6 -> 1400"
            assert with_c["differs_at"] == 1238
            assert any("|1\n" in r or r.endswith("|1") for r in with_c["ours"])
            assert with_c["ours"] != with_c["theirs"]
            said = ia.status()["test"]
            assert said["compared"] == 2 and said["agree"] == 1 and said["differ"][0]["differs_at"] == 1238
        finally:
            for n in (a, b, c):
                await n.stop()
    run(go())


def test_a_block_fingerprint_ignores_what_later_blocks_change(tmp_path):
    ix = FakeIndex(tmp_path / "x.sqlite")
    fp = index_digest.Fingerprints(ix, FLOOR)
    before = fp.digest(FLOOR, 1400)
    # An inscription's current owner moves with later transfers: not part of its block's fingerprint.
    ix.conn.execute("INSERT INTO inscription (txid, number, creator, owner, block_height, position, content_type, "
                    "content_len, sha256, chunks) VALUES ('aa', 1, 'Pc', 'Pc', 1300, 3, 'image/png', 9, 'ff', 1)")
    with_piece = index_digest.Fingerprints(ix, FLOOR).digest(FLOOR, 1400)
    assert with_piece != before
    ix.conn.execute("UPDATE inscription SET owner = 'Pnew' WHERE txid = 'aa'")
    assert index_digest.Fingerprints(ix, FLOOR).digest(FLOOR, 1400) == with_piece
    # Nor a name's current claim, which moves to a later block when the name is claimed again.
    assert ix.conn.execute("UPDATE tag SET block_height = 1450, claimed_txid = 'bb' "
                           "WHERE block_height = 1049").rowcount == 1
    assert index_digest.Fingerprints(ix, FLOOR).digest(FLOOR, 1400) == with_piece
    assert index_digest.checkpoint(1406, FLOOR) == 1400 and index_digest.checkpoint(1405, FLOOR) == 1300
    assert index_digest.checkpoint(1003, FLOOR) is None
