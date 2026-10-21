"""Spend notes: a chunk of blocks, cut down to what the ord walk reads (2026-10-20: "each assigned a batch of
blocks to scan so that no 2 nodes are scanning the same blocks"; "the next one who finishes its chunk would just be
assigned the next chunk in line").

Who owns an inscription now, and every DRC-20 / PRC-20 balance, needs every block in order: a block can spend a coin
an earlier chunk inscribed. So an arcade that scans a chunk writes its NOTES -- every transaction's id, the coins it
spends, its outputs (value and owner), and input 0's script where it can open or continue an envelope -- and the walk
(doginal_index.Walker) applies notes in order exactly as it applies blocks read from a Core. Scanning, the slow part
(reading and decoding every block), is shared; stitching the notes together is quick and reads no chain.

What a note keeps, and why that is enough (doginal_index.Walker.walk_block reads no more):

* the block's 80-byte header: its hash and its previous block's, and the merkle root the transaction ids must hash to
  (`decode` proves that, so the list of transactions is the block's own);
* per transaction: its id, every input's outpoint, every output's value and owner;
* input 0's script only when it can matter to ord_rules.parse -- push-only, and starting with "ord" or a piece count;
* on a Taproot chain, the witness of an input that carries ord's witness envelope.

A chain of envelope pieces that completes inside the chunk and whose content cannot matter (no "p", no token
operation, no name claim) has its big pushes zeroed: the same lengths, so every size and number stays the same, and a
run of zeros costs nothing compressed. Everything else is kept whole.

Notes are canonical: two arcades that scan the same chunk write the same bytes, so `digest` is a second word on the
chunk beside its births hash, and two arcades agreeing on it means the notes are right.
"""
from __future__ import annotations

import hashlib
import zlib
from dataclasses import dataclass, field

from . import ord_rules, rawchain

#: The notes format. Part of every digest, so notes in another format are never compared with these.
VERSION = 1

_P2PKH, _P2SH, _HASHED = 0, 1, 2
_COINBASE, _SCRIPT, _WITNESS = 1, 2, 4


class NotesError(ValueError):
    """Notes that do not read, or that are not the block they say they are."""


class Hashed(bytes):
    """An output script kept as its hash160 (it pays no address): Walker.owner_of names it "script:<hash>" as it
    names the whole script."""


@dataclass
class NoteTx:
    txid: str
    vin: list[dict]
    vout: list[tuple[int, bytes]]


@dataclass
class NoteBlock:
    hash: str
    prev: str
    header: bytes
    txs: list[NoteTx] = field(default_factory=list)


# --------------------------------------------------------------------------- what can matter

def may_envelope(script: bytes) -> bool:
    """Can input 0's script open or continue an envelope (ord_rules.parse): push-only, two pushes or more, the first
    "ord" or a piece count? A plain signature (a 72-byte push first) cannot."""
    pushes = ord_rules.decode_push_datas(script)
    if pushes is None or len(pushes) < 2:
        return False
    return pushes[0] == b"ord" or ord_rules.push_number(pushes[0]) is not None


def content_matters(ctype: bytes, body: bytes) -> bool:
    """Does the walk read this content, beyond its length: a "p", a token operation on either protocol, a name?"""
    return (ord_rules.protocol_of(body) is not None
            or ord_rules.token_op("drc-20", ctype, body) is not None
            or ord_rules.token_op("prc-20", ctype, body) is not None
            or ord_rules.name_claim(ctype, body) is not None)


def _zeroed(script: bytes, genesis: bool) -> bytes:
    """The same script with every push longer than 8 bytes zeroed (a genesis keeps "ord", the piece count and the
    content type): the same operations and lengths, so parse reads the same pieces and the same sizes."""
    out, at, n = bytearray(), 0, 0
    while at < len(script):
        op = script[at]
        at += 1
        if 1 <= op <= 75:
            size, head = op, bytes([op])
        elif op == 0x4C:
            size, head = script[at], script[at - 1:at + 1]
            at += 1
        elif op == 0x4D:
            size, head = int.from_bytes(script[at:at + 2], "little"), script[at - 1:at + 2]
            at += 2
        elif op == 0x4E:
            size, head = int.from_bytes(script[at:at + 4], "little"), script[at - 1:at + 4]
            at += 4
        else:
            out.append(op)
            n += 1
            continue
        data = script[at:at + size]
        at += size
        keep = size <= 8 or (genesis and n < 3)
        out += head + (data if keep else bytes(len(data)))
        n += 1
    return bytes(out)


