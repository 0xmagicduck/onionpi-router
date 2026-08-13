#!/usr/bin/env python3
"""Agent OnionPi pour un nœud distant (VPS, serveur, seconde machine).

Il n'écoute que sur la boucle locale. Ce qui le rend joignable est le service
onion v3 publié par le Tor de cette machine, dont le descripteur est chiffré
pour la seule clé de la baie qui l'administre. Aucun port n'est ouvert sur
Internet, et l'adresse seule ne suffit pas à l'atteindre.

Chaque appel est signé: HMAC-SHA256 sur le verbe, un horodatage, un nonce et
l'empreinte du corps. Un horodatage périmé ou un nonce déjà vu est refusé, donc
une requête capturée ne peut pas être rejouée.

Ce programme ne dispose d'aucun privilège. Les trois actions qui en demandent
— appliquer le pare-feu, redémarrer Tor, redémarrer la machine — passent par un
fichier de requête qu'un service privilégié natif relit et revalide. La
frontière de sécurité est l'exécutant `onionpi-node-apply-*`, pas ce fichier.

Dépendances: la bibliothèque standard uniquement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.4.1"
PROTOCOL_VERSION = 1

STATE_DIR = Path(os.environ.get("ONIONPI_NODE_STATE", "/var/lib/onionpi-node"))
RESULT_DIR = Path(os.environ.get("ONIONPI_NODE_RESULT", "/var/lib/onionpi-node-privileged"))
REQUEST_PATH = STATE_DIR / "apply.request"
RESULT_PATH = RESULT_DIR / "apply.result"
POLICY_PATH = STATE_DIR / "policy.json"
APPLIED_PATH = RESULT_DIR / "policy.applied"

TOR_COOKIE = Path(os.environ.get("ONIONPI_NODE_TOR_COOKIE", "/run/tor/control.authcookie"))
TOR_CONTROL_PORT = int(os.environ.get("ONIONPI_NODE_TOR_CONTROL_PORT", "9051"))

MAX_BODY_BYTES = 64 * 1024
MAX_CLOCK_SKEW = 120
NONCE_MEMORY = 512
MAX_JOURNAL_LINES = 200
PLATFORM = platform.system().lower()
LOG_DIR_VALUE = os.environ.get("ONIONPI_NODE_LOG_DIR", "")
LOG_DIR = Path(LOG_DIR_VALUE) if LOG_DIR_VALUE else None

#: Le même vocabulaire que côté baie. Un verbe absent d'ici n'existe pas, quelle
#: que soit la signature qui l'accompagne.
VERBS = ("status", "apply-policy", "new-identity", "restart-tor", "journal", "reboot")

#: Unités dont le journal peut être lu. Une liste blanche: un nom arbitraire
#: transformerait cette lecture en fuite du journal complet de la machine.
JOURNAL_UNITS = ("tor", "onionpi-node-agent", "onionpi-node-apply", "ssh", "sshd")

EGRESS_MODES = ("tor-only", "direct")

logger = logging.getLogger("onionpi-node-agent")


def load_config() -> dict[str, str]:
    """Lit /etc/onionpi-node/agent.env, écrit par l'installateur."""
    path = Path(os.environ.get("ONIONPI_NODE_CONFIG", "/etc/onionpi-node/agent.env"))
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"')
    except OSError as error:
        sys.exit(f"Configuration illisible ({path}): {error}")
    for required in ("NODE_ID", "TOKEN"):
        if not values.get(required):
            sys.exit(f"{required} absent de {path}")
    if not re.fullmatch(r"[0-9a-f]{16}", values["NODE_ID"]):
        sys.exit("NODE_ID invalide")
    if not re.fullmatch(r"[0-9a-f]{64}", values["TOKEN"]):
        sys.exit("TOKEN invalide")
    return values


class ReplayGuard:
    """Mémoire courte des nonces déjà servis, bornée par construction."""

    def __init__(self, size: int = NONCE_MEMORY) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._size = size

    def accept(self, nonce: str) -> bool:
        now = time.monotonic()
        cutoff = now - MAX_CLOCK_SKEW * 2
        while self._seen and next(iter(self._seen.values())) < cutoff:
            self._seen.popitem(last=False)
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        while len(self._seen) > self._size:
            self._seen.popitem(last=False)
        return True


