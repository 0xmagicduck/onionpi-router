# Audit sécurité du 13 août 2026

## Résultat exécutif

Audit statique complet du dépôt à la version 0.4.0 : les 43 modules Python, les
16 scripts shell de `packaging/` et `scripts/`, les 13 unités systemd, les
gabarits nftables, nginx, dnsmasq et torrc, les deux workflows GitHub et la
surface HTTP (58 routes) ont été relus. Six problèmes ont été relevés :
un moyen-haut, trois moyens et deux faibles. Les six sont corrigés dans cette
branche, chacun avec un test de non-régression qui échoue sans son correctif.

| Sujet | Gravité | État |
| --- | --- | --- |
| En-tête `X-CSRF-Token` non ASCII : exception au lieu d’un refus | Moyen-haut | Corrigé |
| Champ de formulaire d’import accumulé en mémoire sans plafond utile | Moyen | Corrigé |
| Réserve de stockage consultée après la mise en cache du corps | Moyen | Corrigé |
| Dérivation scrypt des sauvegardes hors du plafond mémoire du processus | Moyen | Corrigé |
| Reconstructions simultanées des listes DNS non sérialisées | Faible | Corrigé |
| Récupération de compte créant un second administrateur | Faible | Corrigé |

Aucun chemin d’élévation du compte `onionpi` vers root n’a été trouvé au-delà
de celui que l’architecture assume et documente (les deux fragments torrc que
l’application possède). Les contrôles déjà en place — allow-list de verbes dans
`onionpi-agent-apply`, vérification OpenPGP avant toute mise à niveau,
transaction nftables unique, réécriture de l’adresse transmise par nginx — ont
été revérifiés et tiennent.

## Constats

### 1. Un en-tête CSRF contenant un octet brut renvoyait 500 (moyen-haut)

Starlette décode les en-têtes en latin-1 : un client choisit chacun de leurs
octets. `secrets.compare_digest` refuse les chaînes `str` non ASCII et lève
`TypeError`. Un seul caractère accentué dans `X-CSRF-Token` transformait donc
le refus attendu en 500, avec une trace complète dans le journal — et une
réponse 500 échappe au middleware qui pose les en-têtes de sécurité, donc sans
`X-Frame-Options` ni CSP.

Rien n’était accordé par cette voie : la requête était bien avortée. Mais une
exception dans une dépendance de sécurité est exactement ce qui devient un
échec ouvert au refactor suivant, et c’est une source gratuite de bruit dans un
journal que l’opérateur doit pouvoir lire.

*Correctif :* `routes/context.py` compare désormais les formes encodées. Une
chaîne issue de latin-1 s’encode toujours, et aucune séquence hors de
l’alphabet base64 URL-safe ne peut égaler un jeton.

### 2. Le champ texte du formulaire d’import n’avait pas de plafond utile (moyen)

`request.form(..., max_part_size=settings.max_upload_bytes)` semblait borner
l’import à 1 Gio. En réalité `max_part_size` ne gouverne que les parties
*texte* : une partie fichier est écrite dans un fichier temporaire à débordement
et n’est jamais mesurée contre ce plafond. Le seul champ concerné était donc
`path` — une chaîne bornée à 500 caractères une fois arrivée au gestionnaire,
mais accumulée avant cela dans un `bytearray` jusqu’à un gigaoctet.

Toute personne disposant d’une session pouvait ainsi envoyer `path=<1 Gio>` et
faire dépasser au service le `MemoryMax=384M` de son unité systemd : le foyer
perd son interface d’administration jusqu’au redémarrage automatique.

*Correctif :* `max_part_size` vaut 64 Kio, la taille du seul champ texte que ce
formulaire transporte.

### 3. La réserve de stockage arrivait trop tard (moyen)

`storage_reserve_bytes` existe pour empêcher un import de remplir la carte SD.
Le contrôle était placé après `await request.form(...)`, c’est-à-dire après que
l’analyseur multipart a déjà écrit l’intégralité du corps sur le disque : au
moment où le 507 partait, la place était déjà prise. La copie temporaire et le
fichier final coexistent en outre sur le même système de fichiers, donc le pic
réel valait deux fois la taille de l’import.

*Correctif :* le budget est calculé avant toute lecture du corps, divisé par
deux pour tenir compte de cette coexistence, et une longueur annoncée
au-dessus du budget est refusée sans rien mettre en cache. Comme un corps peut
n’annoncer aucune longueur, `BodyLimitMiddleware` accepte désormais une limite
d’import réévaluée à chaque requête : c’est la seule couche qui voit un envoi
qui ne s’arrête jamais.

### 4. Les sauvegardes chiffrées dérivaient leur clé hors du plafond mémoire (moyen)

