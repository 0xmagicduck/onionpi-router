import hashlib
import hmac

from onionpi.tor_control import TorController


def test_parse_built_circuit() -> None:
    status = "1 BUILT $AAA~Guard,$BBB~Middle,$CCC~Exit PURPOSE=GENERAL\n2 LAUNCHED"
    assert TorController._circuit_nodes(status) == [
        {"role": "Entrée", "name": "Guard"},
        {"role": "Relais", "name": "Middle"},
        {"role": "Sortie", "name": "Exit"},
    ]


def test_safecookie_hashes_follow_tor_spec() -> None:
    cookie = bytes(range(32))
    client_nonce = bytes(range(32, 64))
    server_nonce = bytes(range(64, 96))
    server_hash, client_hash = TorController._safe_cookie_hashes(
        cookie, client_nonce, server_nonce
    )
    material = cookie + client_nonce + server_nonce
    assert server_hash == hmac.new(
        b"Tor safe cookie authentication server-to-controller hash",
        material,
        hashlib.sha256,
    ).digest()
    assert client_hash == hmac.new(
        b"Tor safe cookie authentication controller-to-server hash",
        material,
        hashlib.sha256,
    ).digest()
