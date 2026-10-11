"""Publishing an inscription straight through a node: plan, coins, sign, send.

The interface writes an inscription as an OFFER: the node builds an unsigned
transaction naming the account's coins, a browser signs it, and the node keeps
the offer in memory until the signature comes back (arcade/web/app.py
`/account/inscribe` and `/account/sign`). That is right for a person at a tab.
It is wrong for a program that publishes a few hundred kilobytes in a dozen
transactions: the offer lives only until the interface restarts, the browser
between the two is a moving part that fails under memory pressure, and a send
that went out but was not answered gets sent again (2026-10-11: a
game module on the chain twice, paid for twice, during a release).

This is the same inscription with no offer and no browser. The key is here,
the coins are asked of the node, the transaction is built by the same code the
interface uses (`funding`, `encoding`, `inscribe`), signed here, and handed to
the node to broadcast. Nothing is held in anybody's memory between steps: what
has gone out is written in a journal beside the file, keyed by the file's
sha256, so a run that stops is picked up where it stopped and no piece is
paid for twice.

    arcade-publish -testnet --key ~/.arcade/publish.key game.js --json '{...}'

Shape of a run (one file):

1. `inscribe.plan` says how many transactions and what each carries.
2. One transaction: its payload outputs (Class C in an OP_RETURN where it
   fits, Class B marker + multisig outputs otherwise) paid from the address's
   confirmed coins, change back to the sender. Broadcast; done.
3. Several: a SPLIT first -- one output per piece, sized `inscribe.piece_size`
   -- broadcast and waited for (one block: the pieces spend its outputs, and
   the chain will not carry the whole package under an unconfirmed parent,
   see `_inscribe_piece`'s note). Then every piece, each spending its own
   output, all broadcast at once. The inscription's id is the txid of piece
   0, which carries the manifest; the same naming the index uses.

The coins come from the node's own wallet view: the address is imported there
watch-only (`importaddress`, once; the node rescans), and `listunspent` names
its coins. Nothing of the arcade's index is needed, so this runs against a
bare node on another machine through an ssh tunnel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable

from . import fees
from . import funding
from . import inscribe as inscribelib
from . import inscriptions as inscriptionlib
from . import media
from . import payload as P
from .config import MAINNET, REGTEST, TESTNET, Params, RpcCredentials, load_rpc_credentials
from .encoding import MAX_CLASS_B_PAYLOAD, encode_class_b, encode_class_c, max_class_c_payload
from .messaging import sender as sendermod
from .rpc import RpcClient, RpcError
from .script import b58check_encode, hash160
from .txbuild import build_raw_tx, multisig_script, op_return_script, p2pkh_script

#: The secp256k1 group order: a secret is 1 .. N-1, and a signature's s is kept
#: in the low half (BIP-62), which is what the network relays.
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141

#: Confirmations a split needs before its pieces go out.
SPLIT_CONFIRMATIONS = 1


class PublishError(Exception):
    """Something that stops a publication, said in words a person can act on."""


class Key:
    """A secp256k1 secret, held here, that signs for one address.

    Read from a file holding the secret as 64 hex digits (mode 0600), the way
    `referee.key` is kept. The same key makes the same address on every chain
    but for the version byte, so one file serves testnet and mainnet.
    """

    def __init__(self, secret: int):
        if not 1 <= secret < N:
            raise PublishError("that is not a secp256k1 secret")
        from cryptography.hazmat.primitives.asymmetric import ec
        self._key = ec.derive_private_key(secret, ec.SECP256K1())

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> "Key":
        text = Path(path).read_text().strip()
        try:
            return cls(int(text, 16))
        except ValueError:
            raise PublishError(f"{path} does not hold a 64-hex-digit secret") from None

    @property
    def pubkey(self) -> bytes:
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        return self._key.public_key().public_bytes(Encoding.X962, PublicFormat.CompressedPoint)

    def address(self, params: Params) -> str:
        return b58check_encode(params.pubkeyhash_version, hash160(self.pubkey))

    def sign(self, digest: bytes, sighash_type: int = funding.SIGHASH_ALL) -> bytes:
        """DER, low-S, and the sighash byte: what a scriptSig carries."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        der = self._key.sign(bytes(digest), ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        r, s = utils.decode_dss_signature(der)
        if s > N // 2:
            s = N - s
        return utils.encode_dss_signature(r, s) + bytes([sighash_type])


