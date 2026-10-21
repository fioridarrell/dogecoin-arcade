"""Wallets an account imports: the node's half (plan item 18, 2026-10-20).

"We should be able to import any with private key or 12 word seed phrase, even if that wallet has not been used in
Dogecoin arcade." The key or the phrase never comes here. The browser derives the wallet's address, seals the secret
in a second record under the account's password (accounts.ImportedWallets), and tells this node only the ADDRESS --
with a signature by that address's key over the account and the address, so an account can register an address only
when it holds the key. The node then watches the address (utxos.watch), reads back the coins it already held, and
builds unsigned sends from it, exactly as it does for the account's own address. Only the browser can sign.

What lives here is what the routes in web/app.py share and the tests hold still: the proof, the address a key makes
for each kind, the per-account list kept in the node's settings, and how a wait is said.
"""

from __future__ import annotations

import hashlib
import json
import time

from .script import b58check_encode, hash160, p2sh_p2wpkh_address, p2wpkh_address

#: How many imported addresses one account may have on one chain (the operator's design, 2026-10-21).
CAP_PER_CHAIN = 20
#: How many imports (registrations) one account may make in a day, every chain together: each can start a read-back
#: of the chain, and an account that imported and removed wallets in a loop would keep every other import waiting.
PER_DAY = 40

#: The address kinds an import may be: P2PKH on every chain; on a test chain with SegWit (L test), its two SegWit
#: kinds too, the same ones the account's own words offer there (wallet.js ADDRESS_KINDS).
KINDS = ("p2pkh", "p2wpkh", "p2sh-p2wpkh")

#: The longest name a wallet may have.
NAME_MAX = 40


class ImportRefused(ValueError):
    """An import this node will not take, said so a person can act on it."""


def setting_key(network: str, account: str) -> str:
    """Where one account's imported addresses on one chain are listed (a JSON list in the node's settings)."""
    return f"extra_address:{network}:{(account or '').lower()}"


def name_key(network: str, account: str) -> str:
    """The account's MAIN wallet's name on one chain: not a secret, so a setting (an imported wallet's name is in its
    sealed record)."""
    return f"wallet_name:{network}:{(account or '').lower()}"


def entries(raw) -> list[dict]:
    """The listed addresses, from what the setting holds: a list of {"a": address, "k": kind, "p": pubkey hex,
    "t": when}. Anything else is read as nothing rather than trusted."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    out = []
    for one in raw if isinstance(raw, list) else []:
        if isinstance(one, dict) and isinstance(one.get("a"), str) and one.get("k") in KINDS:
            out.append({"a": one["a"], "k": one["k"], "p": str(one.get("p") or ""), "t": int(one.get("t") or 0)})
    return out


def proof_message(account: str, network: str, address: str, pubkey: str) -> bytes:
    """What the imported key signs to register its address for one account (coins.js `importProof` makes the same
    bytes). Naming the account makes a proof useless to anybody else; naming the chain, useless on another chain."""
    return (f"DogecoinArcade wallet import\n{(account or '').lower()}\n{network}\n{address}\n"
            f"{(pubkey or '').lower()}").encode()


def proof_digest(account: str, network: str, address: str, pubkey: str) -> bytes:
    """The 32 bytes the key signs: SHA-256 twice over the message, as a transaction's hash is."""
    return hashlib.sha256(hashlib.sha256(proof_message(account, network, address, pubkey)).digest()).digest()


def address_of(params, pubkey: bytes, kind: str) -> str:
    """The address `pubkey` makes on this chain, of this kind."""
    if kind == "p2pkh":
        if len(pubkey) not in (33, 65):
            raise ImportRefused("that is not a public key")
        return b58check_encode(params.pubkeyhash_version, hash160(pubkey))
    if kind not in KINDS:
        raise ImportRefused("that is not an address kind this arcade knows")
    if getattr(params, "real_coins", False) or not getattr(params, "bech32_hrp", ""):
        raise ImportRefused("SegWit wallets are on test chains with SegWit only, for now")
    if len(pubkey) != 33:
        raise ImportRefused("a SegWit address is made from a compressed public key")
    if kind == "p2wpkh":
        return p2wpkh_address(params, hash160(pubkey))
    return p2sh_p2wpkh_address(params, hash160(pubkey))


def verify_signature(digest: bytes, signature: bytes, pubkey: bytes) -> bool:
    """A low-S DER signature by `pubkey` over `digest` (the check the Doginal market uses)."""
    from .doginal_market import verify_signature as check
    return check(digest, signature, pubkey)


def check_proof(params, account: str, network: str, address: str, pubkey_hex: str, kind: str,
                signature_hex: str) -> bytes:
    """The public key, once the address is that key's of that kind and the key signed the proof; else
    ImportRefused. This is what makes an imported address the account's own: nobody registers an address whose key
    they do not hold."""
    try:
        pubkey = bytes.fromhex(str(pubkey_hex or ""))
        signature = bytes.fromhex(str(signature_hex or ""))
    except ValueError:
        raise ImportRefused("the import's key proof is not readable; try again") from None
    if address_of(params, pubkey, kind) != address:
        raise ImportRefused("that address is not this key's")
    if not signature or not verify_signature(proof_digest(account, network, address, pubkey.hex()),
                                             signature, pubkey):
        raise ImportRefused("the key did not sign this import, so it was not taken")
    return pubkey


def clean_name(text) -> str:
    """A wallet's name as kept: one line, trimmed, at most NAME_MAX characters."""
    return " ".join(str(text or "").split())[:NAME_MAX]


def say_wait(seconds: float | None) -> str:
    """How long, said the way a person reads it: "about 25 minutes", "about 2 hours", "a few minutes"."""
    if seconds is None:
        return "a few minutes"
    minutes = int(round(max(0.0, float(seconds)) / 60.0))
    if minutes <= 2:
        return "a few minutes"
    if minutes < 55:
        return f"about {minutes} minutes"
    hours = minutes / 60.0
    if hours < 1.5:
        return "about an hour"
    if hours < 36:
        return f"about {int(round(hours))} hours"
    return f"about {int(round(hours / 24.0))} days"


def recent(stamps, now: float | None = None) -> list[int]:
    """The registration times still inside the last day."""
    now = time.time() if now is None else now
    return [int(t) for t in (stamps if isinstance(stamps, list) else []) if isinstance(t, (int, float))
            and now - float(t) < 86400]