# --------------------------------------------------------------------------- writing

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _blob(b: bytes) -> bytes:
    return _varint(len(b)) + b


def _owner_bytes(script: bytes) -> bytes:
    from .script import hash160
    if len(script) == 25 and script[:3] == b"\x76\xa9\x14" and script[23:] == b"\x88\xac":
        return bytes([_P2PKH]) + script[3:23]
    if len(script) == 23 and script[:2] == b"\xa9\x14" and script[22:] == b"\x87":
        return bytes([_P2SH]) + script[2:22]
    return bytes([_HASHED]) + hash160(script)


def block_notes(block, taproot: bool = False) -> list:
    """One block's notes, before the chunk is finished: [header, [(txid, ins, outs)...]] with input 0's script kept
    whole where it may matter (the chunk's finish decides what is zeroed)."""
    txs = []
    for tx in block.txs:
        ins = []
        for j, i in enumerate(tx.vin):
            coinbase = i["vout"] == -1
            script = i["script"] if (j == 0 and not coinbase and may_envelope(i["script"])) else None
            witness = None
            if taproot and not coinbase and i.get("witness"):
                from .doginal_index import taproot_envelopes
                if taproot_envelopes(i["witness"]):
                    witness = list(i["witness"])
            ins.append((None if coinbase else i["txid"], i["vout"], script, witness))
        txs.append((tx.txid, ins, [(v, _owner_bytes(s)) for v, s in tx.vout]))
    return [block.header, txs]


def finish(blocks: list[bytes]) -> bytes:
    """A chunk's notes, canonical: its blocks' notes (`one_block`, in height order) with the content of every envelope
    that completes inside the chunk and cannot matter zeroed. The bytes `digest` and `pack` are of. Every arcade
    finishes a chunk from the same block notes the same way, whether it walked the blocks or helped with them."""
    parts = [_structure(b) for b in blocks]
    # Envelope chains inside the chunk, linked as the walk links them: a piece's input 0 spends the piece before it.
    chains: dict[str, list[tuple[int, int]]] = {}      # txid of the latest piece -> [(block, tx) of each piece]
    zero: dict[tuple[int, int], bool] = {}             # (block, tx) -> is it the genesis piece
    for b, (_header, txs) in enumerate(parts):
        for t, (txid, ins, _outs) in enumerate(txs):
            if not ins or ins[0][0] is None or ins[0][2] is None:
                continue
            prev, script = ins[0][0], ins[0][2]
            chain = chains.pop(prev, None)
            if chain is None and not script.startswith(b"\x03ord"):
                continue                                # a piece of a chain begun before this chunk: kept whole
            pieces = (chain or []) + [(b, t)]
            state, ctype, body = ord_rules.parse([parts[pb][1][pt][1][0][2] for pb, pt in pieces])
            if state == "partial":
                chains[txid] = pieces
            elif state == "complete" and not content_matters(ctype, body):
                zero.update({p: i == 0 for i, p in enumerate(pieces)})
    return _encode(parts, zero)


def one_block(block, taproot: bool = False) -> bytes:
    """One block's notes as read (nothing zeroed yet): what a scanner keeps until its chunk is whole."""
    return _encode([block_notes(block, taproot)], {})[1:]


def _encode(parts: list, zero: dict) -> bytes:
    out = bytearray([VERSION])
    out += _varint(len(parts))
    for b, (header, txs) in enumerate(parts):
        out += header + _varint(len(txs))
        for t, (txid, ins, outs) in enumerate(txs):
            out += bytes.fromhex(txid) + _varint(len(ins))
            for j, (ptxid, vout, script, witness) in enumerate(ins):
                flags = (_COINBASE if ptxid is None else 0) | (_SCRIPT if script is not None else 0) | \
                    (_WITNESS if witness is not None else 0)
                out.append(flags)
                if ptxid is not None:
                    out += bytes.fromhex(ptxid) + _varint(vout)
                if script is not None:
                    if j == 0 and (b, t) in zero:
                        script = _zeroed(script, zero[(b, t)])
                    out += _blob(script)
                if witness is not None:
                    out += _varint(len(witness)) + b"".join(_blob(w) for w in witness)
            out += _varint(len(outs))
            for value, owner in outs:
                out += _varint(value) + owner
    return bytes(out)