def payload_outputs(params: Params, address: str, pubkey: bytes, body: bytes) -> list[tuple[int, bytes]]:
    """The outputs that carry one inscription body, in whichever class fits.

    The body is wrapped in `AnyData` (type 200) here and nowhere else -- the
    rule `_class_c_or_b` keeps for the interface, kept for the same reason: an
    unwrapped body reads as an Omni header the token engine stops on, and a
    body wrapped twice is paid for and never appears.
    """
    payload = P.AnyData(data=body).encode()
    if len(payload) <= max_class_c_payload():
        return [(0, op_return_script(encode_class_c(payload)))]
    if len(payload) > MAX_CLASS_B_PAYLOAD:
        raise PublishError(f"a body of {len(payload)} bytes does not fit one transaction "
                           f"(at most {MAX_CLASS_B_PAYLOAD}); the plan should have split it")
    outputs = [(sendermod.OUTPUT_VALUE, p2pkh_script(params.marker))]
    for group in encode_class_b(address, pubkey, payload):
        outputs.append((sendermod.OUTPUT_VALUE, multisig_script(list(group.keys), group.required)))
    return outputs


class NodeCoins:
    """An address's confirmed coins, as the node's wallet sees them.

    `watch` imports the address watch-only (idempotent; the first time the
    node rescans, which on testnet is minutes and on regtest nothing).
    Between a broadcast and its block the node still lists the coin just
    spent and not the change that came back, so `spent` and `extra` carry
    what this run knows and the wallet does not yet.
    """

    def __init__(self, rpc: RpcClient, address: str):
        self.rpc, self.address = rpc, address
        self.spent: set[tuple[str, int]] = set()
        self.extra: list[dict] = []

    def watch(self, rescan: bool = True) -> None:
        try:
            info = self.rpc.call("validateaddress", self.address)
        except RpcError:
            info = {}
        if info.get("iswatchonly") or info.get("ismine"):
            return
        self.rpc.call("importaddress", self.address, "arcade-publish", rescan)

    def unspent(self, minconf: int = 1) -> list[dict]:
        rows = self.rpc.call("listunspent", minconf, 9_999_999, [self.address])
        coins = [{"txid": str(r["txid"]), "vout": int(r["vout"]),
                  "value": int(round(float(r["amount"]) * fees.COIN)),
                  "address": self.address} for r in rows]
        have = {(c["txid"], c["vout"]) for c in coins}
        coins += [dict(c) for c in self.extra if (c["txid"], c["vout"]) not in have]
        return [c for c in coins if (c["txid"], c["vout"]) not in self.spent]

    def note(self, unsigned: funding.Unsigned, txid: str) -> None:
        """A transaction went out: its inputs are gone, its change is ours."""
        for coin in unsigned.inputs:
            self.spent.add((coin["txid"], coin["vout"]))
        for n, (value, script) in enumerate(unsigned.outputs):
            if script == p2pkh_script(self.address) and value > 0:
                self.extra.append({"txid": txid, "vout": n, "value": value, "address": self.address})


