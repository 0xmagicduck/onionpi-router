from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import socket
import time
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar

import httpx


class TorControlError(RuntimeError):
    pass


class TorController:
    SAFECOOKIE_SERVER_KEY = b"Tor safe cookie authentication server-to-controller hash"
    SAFECOOKIE_CLIENT_KEY = b"Tor safe cookie authentication controller-to-server hash"

    def __init__(self, host: str, port: int, cookie_path: Path, demo_mode: bool = False) -> None:
        self.host = host
        self.port = port
        self.cookie_path = cookie_path
        self.demo_mode = demo_mode
        self._exit_cache: tuple[float, str | None] = (0, None)
        self._lock = Lock()

    def _command(self, command: str) -> list[str]:
        if "\r" in command or "\n" in command:
            raise TorControlError("Commande de contrôle invalide")
        try:
            cookie = self.cookie_path.read_bytes()
        except OSError as error:
            raise TorControlError("Cookie de contrôle Tor inaccessible") from error
        if len(cookie) != 32:
            raise TorControlError("Cookie de contrôle Tor invalide")

        lines: list[str] = []
        with socket.create_connection((self.host, self.port), timeout=3) as connection:
            connection.settimeout(3)
            stream = connection.makefile("rwb", buffering=0)
            self._authenticate_safecookie(stream, cookie)
            stream.write((command + "\r\n").encode())
            response = self._read_response(stream)
            if not response or not response[-1].startswith("250"):
                detail = response[-1] if response else "aucune réponse"
                raise TorControlError(f"Tor a refusé la commande: {detail}")
            lines = response
            try:
                stream.write(b"QUIT\r\n")
            except OSError:
                pass
        return lines

    @classmethod
    def _safe_cookie_hashes(
        cls, cookie: bytes, client_nonce: bytes, server_nonce: bytes
    ) -> tuple[bytes, bytes]:
        material = cookie + client_nonce + server_nonce
        server_hash = hmac.new(cls.SAFECOOKIE_SERVER_KEY, material, hashlib.sha256).digest()
        client_hash = hmac.new(cls.SAFECOOKIE_CLIENT_KEY, material, hashlib.sha256).digest()
        return server_hash, client_hash

    def _authenticate_safecookie(self, stream: Any, cookie: bytes) -> None:
        client_nonce = secrets.token_bytes(32)
        stream.write(f"AUTHCHALLENGE SAFECOOKIE {client_nonce.hex()}\r\n".encode())
        challenge = self._read_response(stream)
        challenge_line = next(
            (line for line in challenge if line.startswith("250 AUTHCHALLENGE ")), ""
        )
        match = re.search(
            r"SERVERHASH=([0-9A-Fa-f]{64})\s+SERVERNONCE=([0-9A-Fa-f]{64})",
            challenge_line,
        )
        if not match:
            detail = challenge[-1] if challenge else "aucune réponse"
            raise TorControlError(f"Authentification SAFECOOKIE refusée: {detail}")
        received_server_hash = bytes.fromhex(match.group(1))
        server_nonce = bytes.fromhex(match.group(2))
        expected_server_hash, client_hash = self._safe_cookie_hashes(
            cookie, client_nonce, server_nonce
        )
        if not hmac.compare_digest(received_server_hash, expected_server_hash):
            raise TorControlError("Réponse SAFECOOKIE de Tor non authentique")
        stream.write(f"AUTHENTICATE {client_hash.hex()}\r\n".encode())
        authenticated = self._read_response(stream)
        if not authenticated or not authenticated[-1].startswith("250"):
            detail = authenticated[-1] if authenticated else "aucune réponse"
            raise TorControlError(f"Authentification Tor refusée: {detail}")

    @staticmethod
    def _read_response(stream: Any) -> list[str]:
        response: list[str] = []
        data_block = False
        while True:
            raw = stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            response.append(line)
            if data_block:
                if line == ".":
                    data_block = False
                continue
            if len(line) >= 4 and line[:3].isdigit() and line[3] == "+":
                data_block = True
                continue
            if re.match(r"^\d{3} ", line):
                break
        return response

    def _conf(self, *keys: str) -> dict[str, list[str]]:
        lines = self._command("GETCONF " + " ".join(keys))
        values: dict[str, list[str]] = {key: [] for key in keys}
        lowered = {key.lower(): key for key in keys}
        for line in lines:
            if not line.startswith(("250-", "250 ")):
                continue
            name, separator, value = line[4:].partition("=")
            key = lowered.get(name.strip().lower())
            # A key with no value is reported as "250 Key": the default applies.
            if key and separator and value:
                values[key].append(value)
        return values

    def _info(self, key: str) -> str:
        lines = self._command(f"GETINFO {key}")
        values: list[str] = []
        prefix = f"250-{key}="
        data_prefix = f"250+{key}="
        collecting = False
        for line in lines:
            if line.startswith(prefix):
                values.append(line[len(prefix) :])
            elif line.startswith(data_prefix):
                collecting = True
                initial = line[len(data_prefix) :]
                if initial:
                    values.append(initial)
            elif collecting and line == ".":
                collecting = False
            elif collecting:
                values.append(line)
        return "\n".join(values)

    @staticmethod
    def _circuit_nodes(circuit_status: str) -> list[dict[str, str]]:
        for line in circuit_status.splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[1] != "BUILT":
                continue
            path = parts[2].split(",")
            labels = ["Entrée", "Relais", "Sortie"]
            nodes: list[dict[str, str]] = []
            for index, relay in enumerate(path[:3]):
                nickname = relay.split("~", 1)[-1].split("=", 1)[-1]
                if nickname.startswith("$"):
                    nickname = nickname[1:9]
                nodes.append({"role": labels[min(index, 2)], "name": nickname or "Relais Tor"})
            if nodes:
                return nodes
        return []

    def _exit_ip(self) -> str | None:
        cached_at, cached_ip = self._exit_cache
        if time.monotonic() - cached_at < 300:
            return cached_ip
        try:
            # socks5h, not socks5: the "h" keeps the name resolution inside Tor.
            # With socks5 httpx resolves check.torproject.org through the system
            # resolver first, which on this appliance is the upstream provider's
            # — a clear-text query that announces a Tor user at this address.
            with httpx.Client(proxy="socks5h://127.0.0.1:9050", timeout=4) as client:
                payload = client.get("https://check.torproject.org/api/ip").json()
                value = str(payload.get("IP", ""))
                if not re.fullmatch(r"[0-9a-fA-F:.]+", value):
                    value = ""
        except Exception:
            value = ""
        self._exit_cache = (time.monotonic(), value or None)
        return value or None

    ADDRESS_PATTERN = re.compile(r"^\[?[0-9A-Fa-f.:]+\]?:\d{1,5}$")

    def bridge_state(self) -> dict[str, Any]:
        """Reports the bridge configuration Tor currently runs with.

        Read from the control port rather than from the generated file, so a
        manual torrc edit or a failed reload cannot make the interface lie.
        """
        if self.demo_mode:
            return {"use_bridges": True, "transport": "snowflake", "bridge_count": 2, "known": True}
        try:
            values = self._conf("UseBridges", "Bridge")
        except TorControlError:
            return {"use_bridges": False, "transport": None, "bridge_count": 0, "known": False}
        enabled = bool(values["UseBridges"]) and values["UseBridges"][0].strip() == "1"
        bridges = values["Bridge"]
        transport: str | None = None
        if bridges:
            head = bridges[0].split()
            if head and not self.ADDRESS_PATTERN.match(head[0]):
                transport = head[0]
        return {
            "use_bridges": enabled,
            "transport": transport,
            "bridge_count": len(bridges),
            "known": True,
        }

    def bootstrap_progress(self) -> int:
        """Cheap bootstrap probe for the censorship watchdog: no exit-IP lookup."""
        if self.demo_mode:
            return 100
        try:
            phase = self._info("status/bootstrap-phase")
        except TorControlError:
            return 0
        match = re.search(r"PROGRESS=(\d+)", phase)
        return int(match.group(1)) if match else 0

    def reload_config(self) -> None:
        """Makes Tor re-read torrc, including the OnionPi bridge file."""
        if self.demo_mode:
            return
        with self._lock:
            self._command("SIGNAL RELOAD")

    def status(self) -> dict[str, Any]:
        if self.demo_mode:
            return {
                "connected": True,
                "bootstrap": 100,
                "summary": "Connecté au réseau Tor",
                "circuit": [
                    {"role": "Entrée", "name": "Belgique"},
                    {"role": "Relais", "name": "Allemagne"},
                    {"role": "Sortie", "name": "Suède"},
                ],
                "exit_ip": "185.220.101.34",
                "exit_country": "SE",
                "bridges": self.bridge_state(),
            }
        try:
            bootstrap = self._info("status/bootstrap-phase")
            progress_match = re.search(r"PROGRESS=(\d+)", bootstrap)
            summary_match = re.search(r'SUMMARY="([^"]+)"', bootstrap)
            progress = int(progress_match.group(1)) if progress_match else 0
            circuit = self._circuit_nodes(self._info("circuit-status")) if progress == 100 else []
            exit_ip = self._exit_ip() if progress == 100 else None
            return {
                "connected": progress == 100,
                "bootstrap": progress,
                "summary": summary_match.group(1) if summary_match else "Tor démarre",
                "circuit": circuit,
                "exit_ip": exit_ip,
                "exit_country": self.country_of(exit_ip),
                "bridges": self.bridge_state(),
            }
        except TorControlError as error:
            return {
                "connected": False,
                "bootstrap": 0,
                "summary": str(error),
                "circuit": [],
                "exit_ip": None,
                "exit_country": None,
                "bridges": {
                    "use_bridges": False,
                    "transport": None,
                    "bridge_count": 0,
                    "known": False,
                },
            }

    def new_identity(self) -> None:
        if self.demo_mode:
            self._exit_cache = (0, None)
            return
        with self._lock:
            self._command("SIGNAL NEWNYM")
            self._exit_cache = (0, None)

    # ------------------------------------------------------------ circuits --

    DEMO_CIRCUITS: ClassVar[list[dict[str, Any]]] = [
        {
            "id": "6",
            "purpose": "GENERAL",
            "nodes": [
                {"role": "Entrée", "name": "ForPrivacyNET"},
                {"role": "Relais", "name": "Quintex12"},
                {"role": "Sortie", "name": "NTH11R1"},
            ],
        },
        {
            "id": "9",
            "purpose": "HS_SERVICE_REND",
            "nodes": [
                {"role": "Entrée", "name": "ForPrivacyNET"},
                {"role": "Relais", "name": "artikel10ber2"},
                {"role": "Sortie", "name": "rendezvous"},
            ],
        },
    ]

    def circuits(self) -> list[dict[str, Any]]:
        """Every circuit Tor currently keeps open, not only the newest one."""
        if self.demo_mode:
            return self.DEMO_CIRCUITS
        try:
            raw = self._info("circuit-status")
        except TorControlError:
            return []
        circuits: list[dict[str, Any]] = []
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[1] != "BUILT":
                continue
            purpose = next(
                (item.split("=", 1)[1] for item in parts[3:] if item.startswith("PURPOSE=")),
                "GENERAL",
            )
            nodes = self._circuit_nodes(f"{parts[0]} BUILT {parts[2]}")
            if nodes:
                circuits.append({"id": parts[0][:12], "purpose": purpose[:32], "nodes": nodes})
        return circuits[:12]

    def country_of(self, address: str | None) -> str | None:
        """Two-letter country of an IP, straight from Tor's GeoIP database."""
        if not address or self.demo_mode:
            return "SE" if self.demo_mode else None
        if not re.fullmatch(r"[0-9a-fA-F:.]+", address):
            return None
        try:
            value = self._info(f"ip-to-country/{address}")
        except TorControlError:
            return None
        code = value.strip().upper()
        return code if re.fullmatch(r"[A-Z]{2}", code) else None

    # ------------------------------------------------------- onion service --

    def add_onion(self, private_key: str | None, target: str) -> dict[str, str]:
        """Publishes a v3 onion service pointing at `target` (host:port).

        `Flags=Detach` keeps the service alive once this short-lived control
        connection closes; it still disappears when Tor itself restarts, which
        is why the caller stores the key and re-publishes at startup.
        """
        if self.demo_mode:
            return {
                "service_id": "onionpidemoaddressonionpidemoaddressonionpidemoaddr2id",
                "private_key": private_key or "ED25519-V3:demo",
            }
        if not re.fullmatch(r"[0-9a-zA-Z.:_-]{3,64}", target):
            raise TorControlError("Cible du service onion invalide")
        if private_key is not None and not re.fullmatch(r"ED25519-V3:[A-Za-z0-9+/=]{1,200}", private_key):
            raise TorControlError("Clé de service onion invalide")
        specification = private_key or "NEW:ED25519-V3"
        with self._lock:
            lines = self._command(f"ADD_ONION {specification} Flags=Detach Port=80,{target}")
        service_id = ""
        stored_key = private_key or ""
        for line in lines:
            body = line[4:] if len(line) > 4 else ""
            if body.startswith("ServiceID="):
                service_id = body.split("=", 1)[1].strip()
            elif body.startswith("PrivateKey="):
                stored_key = body.split("=", 1)[1].strip()
        if not re.fullmatch(r"[a-z2-7]{56}", service_id):
            raise TorControlError("Tor n’a pas renvoyé d’adresse onion valide")
        return {"service_id": service_id, "private_key": stored_key}

    def remove_onion(self, service_id: str) -> None:
        if self.demo_mode:
            return
        if not re.fullmatch(r"[a-z2-7]{16,56}", service_id):
            raise TorControlError("Adresse onion invalide")
        with self._lock:
            self._command(f"DEL_ONION {service_id}")

    def onion_published(self, service_id: str) -> bool:
        if self.demo_mode:
            return True
        try:
            return service_id in self._info("onions/detached").split()
        except TorControlError:
            return False

    # ------------------------------------------------------------ speed test --

    def speed_test(self, sample_bytes: int = 3_000_000, timeout: float = 90.0) -> dict[str, Any]:
        """Measures what a client actually gets through the current circuit."""
        if self.demo_mode:
            return {
                "download_mbps": 6.4,
                "latency_ms": 780,
                "bytes": sample_bytes,
                "seconds": 3.75,
            }
        url = f"https://speed.cloudflare.com/__down?bytes={sample_bytes}"
        started = time.monotonic()
        received = 0
        first_byte: float | None = None
        try:
            with httpx.Client(proxy="socks5h://127.0.0.1:9050", timeout=timeout) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    for chunk in response.iter_bytes():
                        if first_byte is None:
                            first_byte = time.monotonic()
                        received += len(chunk)
        except Exception as error:
            raise TorControlError(
                "Mesure impossible: la connexion Tor n’a pas répondu."
            ) from error
        elapsed = max(time.monotonic() - (first_byte or started), 0.001)
        return {
            "download_mbps": round(received * 8 / elapsed / 1_000_000, 2),
            "latency_ms": int(((first_byte or started) - started) * 1000),
            "bytes": received,
            "seconds": round(elapsed, 2),
        }
