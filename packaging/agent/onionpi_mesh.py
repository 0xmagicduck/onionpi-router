#!/usr/bin/env python3
"""Cryptographie et protocole du maillage OnionPi, côté nœud.

Ce module ne dépend que de la bibliothèque standard. Il tient trois choses:

* **L'identité d'un nœud.** Une clé Ed25519 générée sur la machine et qui n'en
  sort jamais, plus une clé statique X25519 signée par elle. L'adresse du nœud
  sur le maillage est dérivée de la clé d'identité: personne ne peut prendre
  l'adresse d'un autre sans une préimage de SHA-256.
* **La carte du réseau.** Un document signé par le coordinateur, refusé si son
  numéro de série n'augmente pas, s'il est périmé, ou si sa signature n'est pas
  celle de la clé épinglée à l'installation. Sous verrou de maillage, une
  nouvelle clé de pair exige en plus K signatures de garants sur N.
* **Le plan de données.** Une poignée de main Noise_IK_25519_ChaChaPoly_BLAKE2s
  — la construction de WireGuard — au-dessus d'un flux TCP, que ce flux vienne
  d'un circuit Tor ou du lien radio `bat0`. Le transport change, la sécurité
  non.

Pourquoi chiffrer par-dessus Tor, qui chiffre déjà: un service onion authentifie
le *service* joint, Noise authentifie l'*identité* du pair. Une adresse onion
recopiée de travers dans une fiche donne alors « aucun pair », pas « un mauvais
pair ».

Les primitives sont écrites ici en Python pur pour rester lisibles et
vérifiables sans dépendance. Elles sont validées par les vecteurs des RFC 8032,
7748 et 8439 dans `backend/tests/test_mesh_crypto.py`. Le débit d'un chiffrement
en Python pur plafonne à quelques mégaoctets par seconde: c'est au-dessus de ce
qu'un circuit Tor transporte, en dessous de ce que `bat0` pourrait porter, et
c'est assumé — ce maillage sert l'administration, pas la vidéo. Quand la
bibliothèque `cryptography` est présente, elle prend le relais pour le seul
chiffrement de flux, avec les mêmes vecteurs de test des deux côtés.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import struct
from typing import Any

# --------------------------------------------------------------- Ed25519 ---
# RFC 8032. Écrit à partir de la description de la RFC plutôt qu'importé: le
# nœud n'a que la bibliothèque standard, et une signature de carte réseau est
# ce qui décide quels pairs existent.

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)
_BY = 4 * pow(5, _P - 2, _P) % _P


def _recover_x(y: int, sign: int) -> int | None:
    """L'abscisse d'un point de la courbe d'ordonnée `y`, ou None."""
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if x & 1 != sign:
        x = _P - x
    return x


_BX = _recover_x(_BY, 0) or 0
#: Point de base, en coordonnées projetées étendues (X, Y, Z, T).
_B = (_BX, _BY, 1, _BX * _BY % _P)
_ZERO = (0, 1, 1, 0)


