import os, sys
import pytest
from arcade.lora import device_keys as K

ABANDON = "abandon " * 11 + "about"

def test_bip39_trezor_vector():
    # BIP39 reference vector: "abandon ... about" with passphrase TREZOR
    assert K.seed(ABANDON, "TREZOR").hex().startswith("c55257c360c07c72029aebc1b53c05ed0362ada38ead3e3e9efa3708e5349553")

def test_bip32_vector1():
    # BIP32 test vector 1, chain m/0'/1/2'/2/1000000000
    seed = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    k = K.bip32(seed, (0 | K.H, 1, 2 | K.H, 2, 1000000000))
    assert k.hex() == "471b76e389e528d6de6d816857e012c5455051cad6660850e58372a6c3e6e7c8"

def test_bip44_address_vector():
    # well-known: abandon..about m/44'/0'/0'/0/0 -> 1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA
    s = K.seed(ABANDON)
    pub = __import__("coincurve").PrivateKey(K.bip32(s, (44 | K.H, 0 | K.H, 0 | K.H, 0, 0))).public_key.format(True)
    assert K.b58check(0, K.hash160(pub)) == "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"

ARCADE = os.path.expanduser("~/arcade-localtest")

@pytest.mark.skipif(not os.path.isdir(ARCADE), reason="no arcade checkout")
def test_login_and_mail_keys_match_arcade():
    sys.path.insert(0, ARCADE)
    from arcade import seed as S
    phrase = ABANDON
    a = K.Account(phrase)
    assert a.login_pubkey.hex() == S.login_pubkey(phrase)
    assert a.mail_secret == S.messaging_key(phrase)
