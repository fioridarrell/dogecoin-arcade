"""A fifth of every fee on a borrowed chain goes to the arcade lending it (2026-10-28: "On a borrowed chain 20%
of all fees should go to the operator who is loaning the chain"; a share under the chain's minimum is "raised to the
minimum"). A lock's operator fee is split in the claim: the locker's operator and the lending arcade each get an output
of their own (trade.fee_split). The lender's address travels in the terms (fee_share_to); an arcade from before that
day drops it and the lock pays the locker's operator alone; a taker may never put another address there.
"""
import dataclasses
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_htlc import Key                                              # noqa: E402

from arcade import htlc as H, node_trader as NT, trade as T            # noqa: E402
from arcade import trade_messages as M, trade_orders as O               # noqa: E402
from arcade.txbuild import p2pkh_script                                # noqa: E402

COIN = 100_000_000
OPERATOR = Key(30).address           # the locker's arcade, on Dogecoin
LENDER = Key(30).address             # the arcade it borrows Dogecoin from


def _leg(amount=10 * COIN, bps=50, share=LENDER, kind="coin", asset=""):
    a, b = Key(), Key()
    return T.Leg("doge", kind, asset, amount, a.pub.hex(), b.pub.hex(), bps, OPERATOR if bps else "", share)


def test_the_fee_is_split_a_fifth_to_the_lender_each_at_least_the_floor():
    floor = T.fee_floor("doge")
    leg = _leg()                                                     # 0.5% of 10 DOGE = 0.05 DOGE
    assert T.operator_fee(leg) == 5_000_000
    assert T.fee_split(leg) == [(4_000_000, OPERATOR), (1_000_000, LENDER)]
    assert T.held_fee(leg) == 5_000_000
    small = _leg(amount=COIN)                                        # 0.005 DOGE: the floor, 0.01, for both
    assert T.fee_split(small) == [(floor, OPERATOR), (floor, LENDER)] and T.held_fee(small) == 2 * floor
    assert T.lock_value(small) == COIN + T.claim_fee("doge") + 2 * floor, "the lock holds both outputs"
    assert T.fee_split(_leg(share="")) == [(5_000_000, OPERATOR)], "a chain run here: the operator alone"
    assert T.fee_split(_leg(bps=0)) == [], "no fee, nothing to share"
    assert T.fee_split(dataclasses.replace(leg, fee_share_to=OPERATOR)) == [(5_000_000, OPERATOR)]


def test_a_claim_pays_both_and_the_claimer_exactly_the_amount():
    leg = _leg()
    coin = {"txid": "38" * 32, "vout": 0, "value": T.lock_value(leg)}
    me = Key(30).address
    _, outs = H.pay_out(coin, me, fee=T.claim_fee("doge"), operator=T.payees(leg))
    assert outs == [(4_000_000, p2pkh_script(OPERATOR)), (1_000_000, p2pkh_script(LENDER)), (10 * COIN, p2pkh_script(me))]
    # One pair, as before, still works.
    _, one = H.pay_out(coin, me, fee=T.claim_fee("doge"), operator=(5_000_000, OPERATOR))
    assert one == [(5_000_000, p2pkh_script(OPERATOR)), (10 * COIN, p2pkh_script(me))]
    with pytest.raises(H.HtlcError):
        H.pay_out(coin, me, operator=[(5_000_000, OPERATOR), (999, LENDER)])       # under the dust line


def test_a_doginal_claim_keeps_the_inscription_first_and_pays_both_from_the_reserve():
    leg = _leg(amount=1, kind="doginal", asset="ab" * 32 + "i0")
    reserve = T.doginal_reserve(leg)
    assert reserve == H.DOGINAL_RESERVE + 2 * T.fee_floor("doge")
    me = Key(30).address
    _, outs = H.doginal_out({"txid": "aa" * 32, "vout": 0, "value": 100_000},
                            {"txid": "aa" * 32, "vout": 1, "value": reserve}, me, operator=T.payees(leg))
    assert outs[0] == (100_000, p2pkh_script(me))
    assert outs[1:] == [(T.fee_floor("doge"), p2pkh_script(OPERATOR)), (T.fee_floor("doge"), p2pkh_script(LENDER))]


def test_the_lenders_address_travels_with_the_leg_and_an_old_leg_has_none():
    leg = _leg()
    said = M.leg_json(leg)
    assert said["fee_share_to"] == LENDER and M.leg_from(said) == leg
    old = {k: v for k, v in said.items() if k != "fee_share_to"}
    assert M.leg_from(old).fee_share_to == "", "an arcade from before 2026-10-28"
    with pytest.raises(M.TradeMessageError):
        M.leg_from({**said, "fee_share_to": "not an address"})
    assert "fee_share_to" not in M.leg_json(_leg(share=""))


def test_an_order_names_the_lender_beside_its_fee():
    side = O._side({"network": "doge", "kind": "coin", "asset": "", "amount": COIN,
                    "fee_bps": 50, "fee_to": OPERATOR, "fee_share_to": LENDER})
    assert side["fee_share_to"] == LENDER
    plain = O._side({"network": "doge", "kind": "coin", "asset": "", "amount": COIN, "fee_bps": 50, "fee_to": OPERATOR})
    assert "fee_share_to" not in plain
    with pytest.raises(O.OrderError):
        O._side({"network": "doge", "kind": "coin", "asset": "", "amount": COIN,
                 "fee_bps": 50, "fee_to": OPERATOR, "fee_share_to": "junk"})


def test_a_taker_may_drop_the_lenders_share_but_never_name_another():
    stood = {"network": "doge", "kind": "coin", "amount": COIN, "fee_bps": 50, "fee_to": OPERATOR,
             "fee_share_to": LENDER}
    leg = _leg()
    assert NT.fee_terms_fit(leg, stood)
    assert NT.fee_terms_fit(dataclasses.replace(leg, fee_share_to=""), stood), "an older taker drops the field"
    assert not NT.fee_terms_fit(dataclasses.replace(leg, fee_share_to=Key(30).address), stood), \
        "a taker's own address in the lister's lock would pay the taker a fifth of the lister's fee"
    assert not NT.fee_terms_fit(leg, {**stood, "fee_share_to": ""}), "listed with no lender"


def test_an_arcade_on_a_borrowed_chain_names_its_lender(monkeypatch):
    from arcade.config import NETWORKS
    from arcade.mesh import coreshare

    class Share:
        def payee(self, net, valid=lambda a: True):
            return LENDER if valid(LENDER) else None

    class Chain:
        network = "doge"
        params = NETWORKS["doge"]

        def __init__(self, borrowed):
            self.borrowed = borrowed

    class State:
        home = "/tmp/lender-share-test"
        messaging = type("M", (), {"network": "regtest"})()

        def setting(self, key, default=None):
            return 0.5 if key == "trade_cut" else default

        def keyed(self, chain):
            return False

        def home_address(self, chain):
            return OPERATOR

    monkeypatch.setattr(coreshare, "CURRENT", Share())
    NT._payees.clear()
    assert NT.lock_fee(State(), Chain(True)) == {"bps": 50, "to": OPERATOR, "share_to": LENDER}
    NT._payees.clear()
    assert NT.lock_fee(State(), Chain(False)) == {"bps": 50, "to": OPERATOR}, "a chain run here"
    NT._payees.clear()
    assert T.with_fee(_leg(share=""), {"bps": 50, "to": OPERATOR, "share_to": LENDER}).fee_share_to == LENDER
    assert T.with_fee(_leg(share=""), {"bps": 50, "to": OPERATOR, "share_to": "junk"}).fee_share_to == ""
