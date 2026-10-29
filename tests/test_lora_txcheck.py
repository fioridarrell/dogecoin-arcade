import pytest
from arcade.lora import txcheck as T
from arcade.lora import device_keys as K

acct = K.Account("abandon " * 11 + "about")
NET, VER, P2SH = "test", 113, 196

def make_offer(pay_to_script, amount, change, inputs):
    tx = T.Tx(1)
    for txid, vout in inputs:
        tx.ins.append(T.TxIn(bytes.fromhex(txid), vout, b"", 0xFFFFFFFF))
    tx.outs = [T.TxOut(amount, pay_to_script), T.TxOut(change, T.p2pkh(K.hash160(acct.coin_pubkey(NET))))]
    return tx.serialize()

INS = [("11" * 32, 0), ("22" * 32, 3)]
PAYEE = T.p2pkh(b"\x05" * 20)

def test_good_offer_digests_match_arcade():
    raw = make_offer(PAYEE, 5000, 1000, INS)
    c = T.check_offer(raw, [4000, 3000], acct.coin_pubkey(NET), pay_script=PAYEE, amount=5000, fee_cap=1500)
    assert c.fee == 1000 and c.change == 1000
    # This repository's own arcade.funding. It used to put ~/arcade-localtest (an old checkout) first on sys.path and
    # leave it there, so every worker process spawned later in the run imported that arcade (2026-10-29; and
    # test_lora_keys).
    from arcade import funding
    own = T.p2pkh(K.hash160(acct.coin_pubkey(NET)))
    outs = [(5000, PAYEE), (1000, own)]
    for i in range(2):
        ins = [{"txid": t, "vout": v} for t, v in INS]
        assert funding.sighash(ins, outs, i, own) == c.digests[i]

def test_wrong_amount_refused():
    raw = make_offer(PAYEE, 6000, 0, INS)
    with pytest.raises(T.OfferRefused):
        T.check_offer(raw, [4000, 3000], acct.coin_pubkey(NET), pay_script=PAYEE, amount=5000, fee_cap=1500)

def test_stranger_output_refused():
    raw = make_offer(T.p2pkh(b"\x09" * 20), 5000, 1000, INS)
    with pytest.raises(T.OfferRefused):
        T.check_offer(raw, [4000, 3000], acct.coin_pubkey(NET), pay_script=PAYEE, amount=5000, fee_cap=1500)

def test_fee_cap():
    raw = make_offer(PAYEE, 5000, 0, INS)
    with pytest.raises(T.OfferRefused):
        T.check_offer(raw, [4000, 3000], acct.coin_pubkey(NET), pay_script=PAYEE, amount=5000, fee_cap=1500)

def test_signature_verifies_and_assembles():
    raw = make_offer(PAYEE, 5000, 1000, INS)
    c = T.check_offer(raw, [4000, 3000], acct.coin_pubkey(NET), pay_script=PAYEE, amount=5000, fee_cap=1500)
    sigs = [acct.sign_digest(NET, d) for d in c.digests]
    import coincurve
    pub = coincurve.PublicKey(acct.coin_pubkey(NET))
    for s, d in zip(sigs, c.digests):
        assert s[-1] == 1 and pub.verify(s[:-1], d, hasher=None)
    signed = T.assemble(raw, sigs, acct.coin_pubkey(NET))
    assert T.Tx.parse(signed).ins[0].script.endswith(acct.coin_pubkey(NET))

def test_address_script():
    addr = acct.address(NET, VER)
    assert T.script_for(addr, VER, P2SH) == T.p2pkh(K.hash160(acct.coin_pubkey(NET)))