def _point_add(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _point_mul(scalar: int, point: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    result = _ZERO
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, point)
        point = _point_add(point, point)
        scalar >>= 1
    return result


def _point_equal(p: tuple[int, int, int, int], q: tuple[int, int, int, int]) -> bool:
    if (p[0] * q[2] - q[0] * p[2]) % _P != 0:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _point_compress(point: tuple[int, int, int, int]) -> bytes:
    inverse = pow(point[2], _P - 2, _P)
    x = point[0] * inverse % _P
    y = point[1] * inverse % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _point_decompress(raw: bytes) -> tuple[int, int, int, int] | None:
    if len(raw) != 32:
        return None
    value = int.from_bytes(raw, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _ed25519_secret(seed: bytes) -> tuple[int, bytes]:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def ed25519_public(seed: bytes) -> bytes:
    """Clé publique Ed25519 des 32 octets de graine."""
    if len(seed) != 32:
        raise ValueError("Graine Ed25519 invalide")
    scalar, _ = _ed25519_secret(seed)
    return _point_compress(_point_mul(scalar, _B))


def ed25519_sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _ed25519_secret(seed)
    public = _point_compress(_point_mul(scalar, _B))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    big_r = _point_compress(_point_mul(r, _B))
    challenge = (
        int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % _L
    )
    return big_r + int.to_bytes((r + challenge * scalar) % _L, 32, "little")


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    point = _point_decompress(public)
    if point is None:
        return False
    big_r = _point_decompress(signature[:32])
    if big_r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    challenge = (
        int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little")
        % _L
    )
    # Vérification par cofacteur omise: on compare [s]B et R + [k]A directement,
    # ce que fait la vérification stricte de la RFC 8032 §5.1.7.
    return _point_equal(_point_mul(s, _B), _point_add(big_r, _point_mul(challenge, point)))


# ---------------------------------------------------------------- X25519 ---
# RFC 7748 §5, échelle de Montgomery.

_A24 = 121665


def x25519(scalar: bytes, point: bytes) -> bytes:
    if len(scalar) != 32 or len(point) != 32:
        raise ValueError("Clé X25519 invalide")
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    key = int.from_bytes(bytes(k), "little")
    u = int.from_bytes(point, "little") & ((1 << 255) - 1)
    x1, x2, z2, x3, z3, swap = u, 1, 0, u, 1, 0
    for index in range(254, -1, -1):
        bit = (key >> index) & 1
        swap ^= bit
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = bit
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) % _P
        x3 = x3 * x3 % _P
        z3 = (da - cb) % _P
        z3 = x1 * z3 % _P * z3 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return int.to_bytes(x2 * pow(z2, _P - 2, _P) % _P, 32, "little")


_X25519_BASE = b"\x09" + b"\x00" * 31


def x25519_public(private: bytes) -> bytes:
    return x25519(private, _X25519_BASE)


# ----------------------------------------------------- ChaCha20-Poly1305 ---
# RFC 8439.


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = [
        0x61707865,
        0x3320646E,
        0x79622D32,
        0x6B206574,
        *struct.unpack("<8I", key),
        counter,
        *struct.unpack("<3I", nonce),
    ]
    working = list(state)

    def quarter(a: int, b: int, c: int, d: int) -> None:
        working[a] = (working[a] + working[b]) & 0xFFFFFFFF
        working[d] ^= working[a]
        working[d] = ((working[d] << 16) | (working[d] >> 16)) & 0xFFFFFFFF
        working[c] = (working[c] + working[d]) & 0xFFFFFFFF
        working[b] ^= working[c]
        working[b] = ((working[b] << 12) | (working[b] >> 20)) & 0xFFFFFFFF
        working[a] = (working[a] + working[b]) & 0xFFFFFFFF
        working[d] ^= working[a]
        working[d] = ((working[d] << 8) | (working[d] >> 24)) & 0xFFFFFFFF
        working[c] = (working[c] + working[d]) & 0xFFFFFFFF
        working[b] ^= working[c]
        working[b] = ((working[b] << 7) | (working[b] >> 25)) & 0xFFFFFFFF

    for _ in range(10):
        quarter(0, 4, 8, 12)
        quarter(1, 5, 9, 13)
        quarter(2, 6, 10, 14)
        quarter(3, 7, 11, 15)
        quarter(0, 5, 10, 15)
        quarter(1, 6, 11, 12)
        quarter(2, 7, 8, 13)
        quarter(3, 4, 9, 14)
    return struct.pack(
        "<16I", *((working[index] + state[index]) & 0xFFFFFFFF for index in range(16))
    )


def _chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    out = bytearray(len(data))
    for offset in range(0, len(data), 64):
        stream = _chacha20_block(key, counter + offset // 64, nonce)
        chunk = data[offset : offset + 64]
        for index, byte in enumerate(chunk):
            out[offset + index] = byte ^ stream[index]
    return bytes(out)


def _poly1305(key: bytes, message: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:], "little")
    prime = (1 << 130) - 5
    accumulator = 0
    for offset in range(0, len(message), 16):
        block = message[offset : offset + 16]
        accumulator += int.from_bytes(block + b"\x01", "little")
        accumulator = accumulator * r % prime
    return int.to_bytes((accumulator + s) & ((1 << 128) - 1), 16, "little")


def _pad16(data: bytes) -> bytes:
    remainder = len(data) % 16
    return b"" if remainder == 0 else b"\x00" * (16 - remainder)


def _aead_encrypt_pure(key: bytes, nonce: bytes, plaintext: bytes, associated: bytes) -> bytes:
    otk = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = _chacha20(key, 1, nonce, plaintext)
    mac_data = (
        associated
        + _pad16(associated)
        + ciphertext
        + _pad16(ciphertext)
        + struct.pack("<QQ", len(associated), len(ciphertext))
    )
    return ciphertext + _poly1305(otk, mac_data)


def _aead_decrypt_pure(key: bytes, nonce: bytes, ciphertext: bytes, associated: bytes) -> bytes:
    if len(ciphertext) < 16:
        raise ValueError("Bloc chiffré tronqué")
    body, tag = ciphertext[:-16], ciphertext[-16:]
    otk = _chacha20_block(key, 0, nonce)[:32]
    mac_data = (
        associated
        + _pad16(associated)
        + body
        + _pad16(body)
        + struct.pack("<QQ", len(associated), len(body))
    )
    if not hmac.compare_digest(_poly1305(otk, mac_data), tag):
        raise ValueError("Authentification du bloc refusée")
    return _chacha20(key, 1, nonce, body)


try:  # pragma: no cover - dépend de la machine du nœud
    from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305 as _FastAEAD
except ImportError:  # pragma: no cover
    _FastAEAD = None


def aead_encrypt(key: bytes, nonce: bytes, plaintext: bytes, associated: bytes) -> bytes:
    if _FastAEAD is not None:
        return _FastAEAD(key).encrypt(nonce, plaintext, associated or None)
    return _aead_encrypt_pure(key, nonce, plaintext, associated)


def aead_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, associated: bytes) -> bytes:
    if _FastAEAD is not None:
        try:
            return _FastAEAD(key).decrypt(nonce, ciphertext, associated or None)
        except Exception as error:  # la bibliothèque lève InvalidTag
            raise ValueError("Authentification du bloc refusée") from error
    return _aead_decrypt_pure(key, nonce, ciphertext, associated)