class PrivilegedRequest:
    """Passe-plat vers le service root, sur le modèle de la Raspberry Pi."""

    ACTIONS = ("policy", "restart-tor", "reboot")

    def submit(self, action: str, timeout: float | None = None) -> dict[str, object]:
        if action not in self.ACTIONS:
            raise ValueError(f"Action privilégiée inconnue: {action}")
        nonce = secrets.token_hex(8)
        temporary = REQUEST_PATH.with_suffix(".tmp")
        temporary.write_text(f"{nonce} {action}\n", encoding="utf-8")
        os.chmod(temporary, 0o640)
        os.replace(temporary, REQUEST_PATH)
        if action == "reboot":
            return {"status": "pending", "message": "Redémarrage demandé"}
        wait = timeout if timeout is not None else float(
            os.environ.get("ONIONPI_NODE_APPLY_TIMEOUT", "25")
        )
        deadline = time.monotonic() + max(5.0, min(wait, 120.0))
        while time.monotonic() < deadline:
            answer = self._result(nonce)
            if answer is not None:
                return answer
            time.sleep(0.25)
        return {"status": "error", "message": "Aucune réponse du service privilégié"}

    @staticmethod
    def _result(nonce: str) -> dict[str, object] | None:
        try:
            parts = RESULT_PATH.read_text(encoding="utf-8").strip().split(" ", 2)
        except OSError:
            return None
        if len(parts) < 2 or parts[0] != nonce:
            return None
        return {"status": parts[1], "message": parts[2] if len(parts) > 2 else ""}


class TorControl:
    """Le strict minimum du protocole de contrôle: authentifier, signaler."""

    def command(self, command: str) -> list[str]:
        if "\r" in command or "\n" in command:
            raise RuntimeError("Commande de contrôle invalide")
        cookie = TOR_COOKIE.read_bytes()
        with socket.create_connection(("127.0.0.1", TOR_CONTROL_PORT), timeout=5) as link:
            link.settimeout(5)
            stream = link.makefile("rwb", buffering=0)
            stream.write(f"AUTHENTICATE {cookie.hex()}\r\n".encode())
            if not self._ok(self._read(stream)):
                raise RuntimeError("Authentification Tor refusée")
            stream.write((command + "\r\n").encode())
            lines = self._read(stream)
            if not self._ok(lines):
                raise RuntimeError(f"Tor a refusé « {command.split()[0]} »")
            return lines

    @staticmethod
    def _ok(lines: list[str]) -> bool:
        return bool(lines) and lines[-1].startswith("250")

    @staticmethod
    def _read(stream: object) -> list[str]:
        lines: list[str] = []
        while True:
            raw = stream.readline()  # type: ignore[attr-defined]
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            lines.append(line)
            if re.match(r"^\d{3} ", line):
                break
        return lines

    def bootstrap(self) -> dict[str, object]:
        try:
            lines = self.command("GETINFO status/bootstrap-phase")
        except (OSError, RuntimeError) as error:
            return {"connected": False, "bootstrap": 0, "summary": str(error)}
        joined = "\n".join(lines)
        progress = re.search(r"PROGRESS=(\d+)", joined)
        summary = re.search(r'SUMMARY="([^"]+)"', joined)
        value = int(progress.group(1)) if progress else 0
        return {
            "connected": value == 100,
            "bootstrap": value,
            "summary": summary.group(1) if summary else "",
        }

    def new_identity(self) -> None:
        self.command("SIGNAL NEWNYM")