def _structure(raw: bytes) -> list:
    """`one_block`'s bytes back to [header, [(txid, ins, outs)...]] (outs as their owner bytes)."""
    r = _R(b"\x01" + raw)
    r.take(1)
    if r.varint() != 1:
        raise NotesError("block notes hold one block")
    header = r.take(80)
    txs = []
    for _t in range(r.varint()):
        txid = r.take(32).hex()
        ins = []
        for _i in range(r.varint()):
            flags = r.take(1)[0]
            ptxid, vout = (None, -1) if flags & _COINBASE else (r.take(32).hex(), r.varint())
            script = r.blob() if flags & _SCRIPT else None
            witness = [r.blob() for _w in range(r.varint())] if flags & _WITNESS else None
            ins.append((ptxid, vout, script, witness))
        outs = []
        for _o in range(r.varint()):
            value = r.varint()
            outs.append((value, r.take(21)))
        txs.append((txid, ins, outs))
    if r.at != len(r.data):
        raise NotesError("bytes left over after the block notes")
    return [header, txs]


def digest(notes: bytes) -> str:
    return hashlib.sha256(b"ord-notes\n" + notes).hexdigest()


def pack(notes: bytes) -> bytes:
    return zlib.compress(notes, 6)


def unpack(blob: bytes, limit: int = 256 * 1024 * 1024) -> bytes:
    d = zlib.decompressobj()
    try:
        out = d.decompress(blob, limit)
    except zlib.error as exc:
        raise NotesError(f"notes that do not unpack: {exc}") from None
    if d.unconsumed_tail:
        raise NotesError("notes larger than any chunk's")
    if not d.eof or d.unused_data:
        raise NotesError("notes cut short, or with bytes after them")
    return out


# --------------------------------------------------------------------------- reading

class _R:
    def __init__(self, data: bytes):
        self.data, self.at = data, 0

    def take(self, n: int) -> bytes:
        if self.at + n > len(self.data):
            raise NotesError("notes end too soon")
        got = self.data[self.at:self.at + n]
        self.at += n
        return got

    def varint(self) -> int:
        n = shift = 0
        while True:
            b = self.take(1)[0]
            n |= (b & 0x7F) << shift
            if not b & 0x80:
                return n
            shift += 7
            if shift > 70:
                raise NotesError("a number too long")

    def blob(self) -> bytes:
        return self.take(self.varint())


def _script_of(kind: int, h: bytes) -> bytes:
    if kind == _P2PKH:
        return b"\x76\xa9\x14" + h + b"\x88\xac"
    if kind == _P2SH:
        return b"\xa9\x14" + h + b"\x87"
    if kind == _HASHED:
        return Hashed(h)
    raise NotesError("an output of no kind")


def decode(notes: bytes) -> list[NoteBlock]:
    """The blocks of a chunk's notes, each proved to be the block its header names: the transaction ids hash up to
    the header's merkle root (what is said of each transaction beyond its id is the scanners' word, which is why two of
    them must agree on `digest`)."""
    r = _R(notes)
    if r.take(1)[0] != VERSION:
        raise NotesError("notes in another format")
    blocks = []
    for _ in range(r.varint()):
        header = r.take(80)
        fields = rawchain._header_fields(header, None)
        txs = []
        for _t in range(r.varint()):
            txid = r.take(32).hex()
            vin = []
            for _i in range(r.varint()):
                flags = r.take(1)[0]
                if flags & _COINBASE:
                    i = {"txid": "", "vout": -1, "script": b"", "witness": []}
                else:
                    i = {"txid": r.take(32).hex(), "vout": r.varint(), "script": b"", "witness": []}
                if flags & _SCRIPT:
                    i["script"] = r.blob()
                if flags & _WITNESS:
                    i["witness"] = [r.blob() for _w in range(r.varint())]
                vin.append(i)
            vout = []
            for _o in range(r.varint()):
                value = r.varint()
                owner = r.take(21)
                vout.append((value, _script_of(owner[0], owner[1:])))
            txs.append(NoteTx(txid, vin, vout))
        if not txs or rawchain.merkle_root([t.txid for t in txs]) != fields["merkleroot"]:
            raise NotesError(f"block {fields['hash']}: its transactions do not hash to its merkle root")
        blocks.append(NoteBlock(fields["hash"], fields["prev"], header, txs))
    if r.at != len(notes):
        raise NotesError("bytes left over after the notes")
    return blocks