def choose(coins: list[dict], target: int) -> list[dict]:
    """Coins worth at least `target`: the smallest sufficient one first, else
    the largest until there is enough (the rule `funding.choose` follows, so a
    split wallet is not collapsed back into one coin)."""
    big_enough = [c for c in coins if c["value"] >= target]
    ordered = (sorted(big_enough, key=lambda c: c["value"]) if big_enough
               else sorted(coins, key=lambda c: -c["value"]))
    chosen, total = [], 0
    for coin in ordered:
        chosen.append(coin)
        total += coin["value"]
        if total >= target:
            return chosen
    raise PublishError(f"not enough confirmed coins at this address: {total / fees.COIN:.8f} "
                       f"here, {target / fees.COIN:.8f} needed")


def build(address: str, coins: list[dict], outputs: list[tuple[int, bytes]], rate: int,
          what: str = "") -> funding.Unsigned:
    """`funding.build`, paid out of coins the node named instead of the index's."""
    spend = sum(value for value, _ in outputs)
    guess = funding.price(1, outputs, rate, change=True)
    chosen = choose(coins, spend + guess)
    fee = funding.price(len(chosen), outputs, rate, change=True)
    if sum(c["value"] for c in chosen) < spend + fee:
        chosen = choose(coins, spend + fee)
        fee = funding.price(len(chosen), outputs, rate, change=True)
    total = sum(c["value"] for c in chosen)
    change = total - spend - fee
    outs = list(outputs)
    if change >= fees.DUST_LIMIT:
        outs.append((change, p2pkh_script(address)))
    else:
        fee += max(0, change)
        change = 0
    raw = build_raw_tx([(c["txid"], c["vout"]) for c in chosen], outs)
    script = p2pkh_script(address)
    hashes = [funding.sighash(chosen, outs, n, script).hex() for n in range(len(chosen))]
    return funding.Unsigned(raw=raw, inputs=chosen, outputs=outs, sighashes=hashes,
                            fee=fee, change=change, what=what)


class Journal:
    """What has gone out for one file, by its sha256: survives a stop."""

    def __init__(self, home: Path, digest: str):
        self.path = Path(home) / f"{digest}.json"
        self.data: dict[str, Any] = {"sha256": digest, "tag": "", "split": "", "pieces": {}, "txids": []}
        if self.path.exists():
            self.data.update(json.loads(self.path.read_text()))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        os.replace(tmp, self.path)