# ------------------------------------------------------------- Noise IK ----

PROTOCOL_NAME = b"Noise_IK_25519_ChaChaPoly_BLAKE2s"
#: Taille maximale d'un message de transport, en-tête de longueur exclu. Une
#: borne, pas un réglage: sans elle, un pair annonce 4 Gio et le nœud alloue.
MAX_MESSAGE = 65535
TAG_BYTES = 16


def _hash(data: bytes) -> bytes:
    return hashlib.blake2s(data).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.blake2s).digest()


def _hkdf(chaining: bytes, material: bytes, outputs: int) -> tuple[bytes, ...]:
    temporary = _hmac(chaining, material)
    first = _hmac(temporary, b"\x01")
    second = _hmac(temporary, first + b"\x02")
    if outputs == 2:
        return (first, second)
    return (first, second, _hmac(temporary, second + b"\x03"))


class CipherState:
    """Un sens de transport: une clé, un compteur qui n'est jamais réutilisé."""

    def __init__(self, key: bytes) -> None:
        self.key = key
        self.nonce = 0

    def _nonce_bytes(self) -> bytes:
        # Noise: 4 octets nuls puis le compteur sur 64 bits en petit-boutiste.
        return b"\x00\x00\x00\x00" + struct.pack("<Q", self.nonce)

    def encrypt(self, plaintext: bytes, associated: bytes = b"") -> bytes:
        if self.nonce >= 2**64 - 1:
            raise ValueError("Compteur de session épuisé")
        out = aead_encrypt(self.key, self._nonce_bytes(), plaintext, associated)
        self.nonce += 1
        return out

    def decrypt(self, ciphertext: bytes, associated: bytes = b"") -> bytes:
        if self.nonce >= 2**64 - 1:
            raise ValueError("Compteur de session épuisé")
        out = aead_decrypt(self.key, self._nonce_bytes(), ciphertext, associated)
        self.nonce += 1
        return out


