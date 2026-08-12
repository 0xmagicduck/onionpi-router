"""Security posture audit: what is weak right now, and what to do about it.

`diagnostics.py` answers "does this appliance still work". This module answers
a different question: "is it still worth trusting". A service can be perfectly
healthy and yet expose SSH to every Wi-Fi guest, run on a password set two
years ago, or publish an onion address that anybody who once read it can still
reach.

Every finding carries the action that fixes it, so the report is a work list
rather than a score to feel good about. Nothing here reads a secret, a client
identifier or a DNS query: the report is safe to download and to send to
someone else for help.
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import TYPE_CHECKING, Any

from cryptography import x509

from .policy import EXIT_COUNTRIES
from .system import _run

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters to type checkers
    from .services import AppServices

logger = logging.getLogger("onionpi.hardening")

#: Points removed from a perfect score for each unresolved finding.
SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 30,
    "high": 18,
    "medium": 9,
    "low": 4,
}

LEVELS: tuple[tuple[int, str, str], ...] = (
    (90, "excellent", "Configuration durcie"),
    (75, "solide", "Bonne configuration"),
    (50, "renforcer", "Configuration à renforcer"),
    (0, "faible", "Configuration exposée"),
)

YEAR_SECONDS = 365 * 24 * 3600
DAY_SECONDS = 24 * 3600


def _finding(
    identifier: str,
    title: str,
    severity: str,
    ok: bool,
    detail: str,
    remedy: str = "",
    page: str = "",
    action: str = "",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "title": title,
        "severity": severity,
        "ok": ok,
        "detail": detail,
        "remedy": "" if ok else remedy,
        # Where the interface should send the operator, and which privileged
        # verb — if any — repairs the finding without leaving the page.
        "page": page,
        "action": action,
    }


def _demo_finding(demo_mode: bool) -> dict[str, Any]:
    return _finding(
        "demo-mode",
        "Mode démonstration",
        "critical",
        not demo_mode,
        "Les contrôles réseau sont simulés: rien n’est réellement appliqué."
        if demo_mode
        else "L’appliance applique réellement ses règles.",
        "Retirez ONIONPI_DEMO_MODE de /etc/onionpi/onionpi.env puis redémarrez le service.",
        page="settings",
    )


def _agent_finding(available: bool) -> dict[str, Any]:
    return _finding(
        "privileged-agent",
        "Canal d’actions privilégiées",
        "critical",
        available,
        "La file root répond." if available else "La file root est indisponible.",
        "Vérifiez « systemctl status onionpi-agent.path » depuis la console.",
        page="settings",
    )


def _protection_finding(protection: dict[str, Any]) -> dict[str, Any]:
    status = str(protection.get("status", ""))
    return _finding(
        "protection-state",
        "Coupe-circuit et acheminement Tor",
        "critical" if status == "degraded" else "high",
        status == "protected",
        str(protection.get("summary", "")),
        "Ouvrez Protection: chaque contrôle en échec y propose son action sûre.",
        page="protection",
        action="restart-firewall" if status == "degraded" else "",
    )


def _ssh_finding(services: AppServices) -> dict[str, Any]:
    settings = services.settings
    if settings.demo_mode:
        exposed = False
        detail = "SSH n’est pas ouvert aux clients Wi-Fi (démonstration)."
    else:
        try:
            rules = settings.firewall_rules_path.read_text()
        except OSError:
            return _finding(
                "ssh-exposure",
                "SSH depuis le Wi-Fi",
                "medium",
                True,
                "Règles pare-feu illisibles: contrôle ignoré.",
            )
        exposed = any(
            line.strip().startswith("tcp dport 22 accept") for line in rules.splitlines()
        )
        detail = (
            "Tout appareil connecté au Wi-Fi peut atteindre le port 22."
            if exposed
            else "Le port 22 est refusé aux clients Wi-Fi."
        )
    return _finding(
        "ssh-exposure",
        "SSH depuis le Wi-Fi",
        "medium",
        not exposed,
        detail,
        "Relancez l’installateur avec --no-lan-ssh, ou administrez par le service onion.",
        page="settings",
    )


def _password_finding(age_seconds: int) -> dict[str, Any]:
    if not age_seconds:
        return _finding(
            "password-age",
            "Âge du mot de passe administrateur",
            "medium",
            True,
            "Date de changement inconnue.",
        )
    days = age_seconds // DAY_SECONDS
    return _finding(
        "password-age",
        "Âge du mot de passe administrateur",
        "medium",
        age_seconds < YEAR_SECONDS,
        "Changé aujourd’hui." if not days else f"Inchangé depuis {days} jour(s).",
        "Changez-le dans Paramètres: un secret d’un an a eu le temps de fuiter ailleurs.",
        page="settings",
    )


def _session_finding(ttl_seconds: int) -> dict[str, Any]:
    days = ttl_seconds / DAY_SECONDS
    return _finding(
        "session-ttl",
        "Durée des sessions",
        "low",
        ttl_seconds <= 7 * DAY_SECONDS,
        f"Une session ouverte reste valable {days:.1f} jour(s).",
        "Réduisez ONIONPI_SESSION_TTL: un cookie volé vit aussi longtemps.",
        page="settings",
    )


def _update_findings(state: dict[str, Any]) -> list[dict[str, Any]]:
    if not state.get("supported"):
        return [
            _finding(
                "updates-installed",
                "Client de mise à jour",
                "high",
                False,
                "Aucun client de mise à jour sur cette appliance.",
                "Relancez packaging/install.sh pour installer onionpi-update.",
                page="settings",
            )
        ]
    findings = [
        _finding(
            "updates-automatic",
            "Mises à jour automatiques",
            "high",
            bool(state.get("enabled")),
            f"Vérification programmée à {state.get('schedule', '—')}."
            if state.get("enabled")
            else "La minuterie de mise à jour est arrêtée.",
            "Réactivez-la dans Paramètres: les correctifs de sécurité arrivent par là.",
            page="settings",
        ),
        _finding(
            "updates-pending",
            "Version installée",
            "high",
            not state.get("update_pending"),
            f"Version {state.get('available') or '?'} disponible, "
            f"{state.get('installed') or '?'} installée."
            if state.get("update_pending")
            else f"Version {state.get('installed') or '?'}, à jour.",
            "Installez la mise à jour depuis Paramètres.",
            page="settings",
        ),
    ]
    last_check = int(state.get("last_check") or 0)
    stale = bool(last_check) and time.time() - last_check > 14 * DAY_SECONDS
    if not last_check or stale:
        findings.append(
            _finding(
                "updates-check",
                "Dernière vérification",
                "medium",
                False,
                "Aucune vérification récente: l’appliance ignore ce qui est publié."
                if not last_check
                else f"Dernière vérification il y a {(int(time.time()) - last_check) // DAY_SECONDS} jours.",
                "Lancez une vérification depuis Paramètres.",
                page="settings",
            )
        )
    return findings


def _onion_findings(onion: dict[str, Any]) -> list[dict[str, Any]]:
    if not onion.get("enabled"):
        return [
            _finding(
                "onion-exposure",
                "Accès distant par service onion",
                "low",
                True,
                "Aucun service onion publié: l’administration reste locale.",
                page="tor",
            )
        ]
    authorised = bool(onion.get("client_auth"))
    return [
        _finding(
            "onion-exposure",
            "Autorisation client du service onion",
            "medium",
            authorised,
            f"{len(onion.get('clients', []))} appareil(s) autorisé(s) par clé."
            if authorised
            else "Toute personne qui connaît l’adresse atteint la page de connexion.",
            "Ajoutez au moins un accès autorisé dans Tor: l’adresse seule ne suffira plus.",
            page="tor",
        )
    ]


def _dns_finding(dns_state: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(dns_state.get("profiles") or dns_state.get("custom_blocked"))
    updated_at = int(dns_state.get("updated_at") or 0)
    stale = enabled and updated_at and time.time() - updated_at > 30 * DAY_SECONDS
    if not enabled:
        detail = "Aucune liste active: les traqueurs sont résolus normalement."
    elif stale:
        detail = f"Listes non actualisées depuis {(int(time.time()) - updated_at) // DAY_SECONDS} jours."
    else:
        detail = f"{int(dns_state.get('domain_count') or 0)} domaines filtrés."
    return _finding(
        "dns-filter",
        "Filtrage DNS",
        "low",
        enabled and not stale,
        detail,
        "Activez ou actualisez les listes dans Protection.",
        page="protection",
    )


def _policy_findings(policy: dict[str, Any]) -> list[dict[str, Any]]:
    country = str(policy.get("exit_country", ""))
    findings = []
    if country:
        findings.append(
            _finding(
                "exit-country",
                "Pays de sortie imposé",
                "low",
                False,
                f"Toutes les sorties passent par {EXIT_COUNTRIES.get(country, country)}, "
                "ce qui réduit le groupe dans lequel ce réseau se fond.",
                "Laissez Tor choisir la sortie dès que le service qui l’exigeait est réglé.",
                page="tor",
            )
        )
    findings.append(
        _finding(
            "identity-rotation",
            "Rotation d’identité",
            "low",
            bool(policy.get("rotation_seconds")),
            "Rotation automatique active."
            if policy.get("rotation_seconds")
            else "Le circuit de sortie reste le même tant que Tor ne le change pas.",
            "Programmez une rotation dans Tor pour éviter une session de navigation continue.",
            page="tor",
        )
    )
    return findings


def _clock_finding(demo_mode: bool) -> dict[str, Any]:
    if demo_mode:
        synchronised = True
    else:
        synchronised = (
            _run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]) == "yes"
        )
    return _finding(
        "clock",
        "Horloge système",
        "high",
        synchronised,
        "Synchronisée par NTP."
        if synchronised
        else "Non synchronisée: Tor rejette les certificats des relais avec une horloge fausse.",
        "Vérifiez systemd-timesyncd et le lien amont.",
        page="settings",
    )


def _storage_finding(services: AppServices) -> dict[str, Any]:
    settings = services.settings
    try:
        free = (
            29_200_000_000
            if settings.demo_mode
            else shutil.disk_usage(settings.data_dir).free
        )
    except OSError:
        return _finding("storage", "Stockage", "medium", True, "Lecture impossible.")
    comfortable = free > settings.storage_reserve_bytes * 2
    return _finding(
        "storage",
        "Stockage disponible",
        "medium",
        comfortable,
        f"{free / 1024**3:.1f} Gio libres.",
        "Libérez de l’espace: une mise à jour sans place s’arrête à mi-chemin.",
        page="files",
    )


def _certificate_finding(services: AppServices) -> dict[str, Any] | None:
    settings = services.settings
    if settings.demo_mode:
        return None
    try:
        certificate = x509.load_pem_x509_certificate(
            settings.tls_certificate_path.read_bytes()
        )
        remaining = certificate.not_valid_after_utc.timestamp() - time.time()
    except Exception:  # unreadable or not a certificate: nothing to report
        return None
    days = int(remaining // DAY_SECONDS)
    return _finding(
        "tls-certificate",
        "Certificat du réseau local",
        "medium",
        remaining > 30 * DAY_SECONDS,
        f"Expire dans {days} jour(s)." if days >= 0 else "Certificat expiré.",
        "Relancez l’installateur en mode mise à niveau pour en régénérer un.",
        page="settings",
    )


def security_audit(services: AppServices, protection: dict[str, Any]) -> dict[str, Any]:
    """Builds the full report from the state the managers already hold."""
    settings = services.settings
    findings: list[dict[str, Any]] = [
        _demo_finding(settings.demo_mode),
        _agent_finding(services.agent.available),
        _protection_finding(protection),
        _ssh_finding(services),
        _password_finding(services.database.oldest_password_age()),
        _session_finding(settings.session_ttl_seconds),
        _clock_finding(settings.demo_mode),
        _storage_finding(services),
        _dns_finding(services.dns_filter.state()),
    ]
    update_state = (
        services.updates.demo_state() if settings.demo_mode else services.updates.state()
    )
    findings.extend(_update_findings(update_state))
    findings.extend(_onion_findings(services.onion.snapshot()))
    findings.extend(_policy_findings(services.tor_policy.state()))
    certificate = _certificate_finding(services)
    if certificate:
        findings.append(certificate)

    score = 100
    counts = {severity: 0 for severity in SEVERITY_WEIGHTS}
    for finding in findings:
        if finding["ok"]:
            continue
        counts[finding["severity"]] += 1
        score -= SEVERITY_WEIGHTS[finding["severity"]]
    score = max(0, min(100, score))
    level, label = next(
        (name, text) for threshold, name, text in LEVELS if score >= threshold
    )
    pending = [finding for finding in findings if not finding["ok"]]
    # Worst first: the list is a work order, not an inventory.
    order = list(SEVERITY_WEIGHTS)
    findings.sort(key=lambda item: (item["ok"], order.index(item["severity"])))
    return {
        "generated_at": int(time.time()),
        "score": score,
        "level": level,
        "label": label,
        "summary": (
            "Aucun point faible détecté."
            if not pending
            else f"{len(pending)} point(s) à corriger, dont "
            f"{counts['critical'] + counts['high']} prioritaire(s)."
        ),
        "counts": {**counts, "total": len(findings), "pending": len(pending)},
        "findings": findings,
    }
