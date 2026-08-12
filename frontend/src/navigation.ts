import {
  Folder,
  Gauge,
  HardDrive,
  Logs,
  MessageCircle,
  Settings,
  Shield,
  ShieldBan,
  ShieldCheck,
  Waypoints,
  Wifi,
  type LucideIcon,
} from 'lucide-react'

export type Page =
  | 'dashboard'
  | 'network'
  | 'tor'
  | 'bridges'
  | 'devices'
  | 'protection'
  | 'security'
  | 'files'
  | 'chat'
  | 'logs'
  | 'settings'

export type NavItem = {
  id: Page
  label: string
  icon: LucideIcon
  /** Shown in the command palette, and as the page subtitle prompt. */
  hint: string
  /** Extra search terms, so « pare-feu » finds the protection screen. */
  keywords: string
}

export type NavGroup = { label: string; items: NavItem[] }

export const NAV_GROUPS: NavGroup[] = [
  {
    label: 'Supervision',
    items: [
      { id: 'dashboard', label: 'Vue d’ensemble', icon: Gauge, hint: 'État Tor, trafic et appareils', keywords: 'accueil tableau de bord resume' },
      { id: 'protection', label: 'Protection', icon: ShieldBan, hint: 'Contrôles de sécurité et filtrage DNS', keywords: 'pare-feu firewall dns blocage bloquer' },
      { id: 'security', label: 'Audit', icon: ShieldCheck, hint: 'Points faibles de la configuration et correctifs', keywords: 'audit durcissement score securite exposition ssh mise a jour' },
    ],
  },
  {
    label: 'Réseau et Tor',
    items: [
      { id: 'network', label: 'Réseau', icon: Wifi, hint: 'Point d’accès Wi-Fi et lien amont', keywords: 'wifi ssid passerelle dhcp' },
      { id: 'tor', label: 'Tor', icon: Shield, hint: 'Circuits, identité de sortie, service onion', keywords: 'circuit identite oignon onion relais' },
      { id: 'bridges', label: 'Contournement', icon: Waypoints, hint: 'Ponts, transports enfichables, Snowflake', keywords: 'pont bridge obfs4 snowflake censure' },
      { id: 'devices', label: 'Appareils', icon: HardDrive, hint: 'Clients connectés, pauses et plages horaires', keywords: 'clients mac ip telephone ordinateur pause horaire plage couvre-feu' },
    ],
  },
  {
    label: 'Partage',
    items: [
      { id: 'files', label: 'Fichiers', icon: Folder, hint: 'Espace de fichiers du réseau local', keywords: 'documents televerser importer dossier' },
      { id: 'chat', label: 'Chat', icon: MessageCircle, hint: 'Messages entre personnes connectées', keywords: 'messages discussion' },
    ],
  },
  {
    label: 'Système',
    items: [
      { id: 'logs', label: 'Journaux', icon: Logs, hint: 'Événements récents des services', keywords: 'logs journal systemd erreurs' },
      { id: 'settings', label: 'Paramètres', icon: Settings, hint: 'Mises à jour, maintenance, sauvegarde', keywords: 'reglages update sauvegarde mot de passe redemarrer' },
    ],
  },
]

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items)
export const PAGE_IDS: Page[] = NAV_ITEMS.map((item) => item.id)

export function pageTitle(page: Page): string {
  return NAV_ITEMS.find((item) => item.id === page)?.label ?? 'OnionPi'
}