class HandshakeState:
    """La moitié Noise_IK d'un pair. Une instance par connexion, jamais deux."""

    def __init__(self, static_private: bytes, prologue: bytes = b"") -> None:
        self.s = static_private
        self.s_public = x25519_public(static_private)
        self.e = b""
        self.e_public = b""
        self.rs = b""
        self.re = b""
        # Noise: le nom du protocole tient lieu de `h` quand il fait au plus
        # HASHLEN octets, sinon c'est son empreinte. Celui-ci en fait 33.
        self.h = (
            PROTOCOL_NAME.ljust(32, b"\x00")
            if len(PROTOCOL_NAME) <= 32
            else _hash(PROTOCOL_NAME)
        )
        self.ck = self.h
        self.key = b""
        self.nonce = 0
        self._mix_hash(prologue)

    # -- primitives d'état symétrique --

    def _mix_hash(self, data: bytes) -> None:
        self.h = _hash(self.h + data)

    def _mix_key(self, material: bytes) -> None:
        self.ck, self.key = _hkdf(self.ck, material, 2)
        self.nonce = 0

    def _encrypt_and_hash(self, plaintext: bytes) -> bytes:
        if not self.key:
            self._mix_hash(plaintext)
            return plaintext
        nonce = b"\x00\x00\x00\x00" + struct.pack("<Q", self.nonce)
        out = aead_encrypt(self.key, nonce, plaintext, self.h)
        self.nonce += 1
        self._mix_hash(out)
        return out

    def _decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        if not self.key:
            self._mix_hash(ciphertext)
            return ciphertext
        nonce = b"\x00\x00\x00\x00" + struct.pack("<Q", self.nonce)
        out = aead_decrypt(self.key, nonce, ciphertext, self.h)
        self.nonce += 1
        self._mix_hash(ciphertext)
        return out

    def _split(self, initiator: bool) -> tuple[CipherState, CipherState]:
        first, second = _hkdf(self.ck, b"", 2)
        if initiator:
            return CipherState(first), CipherState(second)
        return CipherState(second), CipherState(first)

    # -- messages --

    def write_message_one(self, remote_static: bytes, payload: bytes = b"") -> bytes:
        """Initiateur: `e, es, s, ss` puis la charge utile chiffrée."""
        if len(remote_static) != 32:
            raise ValueError("Clé statique du pair invalide")
        self.rs = remote_static
        self._mix_hash(self.rs)
        self.e = secrets.token_bytes(32)
        self.e_public = x25519_public(self.e)
        self._mix_hash(self.e_public)
        self._mix_key(x25519(self.e, self.rs))
        encrypted_static = self._encrypt_and_hash(self.s_public)
        self._mix_key(x25519(self.s, self.rs))
        return self.e_public + encrypted_static + self._encrypt_and_hash(payload)

    def read_message_one(self, message: bytes) -> bytes:
        """Répondeur: lit `e, es, s, ss`. Renseigne `self.rs`."""
        if len(message) < 32 + 32 + TAG_BYTES + TAG_BYTES:
            raise ValueError("Premier message trop court")
        self._mix_hash(self.s_public)
        self.re = message[:32]
        self._mix_hash(self.re)
        self._mix_key(x25519(self.s, self.re))
        offset = 32 + 32 + TAG_BYTES
        self.rs = self._decrypt_and_hash(message[32:offset])
        if len(self.rs) != 32:
            raise ValueError("Clé statique du pair invalide")
        self._mix_key(x25519(self.s, self.rs))
        return self._decrypt_and_hash(message[offset:])

    def write_message_two(self, payload: bytes = b"") -> tuple[bytes, CipherState, CipherState]:
        """Répondeur: `e, ee, se`, puis les deux sens de transport."""
        self.e = secrets.token_bytes(32)
        self.e_public = x25519_public(self.e)
        self._mix_hash(self.e_public)
        self._mix_key(x25519(self.e, self.re))
        self._mix_key(x25519(self.e, self.rs))
        message = self.e_public + self._encrypt_and_hash(payload)
        send, receive = self._split(initiator=False)
        return message, send, receive

    def read_message_two(self, message: bytes) -> tuple[bytes, CipherState, CipherState]:
        if len(message) < 32 + TAG_BYTES:
            raise ValueError("Second message trop court")
        self.re = message[:32]
        self._mix_hash(self.re)
        self._mix_key(x25519(self.e, self.re))
        self._mix_key(x25519(self.s, self.re))
        payload = self._decrypt_and_hash(message[32:])
        send, receive = self._split(initiator=True)
        return payload, send, receive


