"""0-conf for NFT and token locks (2026-10-17: "implement the same type of logic for NFT's
doginals"): an unconfirmed lock counts once the pool shows it moving exactly the asset in."""
from arcade import trade as T

LOCK, OWNER, PIECE, TX = "nLockAddress", "nOwner", "ab" * 32, "cd" * 32


class Index:
    def __init__(self, rows, others=(), balance=0):
        self.rows, self.others, self.bal = rows, list(others), balance

    def pool_moves_of(self, txid):
        return self.rows if txid == TX else []

    def pending_moves(self):
        return [{"txid": TX, **r} for r in self.rows] + self.others

    def balance(self, address, pid):
        return self.bal


def nft():
    return T.Leg("test", "nft", PIECE, 1, "02" * 33, "03" * 33)


def token(n):
    return T.Leg("test", "token", "26", n, "02" * 33, "03" * 33)


def move(**kw):
    return {"kind": "inscription", "inscription": PIECE, "from": OWNER, "to": LOCK, "why": "transfer", **kw}


def test_a_piece_moving_into_the_lock_in_the_pool_counts():
    assert T.asset_at(Index([move()]), nft(), LOCK, None, lock_txid=TX) is True


def test_anything_short_of_that_waits_for_the_block_and_is_never_called_wrong():
    assert T.asset_at(Index([move()]), nft(), LOCK, None) is None, "no lock txid: the old rule"
    assert T.asset_at(Index([move(to="nElsewhere")]), nft(), LOCK, None, lock_txid=TX) is None
    assert T.asset_at(Index([]), nft(), LOCK, None, lock_txid=TX) is None
    racing = Index([move()], others=[{"txid": "ef" * 32, **move(to="nThief")}])
    assert T.asset_at(racing, nft(), LOCK, None, lock_txid=TX) is None, "another move of it in the pool"


def test_tokens_count_when_the_confirmed_balance_covers_every_pending_send():
    send = {"kind": "token", "property_id": 26, "units": 100, "from": OWNER, "to": LOCK, "why": "send"}
    assert T.asset_at(Index([send], balance=100), token(100), LOCK, None, lock_txid=TX) is True
    assert T.asset_at(Index([send], balance=99), token(100), LOCK, None, lock_txid=TX) is None
    spent = Index([send], others=[{"txid": "ef" * 32, **send, "to": "nElse"}], balance=150)
    assert T.asset_at(spent, token(100), LOCK, None, lock_txid=TX) is None, "the same coins sent twice"
    assert T.asset_at(Index([send], balance=500), token(101), LOCK, None, lock_txid=TX) is None, "too few"
