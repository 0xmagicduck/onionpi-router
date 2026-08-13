# Sécurité

OnionPi route le trafic de vrais appareils à travers Tor. Une faille ici ne
casse pas seulement un logiciel : elle peut révéler l’adresse IP de personnes
qui comptaient sur l’anonymat.

## Signaler une faille

N’ouvrez pas d’issue publique. Utilisez l’onglet **Security → Report a
vulnerability** du dépôt (avis de sécurité privé GitHub).

Décrivez ce que vous avez observé, la version (`onionpi-status` ou le contenu
de `/opt/onionpi/VERSION`) et, si possible, les étapes de reproduction.
Réponse sous une semaine ; correction publiée sur le canal `stable` et
annoncée dans `CHANGELOG.md`.

## Versions prises en charge

La dernière version publiée. Il n’y a pas de branche de maintenance : la mise
à jour automatique existe précisément pour que la version installée soit celle
qui reçoit les correctifs.

## Ce qui compte comme une faille ici

- fuite de trafic client hors de Tor (UDP, IPv6, DNS direct, route parallèle) ;
- élévation de privilèges depuis l’interface web vers root, y compris par la
  file d’attente `agent.request` ou par `update.settings.json` ;
- installation d’une archive de mise à jour non vérifiée, ou contournement du
  contrôle SHA-256 ou de la signature OpenPGP
  (`FD4DC3B7A6C94E1F3B2F130A99EFBC5B082A1AB8`) ;
- lecture ou écriture hors de `/var/lib/onionpi/shared` par l’API de fichiers ;
- contournement de l’authentification, de la protection CSRF ou de la
  limitation des tentatives de connexion ;
- exposition d’un secret : PSK Wi-Fi, condensat administrateur, clé du service
  onion, cookie de session, jeton d’un nœud de baie ;
- contournement du protocole d’agent de baie : appel accepté sans signature
  valide, rejeu dans la fenêtre d’horodatage, ou **réponse acceptée par la baie
  sans signature du nœud** — le circuit onion authentifie le service, pas
  l’agent, et une lecture forgée pilote alertes, historique et repoussée des
  règles ;
- installation d’un agent de nœud dont l’archive ne correspond pas à
  l’empreinte annoncée par l’appliance.

## Ce qui n’en est pas

- le certificat TLS auto-signé de `https://onionpi.local` : l’appareil n’a pas
  de nom de domaine public, l’empreinte se vérifie depuis la Pi ;
- l’adresse `.onion` du service d’administration : elle vaut un mot de passe et
  c’est documenté ;
- les ponts intégrés bloqués dans un pays donné : ils sont publics par nature ;
- le fait qu’un porteur de la carte SD puisse extraire le PSK Wi-Fi : c’est
  écrit dans le README, une carte perdue est un identifiant compromis ;
- Tor lui-même, obfs4proxy ou snowflake : signalez-les au Tor Project.

## Modèle de confiance interne

Trois frontières, dans cet ordre :

1. **Le client Wi-Fi ne peut pas atteindre Internet directement.** nftables
   coupe tout ce qui n’est pas redirigé vers le `TransPort`, et l’unité du point
   d’accès dépend de l’unité du coupe-circuit.
2. **L’application web ne peut pas devenir root.** `NoNewPrivileges=true`,
   `ProtectSystem=strict`, et trois chemins seulement en écriture. Toute action
   privilégiée passe par un fichier de requête relu et revalidé par
   `onionpi-agent-apply`, qui n’accepte que des verbes de sa propre liste et ne
   lit jamais d’argument dans le fichier. Les réponses root vivent sous
   `/var/lib/onionpi-privileged`, dont l’application ne peut pas renommer les
   entrées.
3. **Le client de mise à jour ne fait confiance à rien.** Les préférences
   venues de l’interface sont revalidées champ par champ, l’archive n’est
   téléchargée qu’après validation du manifeste signé, sa taille est bornée,
   elle n’est dépliée qu’après contrôle de son empreinte, et `/opt/onionpi` est
   copié avant toute écriture pour permettre un retour arrière automatique.

## Limites connues

- **L’installation de dépendances n’est pas encore hermétique.** Une mise à
  niveau réexécute l’installateur et peut consulter APT/PyPI après que l’archive
  OnionPi a été signée. Le wheelhouse signé et le déploiement immuable prévus
  dans `docs/product-roadmap.md` doivent fermer cette frontière.

- **L’adresse du client n’est sûre que derrière nginx.** nginx réécrit
  `X-Forwarded-For` depuis `$remote_addr`, donc un client Wi-Fi ne peut pas
  choisir l’adresse sur laquelle la limitation de connexion le compte. Le
  service onion, lui, atteint l’application directement : ses en-têtes sont
  écrits par le visiteur. C’est pourquoi la limitation comporte aussi un
  plafond global, indépendant de l’adresse annoncée. Publier le service onion
  reste un choix : son adresse vaut un mot de passe.
- **L’application peut tout demander au port de contrôle de Tor.** Elle est
  membre du groupe `debian-tor` pour lire le cookie d’authentification ; une
  exécution de code arbitraire dans l’application donnerait donc le contrôle de
  la configuration de Tor, sans pour autant donner root sur la machine.