# ------------------------------------------------------------- transport ---


def frame(payload: bytes) -> bytes:
    if len(payload) > MAX_MESSAGE:
        raise ValueError("Message trop long")
    return struct.pack(">H", len(payload)) + payload


def read_exactly(stream: Any, count: int) -> bytes:
    """`count` octets d'un socket, ou une erreur. Jamais un tampon partiel."""
    chunks: list[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = stream.recv(remaining)
        if not chunk:
            raise ConnectionError("Flux interrompu")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_frame(stream: Any) -> bytes:
    (length,) = struct.unpack(">H", read_exactly(stream, 2))
    return read_exactly(stream, length) if length else b""


# -------------------------------------------------------------- identité ---

IDENTITY_PREFIX = "ed25519:"
STATIC_PREFIX = "x25519:"
#: Le domaine que la clé d'identité signe pour lier une clé statique à elle.
#: Sans lui, une signature d'un contexte servirait dans un autre.
STATIC_BINDING = b"onionpi-mesh/1/static"
#: Préfixe du plan d'adressage. `fd7a::/32` est un ULA (RFC 4193), donc jamais
#: routé sur Internet, et les 96 bits restants viennent de la clé: il n'y a ni
#: attribution, ni collision à corriger à la main.
ADDRESS_PREFIX = bytes.fromhex("fd7a0000")


def encode_key(prefix: str, raw: bytes) -> str:
    return prefix + raw.hex()


def decode_key(prefix: str, value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise ValueError("Clé mal formée")
    raw = value[len(prefix) :]
    if len(raw) != 64:
        raise ValueError("Clé mal formée")
    try:
        return bytes.fromhex(raw)
    except ValueError as error:
        raise ValueError("Clé mal formée") from error


def mesh_address(identity_public: bytes) -> str:
    """L'adresse IPv6 d'un nœud, déduite de sa clé d'identité.

    `fd7a:0000:` puis les 96 bits de poids faible de SHA-256(clé). Router vers
    une adresse revient donc à authentifier la clé qui la produit, et un
    opérateur qui lit une adresse dans un journal sait de quelle clé il parle
    sans annuaire.
    """
    digest = hashlib.sha256(identity_public).digest()
    raw = ADDRESS_PREFIX + digest[-12:]
    groups = [raw[index : index + 2].hex() for index in range(0, 16, 2)]
    return _compress_v6(groups)


def _compress_v6(groups: list[str]) -> str:
    """Écriture canonique RFC 5952 d'une adresse donnée en huit groupes."""
    trimmed = [group.lstrip("0") or "0" for group in groups]
    best_start, best_length, start, length = -1, 0, -1, 0
    for index, group in enumerate([*trimmed, "x"]):
        if group == "0":
            start = index if start < 0 else start
            length += 1
            continue
        if length > best_length:
            best_start, best_length = start, length
        start, length = -1, 0
    if best_length < 2:
        return ":".join(trimmed)
    head = ":".join(trimmed[:best_start])
    tail = ":".join(trimmed[best_start + best_length :])
    return f"{head}::{tail}"


class Identity:
    """Les clés d'un nœud. Générées ici, jamais transmises."""

    def __init__(self, identity_seed: bytes, static_private: bytes) -> None:
        self.identity_seed = identity_seed
        self.static_private = static_private
        self.identity_public = ed25519_public(identity_seed)
        self.static_public = x25519_public(static_private)

    @classmethod
    def generate(cls) -> Identity:
        return cls(secrets.token_bytes(32), secrets.token_bytes(32))

    @classmethod
    def load(cls, path: Any) -> Identity:
        """Lit le fichier d'identité, le crée à la première exécution.

        Le fichier est en 0600 et n'est jamais envoyé nulle part: ce qui remonte
        au coordinateur, ce sont les moitiés publiques. La baie autorise un
        nœud, elle ne peut pas l'être.
        """
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            identity = cls(
                bytes.fromhex(str(document["identity_seed"])),
                bytes.fromhex(str(document["static_private"])),
            )
        except (OSError, ValueError, KeyError, TypeError):
            identity = cls.generate()
            identity.save(path)
        return identity

    def save(self, path: Any) -> None:
        document = {
            "identity_seed": self.identity_seed.hex(),
            "static_private": self.static_private.hex(),
        }
        temporary = path.with_suffix(".tmp")
        # Ouvert en 0600 dès la création: écrire puis chmod laisse une fenêtre
        # pendant laquelle la clé privée est lisible par la machine entière.
        handle = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(document))
        os.replace(temporary, path)

    @property
    def address(self) -> str:
        return mesh_address(self.identity_public)

    def static_signature(self) -> bytes:
        """La signature qui lie la clé statique à l'identité longue durée."""
        return ed25519_sign(
            self.identity_seed, STATIC_BINDING + b"\n" + self.static_public
        )

    def announcement(self) -> dict[str, str]:
        """Les moitiés publiques, telles qu'elles remontent au coordinateur."""
        return {
            "identity": encode_key(IDENTITY_PREFIX, self.identity_public),
            "static": encode_key(STATIC_PREFIX, self.static_public),
            "static_signature": self.static_signature().hex(),
            "address": self.address,
        }


def verify_static_binding(identity: str, static: str, signature: str) -> bool:
    """La clé statique annoncée est-elle bien signée par l'identité annoncée."""
    try:
        identity_raw = decode_key(IDENTITY_PREFIX, identity)
        static_raw = decode_key(STATIC_PREFIX, static)
        signature_raw = bytes.fromhex(signature)
    except ValueError:
        return False
    return ed25519_verify(identity_raw, STATIC_BINDING + b"\n" + static_raw, signature_raw)


# ----------------------------------------------------------------- carte ---

NETMAP_VERSION = 1
#: Le domaine signé par le coordinateur. Une carte ne peut donc pas servir de
#: caution à un autre document du protocole, ni l'inverse.
NETMAP_BINDING = b"onionpi-mesh/1/netmap"
#: Le domaine signé par un garant sous verrou de maillage.
ENDORSEMENT_BINDING = b"onionpi-mesh/1/endorse"


def canonical(document: dict[str, Any]) -> bytes:
    """La forme exacte que les deux moitiés signent et vérifient."""
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def netmap_message(document: dict[str, Any]) -> bytes:
    body = {key: value for key, value in document.items() if key != "signature"}
    return NETMAP_BINDING + b"\n" + canonical(body)


def sign_netmap(seed: bytes, document: dict[str, Any]) -> dict[str, Any]:
    signed = {key: value for key, value in document.items() if key != "signature"}
    signed["signature"] = ed25519_sign(seed, netmap_message(signed)).hex()
    return signed


def endorsement_message(node_id: str, identity: str) -> bytes:
    return ENDORSEMENT_BINDING + b"\n" + f"{node_id}\n{identity}".encode()


def sign_endorsement(seed: bytes, node_id: str, identity: str) -> str:
    return ed25519_sign(seed, endorsement_message(node_id, identity)).hex()


class MeshLock:
    """Verrou de maillage K-sur-N: qui a le droit d'introduire une clé.

    Sans lui, un coordinateur compromis inscrit le pair de son choix dans la
    carte. Avec lui, une clé de pair *nouvelle* n'est acceptée que contresignée
    par K garants sur N. Le coût est réel — ajouter une machine demande K
    opérateurs — donc c'est un choix, jamais un défaut.
    """

    def __init__(self, threshold: int, trustees: list[str]) -> None:
        if threshold < 1 or threshold > len(trustees):
            raise ValueError("Seuil de verrou incohérent")
        self.threshold = threshold
        self.trustees = list(trustees)

    @classmethod
    def load(cls, path: Any) -> MeshLock | None:
        """Le verrou épinglé à l'installation, ou None s'il n'y en a pas.

        Lu depuis un fichier que le coordinateur n'écrit pas: un verrou que la
        carte pourrait remplacer ne verrouillerait rien.
        """
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        try:
            trustees = [str(value) for value in document["trustees"]]
            for trustee in trustees:
                decode_key(IDENTITY_PREFIX, trustee)
            return cls(int(document["threshold"]), trustees)
        except (KeyError, TypeError, ValueError):
            return None

    def accepts(self, node_id: str, identity: str, endorsements: Any) -> bool:
        if not isinstance(endorsements, dict):
            return False
        message = endorsement_message(node_id, identity)
        seen: set[str] = set()
        for trustee, signature in endorsements.items():
            if trustee not in self.trustees or trustee in seen:
                continue
            try:
                public = decode_key(IDENTITY_PREFIX, trustee)
                raw = bytes.fromhex(str(signature))
            except ValueError:
                continue
            if ed25519_verify(public, message, raw):
                seen.add(trustee)
        return len(seen) >= self.threshold


class NetmapError(ValueError):
    """La carte reçue n'est pas acceptable, et rien n'en est retenu."""


def verify_netmap(
    document: Any,
    coordinator_public: bytes,
    *,
    last_serial: int,
    now: int,
    lock: MeshLock | None = None,
    known_identities: dict[str, str] | None = None,
) -> dict[str, Any]:
    """La carte, si elle est acceptable. Sinon `NetmapError`, et rien n'est retenu.

    Quatre refus, chacun pour une raison qui ne se rattrape pas plus tard:
    une signature inconnue, un `serial` qui n'augmente pas (sinon un rejeu
    réinstalle un pair révoqué, et une révocation qui se rejoue n'en est pas
    une), une carte périmée (ce qui borne ce qu'un coordinateur muet ou saisi
    laisse tourner), et sous verrou une clé de pair sans ses K signatures.
    """
    if not isinstance(document, dict):
        raise NetmapError("Carte illisible")
    signature = document.get("signature", "")
    try:
        raw_signature = bytes.fromhex(str(signature))
    except ValueError as error:
        raise NetmapError("Signature de carte illisible") from error
    if not ed25519_verify(coordinator_public, netmap_message(document), raw_signature):
        raise NetmapError("Carte non signée par le coordinateur épinglé")
    if int(document.get("version", 0)) != NETMAP_VERSION:
        raise NetmapError("Version de carte inconnue")
    serial = int(document.get("serial", 0))
    if serial <= last_serial:
        raise NetmapError(f"Carte rejouée (série {serial} ≤ {last_serial})")
    not_after = int(document.get("not_after", 0))
    if not_after <= now:
        raise NetmapError("Carte périmée")
    peers = document.get("peers", [])
    if not isinstance(peers, list):
        raise NetmapError("Liste de pairs illisible")
    known = known_identities or {}
    checked: list[dict[str, Any]] = []
    for peer in peers:
        if not isinstance(peer, dict):
            raise NetmapError("Pair illisible")
        node = str(peer.get("node", ""))
        identity = str(peer.get("identity", ""))
        static = str(peer.get("static", ""))
        if not verify_static_binding(identity, static, str(peer.get("static_signature", ""))):
            # La carte dit qui est autorisé; c'est le nœud lui-même qui lie sa
            # clé de chiffrement à son identité. Sans cette signature, le
            # coordinateur choisirait avec quoi on chiffre pour un pair.
            raise NetmapError(f"Clé statique non liée à l'identité du pair {node}")
        if lock is not None and known.get(node) != identity:
            if not lock.accepts(node, identity, peer.get("endorsements")):
                raise NetmapError(
                    f"Verrou de maillage: clé du pair {node} sans les signatures requises"
                )
        checked.append(peer)
    return {**document, "peers": checked}
