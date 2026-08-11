from onionpi.auth import hash_password, token_hash, verify_password


def test_password_hash_round_trip() -> None:
    encoded = hash_password("une-phrase-secrete-solide")
    assert encoded.startswith("scrypt$")
    assert verify_password("une-phrase-secrete-solide", encoded)
    assert not verify_password("mauvaise-phrase-secrete", encoded)


def test_token_hash_is_keyed() -> None:
    assert token_hash("token", "secret-a") != token_hash("token", "secret-b")