class Publisher:
    """One address publishing inscriptions through one node."""

    def __init__(self, rpc: RpcClient, params: Params, key: Key, journal: str | os.PathLike,
                 rate: int = fees.MIN_FEE_PER_KB, say: Callable[[str], None] | None = None):
        self.rpc, self.params, self.key = rpc, params, key
        self.address = key.address(params)
        self.coins = NodeCoins(rpc, self.address)
        self.journal_home = Path(journal)
        self.rate = rate
        self.say = say or (lambda s: None)

    # ---------------------------------------------------------------- one transaction
    def _send(self, unsigned: funding.Unsigned) -> str:
        sigs = [self.key.sign(bytes.fromhex(h)).hex() for h in unsigned.sighashes]
        raw = funding.assemble(unsigned, sigs, self.key.pubkey)
        txid = str(self.rpc.call("sendrawtransaction", raw))
        self.coins.note(unsigned, txid)
        return txid

    def _confirmations(self, txid: str) -> int | None:
        """How deep a transaction is, None if the node has no record of it."""
        try:
            tx = self.rpc.call("getrawtransaction", txid, True)
        except RpcError:
            return None
        return int(tx.get("confirmations") or 0)

    def wait_for(self, txid: str, confirmations: int = SPLIT_CONFIRMATIONS,
                 timeout: float = 3600.0, poll: float = 5.0,
                 while_waiting: Callable[[], None] | None = None) -> None:
        """Until `txid` is `confirmations` deep. `while_waiting` is for a
        regtest, where nothing is mined unless the caller mines it."""
        t0 = time.time()
        while True:
            deep = self._confirmations(txid)
            if deep is None:
                raise PublishError(f"the node has lost {txid}: it was broadcast and is neither in "
                                   f"its mempool nor in a block. Nothing more has been sent.")
            if deep >= confirmations:
                return
            if time.time() - t0 > timeout:
                raise PublishError(f"{txid} has not been mined in {timeout:.0f} s; run again to resume")
            if while_waiting:
                while_waiting()
            time.sleep(poll)

    # ---------------------------------------------------------------- the inscription
    def inscribe(self, content: bytes, content_type: str = "", json_text: str = "", name: str = "",
                 while_waiting: Callable[[], None] | None = None) -> dict:
        """Put one file on the chain. Returns {id, txids, chunks, resumed}.

        Asked again with the same bytes it finishes what the journal says is
        unfinished and sends nothing twice; asked with a file already fully
        sent it answers the same ids and sends nothing.
        """
        if not content:
            raise PublishError("there is nothing to inscribe")
        digest = hashlib.sha256(content).hexdigest()
        journal = Journal(self.journal_home, digest)
        kind = media.standard_type(content_type, name) or "application/octet-stream"
        if not journal.data["tag"]:
            journal.data["tag"] = secrets.token_bytes(8).hex()
            journal.data["name"] = name
            journal.save()
        plan = inscribelib.plan(content, kind, json_text, inscription_id=bytes.fromhex(journal.data["tag"]))
        resumed = bool(journal.data["txids"] or journal.data["split"])
        if len(journal.data["txids"]) == plan.chunks:
            return {"id": journal.data["txids"][0], "txids": list(journal.data["txids"]),
                    "chunks": plan.chunks, "resumed": True}

        self.coins.watch()
        if plan.chunks == 1:
            outputs = payload_outputs(self.params, self.address, self.key.pubkey, plan.payloads[0])
            unsigned = build(self.address, self.coins.unspent(), outputs, self.rate, f"inscribe {name or kind}")
            txid = self._send(unsigned)
            journal.data["txids"] = [txid]
            journal.save()
            self.say(f"inscribed {name or kind}: {txid}")
            return {"id": txid, "txids": [txid], "chunks": 1, "resumed": False}

        piece = inscribelib.piece_size(plan)
        if not journal.data["split"] or self._confirmations(journal.data["split"]) is None:
            split = build(self.address, self.coins.unspent(),
                          [(piece, p2pkh_script(self.address))] * plan.chunks, self.rate,
                          f"split into {plan.chunks} outputs, to inscribe {name or kind}")
            journal.data["split"] = self._send(split)
            journal.data["pieces"] = {}
            journal.save()
            self.say(f"split {name or kind} into {plan.chunks} pieces: {journal.data['split']}")
        self.wait_for(journal.data["split"], while_waiting=while_waiting)

        for n, body in enumerate(plan.payloads):
            if str(n) in journal.data["pieces"]:
                continue
            coin = {"txid": journal.data["split"], "vout": n, "value": piece, "address": self.address}
            outputs = payload_outputs(self.params, self.address, self.key.pubkey, body)
            unsigned = funding.build_one(self.params, self.address, coin, outputs, self.rate,
                                         f"piece {n + 1} of {plan.chunks} of {name or kind}")
            try:
                txid = self._send(unsigned)
            except RpcError as exc:
                # The output is already spent: this piece went out in a run whose
                # journal entry never got written. Find it rather than pay again.
                txid = self._already_sent(journal.data["split"], n)
                if not txid:
                    raise PublishError(f"piece {n + 1} could not be sent ({exc}); nothing further sent") from exc
            journal.data["pieces"][str(n)] = txid
            journal.save()
            self.say(f"piece {n + 1} of {plan.chunks}: {txid}")
        txids = [journal.data["pieces"][str(n)] for n in range(plan.chunks)]
        journal.data["txids"] = txids
        journal.save()
        return {"id": txids[0], "txids": txids, "chunks": plan.chunks, "resumed": resumed}

    def _already_sent(self, split: str, vout: int) -> str:
        """The txid that spent output `vout` of the split, if the node can see it."""
        try:
            out = self.rpc.call("gettxout", split, vout, True)
        except RpcError:
            return ""
        if out is not None:
            return ""
        # Spent. The wallet has the address watch-only, so the spending transaction is in its list.
        try:
            for row in self.rpc.call("listtransactions", "*", 500, 0, True):
                txid = str(row.get("txid") or "")
                if not txid:
                    continue
                tx = self.rpc.call("getrawtransaction", txid, True)
                for vin in tx.get("vin", []):
                    if vin.get("txid") == split and int(vin.get("vout", -1)) == vout:
                        return txid
        except RpcError:
            pass
        return ""


