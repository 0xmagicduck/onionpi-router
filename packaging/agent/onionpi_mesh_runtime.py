#!/usr/bin/env python3
"""Plan de données du maillage OnionPi, côté nœud.

Deux moitiés, une seule sécurité.

* **Répondeur.** Une écoute sur la boucle locale, publiée comme second port
  virtuel du service onion du nœud et joignable aussi sur `bat0` quand la radio
  existe. Elle répond à une poignée de main Noise_IK et, seulement si la clé
  statique du pair figure dans la carte signée, relaie le flux vers le port
  demandé — à condition que ce port soit dans les habilitations que **ce** nœud
  a reçues. Les deux extrémités appliquent la carte: ce qu'on a raconté à
  l'initiateur n'entre pas dans la décision.
* **Redirections.** Des écoutes locales, une par ligne `forwards` de la carte,
  qui présentent un port distant ici — comme `ssh -L`. C'est le mode par défaut
  du plan de données: un flux onion est du TCP, et faire passer de l'IP dedans
  empile deux contrôles de congestion qui réagissent au même événement.

Le choix de chemin est celui de Tailscale entre lien direct et relais, transposé:

    direct   bat0 (802.11s, WPA3-SAE)   ~1 ms      pair à portée radio
    relayé   flux onion via SOCKS       0,3 – 2 s  tous les autres

La **même session Noise** dans les deux cas. Le lien radio n'est donc pas une
frontière de confiance — `batman-adv` n'authentifie pas ses annonces
d'originator au-delà du lien —, c'est un tuyau rapide, et une phrase SAE
partagée compromise ne donne qu'un tuyau.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any

from onionpi_mesh import (
    MAX_MESSAGE,
    HandshakeState,
    Identity,
    MeshLock,
    NetmapError,
    decode_key,
    frame,
    read_frame,
    verify_netmap,
)

logger = logging.getLogger("onionpi-node-agent.mesh")

#: Sessions servies en parallèle. Une borne, pas un réglage: un thread par
#: connexion sans plafond, c'est la mémoire de la machine offerte à qui sait
#: ouvrir des circuits.
MAX_SESSIONS = 32
#: Tampon d'un sens de flux. Sous le plafond d'un message Noise, tag compris.
CHUNK = MAX_MESSAGE - 64
#: Délais. Le direct est court parce qu'un pair hors de portée doit échouer vite
#: et laisser sa place au chemin relayé; le relayé est long parce qu'un circuit
#: Tor met couramment plusieurs secondes à s'ouvrir.
DIRECT_TIMEOUT = 1.5
RELAY_TIMEOUT = 45.0
SESSION_TIMEOUT = 300.0
#: Durée pendant laquelle un chemin qui a marché est réessayé en premier. Assez
#: long pour ne pas resonder à chaque connexion, assez court pour qu'un pair qui
#: revient à portée radio retrouve le chemin direct dans la minute.
PATH_MEMORY = 60.0


class MeshRefused(RuntimeError):
    """Le pair a refusé, ou n'était pas joignable. Rien n'a été relayé."""


def socks_connect(host: str, port: int, socks_port: int, timeout: float) -> socket.socket:
    """Ouvre `host:port` à travers le SOCKS5 local de Tor.

    Le nom est envoyé tel quel (SOCKS5 « domainname »): une adresse onion n'a
    pas de résolution DNS, et en résoudre une localement serait la fuiter.
    """
    stream = socket.create_connection(("127.0.0.1", socks_port), timeout=timeout)
    try:
        stream.settimeout(timeout)
        stream.sendall(b"\x05\x01\x00")
        if stream.recv(2) != b"\x05\x00":
            raise MeshRefused("SOCKS: négociation refusée")
        name = host.encode("idna") if host.isascii() else host.encode()
        if len(name) > 255:
            raise MeshRefused("SOCKS: nom trop long")
        stream.sendall(
            b"\x05\x01\x00\x03" + bytes([len(name)]) + name + struct.pack(">H", port)
        )
        reply = stream.recv(4)
        if len(reply) < 4 or reply[1] != 0:
            raise MeshRefused("SOCKS: connexion refusée par Tor")
        # L'adresse de rattachement, qu'on lit pour la jeter: elle précède les
        # octets applicatifs et la laisser dans le tampon décalerait tout.
        kind = reply[3]
        extra = {1: 4, 4: 16}.get(kind)
        if extra is None:
            length = stream.recv(1)
            extra = length[0] if length else 0
        if extra:
            stream.recv(extra)
        stream.recv(2)
    except OSError as error:
        stream.close()
        raise MeshRefused(f"SOCKS: {error}") from error
    except MeshRefused:
        stream.close()
        raise
    return stream


def direct_connect(address: str, port: int, timeout: float) -> socket.socket:
    try:
        return socket.create_connection((address, port), timeout=timeout)
    except OSError as error:
        raise MeshRefused(f"direct: {error}") from error


def _pump(source: socket.socket, sink: socket.socket, cipher: Any, encrypting: bool) -> None:
    """Relaie un sens, chiffré ou déchiffré selon le côté du tunnel."""
    try:
        while True:
            if encrypting:
                data = source.recv(CHUNK)
                if not data:
                    break
                sink.sendall(frame(cipher.encrypt(data)))
            else:
                payload = read_frame(source)
                if not payload:
                    break
                sink.sendall(cipher.decrypt(payload))
    except (OSError, ValueError, ConnectionError):
        # Un flux coupé, un bloc refusé par Poly1305: dans les deux cas la
        # session est finie, et il n'y a rien à dire de plus au pair.
        pass
    finally:
        for stream in (source, sink):
            try:
                stream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _splice(
    outer: socket.socket, inner: socket.socket, send: Any, receive: Any
) -> None:
    """Fait tourner les deux sens jusqu'à ce que l'un se ferme."""
    threads = (
        threading.Thread(target=_pump, args=(inner, outer, send, True), daemon=True),
        threading.Thread(target=_pump, args=(outer, inner, receive, False), daemon=True),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


class MeshRuntime:
    """L'identité du nœud, sa carte, ses écoutes. Une instance par agent."""

    def __init__(
        self,
        identity: Identity,
        state_dir: Path,
        node_id: str,
        *,
        coordinator_key: str = "",
        lock_path: Path | None = None,
        port: int = 9081,
        socks_port: int = 9050,
        direct_address: str = "",
    ) -> None:
        self.identity = identity
        self.state_dir = state_dir
        self.node_id = node_id
        self.netmap_path = state_dir / "netmap.json"
        self.port = port
        self.socks_port = socks_port
        self.direct_address = direct_address
        self.coordinator_key = b""
        if coordinator_key:
            try:
                self.coordinator_key = decode_key("ed25519:", coordinator_key)
            except ValueError:
                logger.warning("Clé de coordinateur illisible: maillage inactif")
        self.lock = MeshLock.load(lock_path) if lock_path else None
        self._guard = threading.RLock()
        self._netmap: dict[str, Any] = {}
        self._paths: dict[str, tuple[str, float]] = {}
        self._sessions = threading.BoundedSemaphore(MAX_SESSIONS)
        self._active = 0
        self._servers: list[socket.socket] = []
        self._stop = threading.Event()
        self._load()

    # ----------------------------------------------------------- carte ---

    def _load(self) -> None:
        """Relit la carte conservée, en la revérifiant.

        Le fichier appartient à l'agent, donc le relire sans revérifier ferait
        d'un compte capable d'y écrire un coordinateur. La signature et le
        `not_after` sont les mêmes règles qu'à la réception.
        """
        if not self.coordinator_key:
            return
        try:
            stored = json.loads(self.netmap_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        try:
            self._netmap = verify_netmap(
                stored,
                self.coordinator_key,
                last_serial=0,
                now=int(time.time()),
                lock=self.lock,
                known_identities=self._known_identities(stored),
            )
        except NetmapError as error:
            logger.info("Carte conservée écartée: %s", error)
            self._netmap = {}

    @staticmethod
    def _known_identities(document: Any) -> dict[str, str]:
        """Les clés déjà acceptées: sous verrou, elles n'ont pas à être re-signées."""
        peers = document.get("peers", []) if isinstance(document, dict) else []
        return {
            str(peer.get("node", "")): str(peer.get("identity", ""))
            for peer in peers
            if isinstance(peer, dict)
        }

    def serial(self) -> int:
        return int(self._netmap.get("serial", 0) or 0)

    def accept(self, document: Any) -> dict[str, Any]:
        """Vérifie une carte, la conserve et rouvre les redirections."""
        if not self.coordinator_key:
            raise NetmapError(
                "Aucune clé de coordinateur épinglée: réinstallez l'agent avec "
                "--coordinator-key."
            )
        with self._guard:
            checked = verify_netmap(
                document,
                self.coordinator_key,
                last_serial=self.serial(),
                now=int(time.time()),
                lock=self.lock,
                known_identities=self._known_identities(self._netmap),
            )
            if str(checked.get("node", "")) != self.node_id:
                # Une carte adressée à un autre nœud porte les habilitations
                # d'un autre nœud. La refuser vaut mieux que les appliquer ici.
                raise NetmapError("Carte destinée à un autre nœud")
            self._netmap = checked
            self._write(checked)
            self._paths.clear()
        self._restart_forwards()
        return {
            "accepted": True,
            "serial": int(checked["serial"]),
            "peers": len(checked.get("peers", [])),
            "message": "Carte acceptée",
        }

    def _write(self, document: dict[str, Any]) -> None:
        temporary = self.netmap_path.with_suffix(".tmp")
        handle = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(document))
        os.replace(temporary, self.netmap_path)

    def peers(self) -> list[dict[str, Any]]:
        with self._guard:
            revoked = set(self._netmap.get("revoked", []) or [])
            return [
                peer
                for peer in self._netmap.get("peers", []) or []
                if str(peer.get("identity", "")) not in revoked
            ]

    def peer_by_static(self, static: bytes) -> dict[str, Any] | None:
        wanted = "x25519:" + static.hex()
        return next(
            (peer for peer in self.peers() if str(peer.get("static", "")) == wanted), None
        )

    def peer_by_node(self, node: str) -> dict[str, Any] | None:
        return next((peer for peer in self.peers() if str(peer.get("node")) == node), None)

    def status(self) -> dict[str, Any]:
        return {
            **self.identity.announcement(),
            "port": self.port,
            "direct": self.direct_address,
            "netmap_serial": self.serial(),
            "peers": len(self.peers()),
            "sessions": self._active,
            "locked": self.lock is not None,
        }

    # ------------------------------------------------------- répondeur ---

    def start(self) -> None:
        # La boucle locale porte le chemin relayé: c'est Tor qui s'y connecte
        # quand un circuit arrive sur le second port virtuel du service onion.
        self._listen(("127.0.0.1", self.port), self._serve_peer)
        if self.direct_address:
            # Et l'adresse de `bat0` porte le chemin direct. Écouter là plutôt
            # que sur 0.0.0.0: sur un VPS, une écoute non liée serait un port
            # ouvert sur Internet, ce que cet agent ne fait jamais.
            self._listen((self.direct_address, self.port), self._serve_peer)
        self._restart_forwards()

    def stop(self) -> None:
        self._stop.set()
        with self._guard:
            servers, self._servers = self._servers, []
        for server in servers:
            try:
                server.close()
            except OSError:
                pass

    def _listen(self, address: tuple[str, int], handler: Any, *args: Any) -> None:
        """Ouvre une écoute et sert ses connexions dans un fil dédié.

        Une écoute qui échoue est signalée et n'arrête pas l'agent: un port
        local déjà pris est une redirection en moins, pas un nœud injoignable.
        """
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(address)
            server.listen(16)
        except OSError as error:
            logger.warning("Écoute %s:%d impossible: %s", address[0], address[1], error)
            return
        with self._guard:
            self._servers.append(server)
        threading.Thread(
            target=self._accept_loop, args=(server, handler, args), daemon=True
        ).start()

    def _accept_loop(self, server: socket.socket, handler: Any, args: tuple) -> None:
        while not self._stop.is_set():
            try:
                stream, _ = server.accept()
            except OSError:
                return
            if not self._sessions.acquire(blocking=False):
                logger.warning("Session de maillage refusée: %d en cours", MAX_SESSIONS)
                stream.close()
                continue
            threading.Thread(
                target=self._serve, args=(handler, stream, args), daemon=True
            ).start()

    def _serve(self, handler: Any, stream: socket.socket, args: tuple) -> None:
        with self._guard:
            self._active += 1
        try:
            stream.settimeout(SESSION_TIMEOUT)
            handler(stream, *args)
        except (MeshRefused, OSError, ValueError, ConnectionError) as error:
            logger.info("Session de maillage terminée: %s", error)
        finally:
            with self._guard:
                self._active -= 1
            self._sessions.release()
            try:
                stream.close()
            except OSError:
                pass

    def _serve_peer(self, stream: socket.socket) -> None:
        """Répond à un pair: poignée de main, habilitation, puis relais."""
        handshake = HandshakeState(self.identity.static_private)
        request = handshake.read_message_one(read_frame(stream))
        peer = self.peer_by_static(handshake.rs)
        if peer is None:
            # Aucune réponse: un pair absent de la carte n'a pas à apprendre
            # qu'il existe une carte, ni ce qu'elle contient.
            raise MeshRefused("pair absent de la carte")
        try:
            wanted = int(json.loads(request.decode("utf-8")).get("port", 0))
        except (UnicodeDecodeError, ValueError, AttributeError) as error:
            raise MeshRefused("demande illisible") from error
        allowed = 1 <= wanted <= 65535 and wanted in self._grants()
        answer = {"ok": allowed} if allowed else {"ok": False, "detail": "port non habilité"}
        message, send, receive = handshake.write_message_two(
            json.dumps(answer).encode("utf-8")
        )
        stream.sendall(frame(message))
        if not allowed:
            logger.info("Port %d refusé au pair %s", wanted, peer.get("node", ""))
            return
        inner = socket.create_connection(("127.0.0.1", wanted), timeout=DIRECT_TIMEOUT * 4)
        try:
            inner.settimeout(SESSION_TIMEOUT)
            _splice(stream, inner, send, receive)
        finally:
            inner.close()

    def _grants(self) -> set[int]:
        """Ce que ce nœud accepte de présenter, tel que sa propre carte le dit.

        Lu dans la carte reçue plutôt que dans une configuration locale: c'est
        ce qui fait que les deux extrémités appliquent la même habilitation, et
        qu'un initiateur à qui on aurait raconté autre chose n'obtient rien.
        """
        with self._guard:
            raw = self._netmap.get("grants", []) or []
        return {
            int(port)
            for port in raw
            if isinstance(port, int) and not isinstance(port, bool)
        }

    # ----------------------------------------------------- redirections ---

    def _restart_forwards(self) -> None:
        """Rouvre les écoutes locales décrites par la carte en cours."""
        with self._guard:
            keep: list[socket.socket] = []
            for server in self._servers:
                try:
                    if server.getsockname()[1] == self.port:
                        keep.append(server)
                        continue
                except OSError:
                    continue
                try:
                    server.close()
                except OSError:
                    pass
            self._servers = keep
            forwards = list(self._netmap.get("forwards", []) or [])
        for forward in forwards:
            try:
                listen = int(forward["listen"])
                node = str(forward["node"])
                target = int(forward["port"])
            except (KeyError, TypeError, ValueError):
                continue
            # Boucle locale seulement: une redirection est un port de cette
            # machine vers un pair, pas un service offert au réseau local.
            self._listen(("127.0.0.1", listen), self._serve_forward, node, target)

    def _serve_forward(self, stream: socket.socket, node: str, target: int) -> None:
        peer = self.peer_by_node(node)
        if peer is None:
            raise MeshRefused(f"pair {node} absent de la carte")
        outer, send, receive, path = self.dial(peer, target)
        logger.info("Session vers %s:%d par le chemin %s", node, target, path)
        try:
            _splice(outer, stream, send, receive)
        finally:
            outer.close()

    # ------------------------------------------------------- initiateur ---

    def dial(self, peer: dict[str, Any], port: int) -> tuple[socket.socket, Any, Any, str]:
        """Ouvre une session Noise vers un pair, direct d'abord, relayé sinon."""
        errors: list[str] = []
        for path in self._order(peer):
            started = time.monotonic()
            try:
                outer = self._open(peer, path)
            except MeshRefused as error:
                errors.append(str(error))
                continue
            try:
                send, receive = self._handshake(outer, peer, port)
            except (MeshRefused, OSError, ValueError, ConnectionError) as error:
                outer.close()
                errors.append(f"{path}: {error}")
                continue
            with self._guard:
                self._paths[str(peer.get("node", ""))] = (path, time.monotonic())
            logger.info(
                "Chemin %s vers %s en %d ms",
                path,
                peer.get("node", ""),
                int((time.monotonic() - started) * 1000),
            )
            return outer, send, receive, path
        raise MeshRefused("; ".join(errors) or "aucun chemin vers ce pair")

    def _order(self, peer: dict[str, Any]) -> list[str]:
        """Les chemins à essayer, le dernier qui a marché en tête.

        Sonder le direct à chaque connexion coûterait une seconde et demie à
        chaque fois qu'un pair est hors de portée; ne jamais le resonder ferait
        rater son retour. La mémoire est donc courte et volontairement bête.
        """
        paths = []
        if peer.get("mesh_v4"):
            paths.append("direct")
        if peer.get("onion"):
            paths.append("relayé")
        with self._guard:
            remembered = self._paths.get(str(peer.get("node", "")))
        if remembered and time.monotonic() - remembered[1] < PATH_MEMORY:
            paths.sort(key=lambda path: path != remembered[0])
        return paths

    def _open(self, peer: dict[str, Any], path: str) -> socket.socket:
        port = int(peer.get("mesh_port", self.port) or self.port)
        if path == "direct":
            return direct_connect(str(peer["mesh_v4"]), port, DIRECT_TIMEOUT)
        return socks_connect(
            f"{peer['onion']}.onion", port, self.socks_port, RELAY_TIMEOUT
        )

    def _handshake(
        self, outer: socket.socket, peer: dict[str, Any], port: int
    ) -> tuple[Any, Any]:
        outer.settimeout(SESSION_TIMEOUT)
        static = decode_key("x25519:", str(peer.get("static", "")))
        handshake = HandshakeState(self.identity.static_private)
        outer.sendall(
            frame(
                handshake.write_message_one(
                    static, json.dumps({"port": int(port)}).encode("utf-8")
                )
            )
        )
        payload, send, receive = handshake.read_message_two(read_frame(outer))
        try:
            answer = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise MeshRefused("réponse illisible du pair") from error
        if not isinstance(answer, dict) or not answer.get("ok"):
            detail = str((answer or {}).get("detail", "refusé"))[:120]
            raise MeshRefused(f"pair: {detail}")
        return send, receive