def read_status(tor: TorControl) -> dict[str, object]:
    usage = shutil.disk_usage("/")
    try:
        applied = json.loads(APPLIED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        applied = {}
    return {
        "agent_version": VERSION,
        "hostname": socket.gethostname()[:64],
        "uptime_seconds": int(read_uptime()),
        "load": load_average(),
        "memory_percent": memory_percent(),
        "storage_percent": round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
        "tor": tor.bootstrap(),
        "policy": {
            "digest": str(applied.get("digest", ""))[:64],
            "egress": str(applied.get("egress", ""))[:16],
            "applied_at": int(applied.get("applied_at", 0) or 0),
        },
        "services": [
            {"id": unit, "label": unit, "active": unit_active(unit)}
            for unit in ("tor", "onionpi-node-agent")
        ],
        "platform": {
            "system": platform.system(),
            "release": platform.release()[:64],
            "machine": platform.machine()[:32],
            "policy_mode": (
                "complet" if PLATFORM == "linux" else
                "pf" if PLATFORM == "darwin" else
                "sortie uniquement" if PLATFORM == "windows" else
                "non pris en charge"
            ),
        },
    }


def read_uptime() -> float:
    if PLATFORM == "windows":
        try:
            import ctypes

            return float(ctypes.windll.kernel32.GetTickCount64()) / 1000
        except (AttributeError, OSError):
            return 0.0
    if PLATFORM == "darwin":
        try:
            result = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            match = re.search(r"sec\s*=\s*(\d+)", result.stdout)
            return max(0.0, time.time() - int(match.group(1))) if match else 0.0
        except (OSError, subprocess.SubprocessError):
            return 0.0
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def memory_percent() -> float:
    if PLATFORM == "windows":
        try:
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return float(status.memory_load)
        except (AttributeError, OSError):
            return 0.0
    if PLATFORM == "darwin":
        try:
            total = int(
                subprocess.check_output(
                    ["/usr/sbin/sysctl", "-n", "hw.memsize"], text=True, timeout=5
                ).strip()
            )
            output = subprocess.check_output(["/usr/bin/vm_stat"], text=True, timeout=5)
            page_match = re.search(r"page size of (\d+) bytes", output)
            page_size = int(page_match.group(1)) if page_match else 4096
            free_pages = 0
            for label in ("Pages free", "Pages inactive", "Pages speculative"):
                match = re.search(rf"^{label}:\s+(\d+)\.", output, re.MULTILINE)
                free_pages += int(match.group(1)) if match else 0
            available = free_pages * page_size
            return round((total - available) / total * 100, 1) if total else 0.0
        except (OSError, ValueError, subprocess.SubprocessError):
            return 0.0
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            fields[key] = float(value.strip().split()[0])
        total = fields.get("MemTotal", 0.0)
        available = fields.get("MemAvailable", 0.0)
    except (OSError, ValueError, IndexError):
        return 0.0
    return round((total - available) / total * 100, 1) if total else 0.0


def load_average() -> float:
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        return 0.0


def unit_active(unit: str) -> bool:
    if PLATFORM == "darwin":
        label = "com.onionpi.node.tor" if unit == "tor" else "com.onionpi.node.agent"
        command = ["/bin/launchctl", "print", f"system/{label}"]
    elif PLATFORM == "windows":
        image = "tor.exe" if unit == "tor" else "python.exe"
        command = ["tasklist.exe", "/FI", f"IMAGENAME eq {image}"]
    else:
        command = ["systemctl", "is-active", "--quiet", unit]
    try:
        result = subprocess.run(  # noqa: S603
            command,  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if PLATFORM == "windows":
        return result.returncode == 0 and image.lower() in result.stdout.lower()
    return result.returncode == 0


def read_journal(unit: str, lines: int) -> list[str]:
    if unit not in JOURNAL_UNITS:
        raise ValueError("Service non autorisé")
    count = max(1, min(int(lines), MAX_JOURNAL_LINES))
    if PLATFORM in {"darwin", "windows"} and LOG_DIR is not None:
        filenames = {
            "tor": "tor.log",
            "onionpi-node-agent": "agent.log",
            "onionpi-node-apply": "apply.log",
        }
        path = LOG_DIR / filenames.get(unit, "agent.log")
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]
        except OSError as error:
            raise RuntimeError(f"Journal illisible: {error}") from error
    try:
        result = subprocess.run(  # noqa: S603
            ["journalctl", "-u", unit, "-n", str(count), "--no-pager", "--output=short"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(f"Journal illisible: {error}") from error
    return result.stdout.splitlines()[-count:]


def store_policy(document: dict[str, object]) -> dict[str, object]:
    """Valide la politique reçue, l'écrit, puis demande son application.

    La validation est refaite par `render-policy.py` sous root: celle-ci sert à
    refuser tôt et à répondre clairement, pas à protéger le pare-feu.
    """
    egress = str(document.get("egress", ""))
    if egress not in EGRESS_MODES:
        raise ValueError("Mode de sortie invalide")
    ports = document.get("keep_open_ports", [])
    if not isinstance(ports, list) or len(ports) > 8:
        raise ValueError("Liste de ports invalide")
    for port in ports:
        # `isinstance(True, int)` is true in Python: without the second test,
        # `[true]` in a JSON body would become port 1.
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("Port invalide")
    country = str(document.get("exit_country", ""))
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise ValueError("Pays de sortie invalide")
    digest = str(document.get("digest", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Empreinte de politique invalide")
    body = {
        "version": PROTOCOL_VERSION,
        "egress": egress,
        "exit_country": country,
        "keep_open_ports": sorted({int(port) for port in ports}),
        "isolated": bool(document.get("isolated", False)),
        "digest": digest,
    }
    temporary = POLICY_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    os.replace(temporary, POLICY_PATH)
    answer = PrivilegedRequest().submit("policy")
    return {
        "applied": answer.get("status") == "ok",
        "digest": digest,
        "message": str(answer.get("message", "")),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"onionpi-node/{VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    config: dict[str, str] = {}
    replay = ReplayGuard()
    tor = TorControl()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        logger.info("%s %s", self.address_string(), format % args)

    def do_POST(self) -> None:  # noqa: N802 - nom imposé par BaseHTTPRequestHandler
        match = re.fullmatch(r"/agent/v1/([a-z-]{1,32})", self.path)
        if not match or match.group(1) not in VERBS:
            self.respond(404, {"detail": "Action inconnue"})
            return
        verb = match.group(1)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.respond(400, {"detail": "En-tête de longueur invalide"})
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self.respond(413, {"detail": "Corps de requête trop long"})
            return
        body = self.rfile.read(length) if length else b""
        if not self.authentic(verb, body):
            self.respond(401, {"detail": "Signature refusée"})
            return
        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.respond(400, {"detail": "Corps illisible"})
            return
        if not isinstance(payload, dict):
            self.respond(400, {"detail": "Corps illisible"})
            return
        try:
            self.respond(200, self.dispatch(verb, payload))
        except ValueError as error:
            self.respond(400, {"detail": str(error)})
        except (OSError, RuntimeError) as error:
            logger.warning("%s: %s", verb, error)
            self.respond(500, {"detail": str(error)})

    def authentic(self, verb: str, body: bytes) -> bool:
        node = self.headers.get("X-OnionPi-Node", "")
        nonce = self.headers.get("X-OnionPi-Nonce", "")
        signature = self.headers.get("X-OnionPi-Signature", "")
        try:
            timestamp = int(self.headers.get("X-OnionPi-Timestamp", "0"))
        except ValueError:
            return False
        if not hmac.compare_digest(node, self.config["NODE_ID"]):
            return False
        if abs(int(time.time()) - timestamp) > MAX_CLOCK_SKEW:
            return False
        if not re.fullmatch(r"[0-9a-f]{16,64}", nonce) or not self.replay.accept(nonce):
            return False
        canonical = (
            f"{PROTOCOL_VERSION}\n{verb}\n{timestamp}\n{nonce}\n"
            f"{hashlib.sha256(body).hexdigest()}"
        )
        expected = hmac.new(
            self.config["TOKEN"].encode(), canonical.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def dispatch(self, verb: str, payload: dict[str, object]) -> dict[str, object]:
        if verb == "status":
            return read_status(self.tor)
        if verb == "apply-policy":
            return store_policy(payload)
        if verb == "new-identity":
            self.tor.new_identity()
            return {"ok": True, "message": "Nouvelle identité Tor demandée"}
        if verb == "restart-tor":
            answer = PrivilegedRequest().submit("restart-tor")
            return {"ok": answer.get("status") == "ok", "message": answer.get("message", "")}
        if verb == "journal":
            unit = str(payload.get("unit", "tor"))
            return {"unit": unit, "lines": read_journal(unit, int(payload.get("lines", 80)))}
        if verb == "reboot":
            PrivilegedRequest().submit("reboot")
            return {"ok": True, "message": "Redémarrage demandé"}
        raise ValueError("Action inconnue")

    def respond(self, status: int, document: dict[str, object]) -> None:
        body = json.dumps(document, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    Handler.config = load_config()
    port = int(os.environ.get("ONIONPI_NODE_PORT", Handler.config.get("PORT", "9080")))
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Boucle locale uniquement: ce qui rend l'agent joignable est le service
    # onion, jamais une interface réseau.
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    logger.info("Agent OnionPi %s à l'écoute sur 127.0.0.1:%d", VERSION, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