# ------------------------------------------------------------------------- the command
def _params(args) -> Params:
    return MAINNET if args.main else REGTEST if args.regtest else TESTNET


def _rpc(args, params: Params) -> RpcClient:
    if args.rpc_url:
        host, _, port = args.rpc_url.rpartition("://")[2].rstrip("/").partition(":")
        password = args.rpc_password or os.environ.get("ARCADE_RPC_PASSWORD", "")
        user = args.rpc_user
        if args.rpc_cookie:
            user, _, password = Path(args.rpc_cookie).read_text().strip().partition(":")
        if not user or not password:
            raise PublishError("--rpc-url needs --rpc-user and --rpc-password (or ARCADE_RPC_PASSWORD), or --rpc-cookie")
        creds = RpcCredentials(host or "127.0.0.1", int(port or params.rpc_port), user, password)
    else:
        creds = load_rpc_credentials(params, datadir=Path(args.datadir) if args.datadir else None)
    return RpcClient(creds, timeout=300)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="arcade-publish", description=__doc__.split("\n\n")[0])
    net = ap.add_mutually_exclusive_group()
    net.add_argument("-main", action="store_true", help="mainnet (testnet unless said)")
    net.add_argument("-testnet", action="store_true")
    net.add_argument("-regtest", action="store_true")
    ap.add_argument("--key", required=True, help="file holding the secret as 64 hex digits")
    ap.add_argument("--datadir", help="the node's datadir, for its cookie or conf")
    ap.add_argument("--rpc-url", help="the node's RPC, e.g. http://127.0.0.1:44555 (an ssh tunnel)")
    ap.add_argument("--rpc-user")
    ap.add_argument("--rpc-password", help="or ARCADE_RPC_PASSWORD in the environment")
    ap.add_argument("--rpc-cookie", help="the node's .cookie file, copied here")
    ap.add_argument("--journal", default=os.path.expanduser("~/.dogecoinarcade/publish"),
                    help="where what has gone out is written (default ~/.dogecoinarcade/publish)")
    ap.add_argument("--json", default="", help="the JSON beside the file")
    ap.add_argument("--name", default="", help="the inscription's name (default: the file's)")
    ap.add_argument("--type", default="", help="content type (default: from the file name)")
    ap.add_argument("--whoami", action="store_true", help="say the address and its confirmed balance, send nothing")
    ap.add_argument("file", nargs="?")
    args = ap.parse_args(argv)
    try:
        params = _params(args)
        key = Key.from_file(args.key)
        with _rpc(args, params) as rpc:
            pub = Publisher(rpc, params, key, args.journal, say=lambda s: print("   " + s, flush=True))
            if args.whoami or not args.file:
                pub.coins.watch()
                coins = pub.coins.unspent()
                print(json.dumps({"address": pub.address, "network": params.name,
                                  "confirmed": sum(c["value"] for c in coins) / fees.COIN,
                                  "coins": len(coins)}, indent=1))
                return 0
            data = Path(args.file).read_bytes()
            out = pub.inscribe(data, args.type, args.json, args.name or os.path.basename(args.file))
            print(json.dumps(out, indent=1))
            return 0
    except (PublishError, funding.FundingError, inscriptionlib.InscriptionError, RpcError) as exc:
        print(f"arcade-publish: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