`auth.hashing_slot` borne à quatre le nombre de calculs scrypt simultanés dans
le processus, précisément parce qu’un seul tient 16 Mio. Son commentaire
annonce couvrir « les futurs appels qui ne passent pas par ce limiteur » —
`backup.py` était justement resté en dehors. Les trois routes de sauvegarde
(`/system/backup`, `/system/backup/preview`, `/system/backup/restore`) sont
ouvertes à toute session, et FastAPI sert les fonctions synchrones sur un pool
de 40 threads : quelques dizaines d’appels parallèles franchissaient le
`MemoryMax` de l’unité.

*Correctif :* la dérivation passe par `hashing_slot`, et les trois routes
répondent 429 plutôt que d’attendre indéfiniment.

### 5. Les reconstructions des listes DNS n’étaient pas sérialisées (faible)

`DnsFilter` tenait déjà un drapeau `_refreshing`, mais seulement pour l’afficher :
rien n’empêchait plusieurs reconstructions de tourner ensemble. Chacune
télécharge jusqu’à quatre listes et garde jusqu’à 300 000 noms en mémoire avant
de les écrire, ce qu’une Raspberry Pi supporte une fois et pas quatre. Deux
d’entre elles se disputent en outre l’écriture de `block.hosts` et le
rechargement demandé ensuite à l’agent : la liste du perdant cesse
silencieusement d’être celle que dnsmasq sert.

*Correctif :* une reconstruction à la fois pour toute l’appliance ;
`POST /api/v1/dns-filter` et `POST /api/v1/dns-filter/refresh` répondent 409
lorsqu’une autre est en cours.

### 6. La récupération de compte pouvait créer un second administrateur (faible)

`POST /api/v1/auth/recover` écrivait `create_user("admin", "Administrateur", …)`
en dur. Sur une appliance dont le compte a été créé sous un autre nom — ce que
`onionpi-admin create-admin --username` permet — la récupération n’aurait pas
remplacé le mot de passe existant : elle aurait ajouté un compte à côté, en
laissant valides l’ancien mot de passe et les sessions ouvertes de la personne
que la manœuvre visait justement à exclure.

*Correctif :* la récupération réinitialise le compte que l’appliance possède
déjà, et révoque toutes les sessions ainsi que tous les WebSockets, quel que
soit le compte qui les détient.

## Ce qui a été vérifié sans donner lieu à correctif

- **Frontière privilégiée.** `agent.request`, `relay.state`, `blocked-macs.txt`
  et `update.settings.json` sont traités comme hostiles côté root : verbe
  validé contre une allow-list propre, aucun argument repris du fichier,
  réponse écrite dans un espace que l’application ne peut pas renommer. Un lien
  symbolique déposé à la place de `agent.request` ne fait rien fuiter : le
  contenu lu n’est jamais renvoyé.
- **Chaîne de mise à jour.** SHA256SUMS signé vérifié par `gpgv` avant l’archive,
  empreinte de l’archive vérifiée avant l’extraction, hôte de téléchargement
  restreint à `github.com`/`githubusercontent.com` (`github.com@evil`,
  `github.com.evil.com` et `evil-github.com` sont bien rejetés), version
  refusée si elle ne correspond pas au `VERSION` embarqué, `--no-same-owner` à
  l’extraction, journal root pour reprendre une installation interrompue.
- **Traversée de chemin.** `safe_path` et le repli SPA résolvent puis comparent
  par `commonpath`; un chemin absolu injecté dans `Path.__truediv__` remplace
  bien la base mais reste attrapé par cette comparaison.
- **Injection dans torrc.** `validate_bridge_line` refuse les retours à la
  ligne, le non-ASCII et tout ce qui ne correspond pas à une ligne de pont, y
  compris sur les ponts rapportés par le service moat.
- **Coupe-circuit.** `table inet` couvre IPv4 et IPv6, la chaîne `forward`
  laisse tomber les deux sens, `protect_local` ferme par défaut les ports du
  Pi aux clients Wi-Fi, et le remplacement des règles est une transaction
  nftables unique dont `onionpi-ap` dépend.
- **Interface web.** Aucune route non authentifiée hors `/api/v1/health`, aucun
  puits XSS dans le frontend, `shellcheck --severity=warning` propre sur les
  16 scripts.

## Validation

`./scripts/check.sh` passe en entier : cohérence des versions, absence de
secrets, budget Raspberry Pi, contrat OpenPGP/OpenAPI inchangé, `ruff`,
146 tests backend, `tsc`, `vite build`, exécution d’une version publiée, mise à
niveau non interactive, matrice de 12 interruptions et `shellcheck`.

La qualification matérielle Bookworm/Trixie décrite dans
[`security-audit-2026-08-11.md`](security-audit-2026-08-11.md) reste requise :
aucun des correctifs ci-dessus ne touche au pare-feu, à l’acheminement Tor ni
au chemin de mise à jour.
