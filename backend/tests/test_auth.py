import base64

from onionpi.auth import (
    LoginLimiter,
    hash_password,
    token_hash,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    encoded = hash_password("une-phrase-secrete-solide")
    assert encoded.startswith("scrypt$")
    assert verify_password("une-phrase-secrete-solide", encoded)
    assert not verify_password("mauvaise-phrase-secrete", encoded)


def test_token_hash_is_keyed() -> None:
    assert token_hash("token", "secret-a") != token_hash("token", "secret-b")


def test_downgraded_scrypt_parameters_are_refused() -> None:
    """A digest rewritten with a cheap cost must not verify at that cost."""
    salt = base64.urlsafe_b64encode(b"0123456789abcdef").decode()
    digest = base64.urlsafe_b64encode(b"\x00" * 32).decode()
    assert not verify_password("peu-importe", f"scrypt$2$1$1${salt}${digest}")
    # And an absurd cost is refused instead of being attempted.
    assert not verify_password("peu-importe", f"scrypt$1073741824$8$1${salt}${digest}")


def test_login_limiter_caps_attempts_per_address() -> None:
    limiter = LoginLimiter(attempts=3, window_seconds=300)
    for _ in range(3):
        assert limiter.allow("10.42.0.5")
        limiter.failure("10.42.0.5")
    assert not limiter.allow("10.42.0.5")
    assert limiter.allow("10.42.0.6")
    limiter.success("10.42.0.5")
    assert limiter.allow("10.42.0.5")


def test_login_limiter_global_ceiling_survives_forged_addresses() -> None:
    """A caller that invents a new address per attempt still runs out.

    Requests that do not pass through nginx carry whatever forwarded address
    their author chose, so the per-address budget alone would be free to reset.
    """
    limiter = LoginLimiter(attempts=3, window_seconds=300, global_attempts=10)
    for index in range(10):
        address = f"10.42.0.{index}"
        assert limiter.allow(address)
        limiter.failure(address)
    assert not limiter.allow("10.42.0.99")

