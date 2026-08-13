export type User = {
  username: string
  display_name: string
}

export type Session = {
  user: User
  csrf: string
}

export type TorNode = {
  role: string
  name: string
}

export type StatusPayload = GeneratedStatusResponse

export type BridgeState = {
  use_bridges: boolean
  transport: string | null
  bridge_count: number
  known: boolean
}

export type ConnectionMode = 'direct' | 'auto' | 'manual'

export type TransportInfo = {
  id: string
  label: string
  description: string
  available: boolean
  binary: string
}

export type RelayStatistics = {
  connections: number
  downloaded: number
  uploaded: number
  periods: number
}

export type RelayState = {
  installed: boolean
  enabled: boolean
  active: boolean
  controllable: boolean
  statistics: RelayStatistics | null
}

export type CircumventionPayload = {
  mode: ConnectionMode
  transport: string
  country: string
  custom_bridges: string[]
  auto_transport: string
  updated_at: number
  censored_country: boolean
  recommended: string[]
  known_countries: string[]
  catalog: { source: string; snapshot_at: number; counts: Record<string, number> }
  stalled_seconds: number
  transports: TransportInfo[]
  relay: RelayState
}

export type Activity = {
  id: number
  kind: string
  message: string
  created_at: number
}

export type TrafficSample = {
  timestamp: number
  download_mbps: number
  upload_mbps: number
}

/** `allowed` and `blocked` come from the firewall, the two others from the
 *  access schedule: a device can be reachable and still be outside its hours. */
export type AccessState = 'allowed' | 'paused' | 'outside' | 'blocked'

export type DeviceSchedule = {
  enabled: boolean
  /** 0 = lundi, 6 = dimanche, as the backend counts weekdays. */
  days: number[]
  start: string
  end: string
}

export type Device = {
  name: string
  ip: string
  mac: string
  download: number
  upload: number
  online: boolean
  blocked?: boolean
  alias?: string
  access_state?: AccessState
  paused_until?: number
  schedule?: DeviceSchedule | null
}

export type BlockedDevice = {
  mac: string
  label: string
  blocked_at: number
}

export type DeviceAccessRule = {
  mac: string
  alias: string
  paused_until: number
  schedule: DeviceSchedule | null
  state: AccessState
  manually_blocked: boolean
}

export type DeviceAccessState = {
  rules: DeviceAccessRule[]
  weekdays: string[]
  pause_choices: number[]
  now: number
}

/** State of the per-device byte counters, not of any one device. */
export type DeviceTraffic = {
  /** False while the firewall counters have never been sampled. */
  supported: boolean
  /** When the totals started being accumulated. */
  since: number
  /** Last sampling of the counters by the privileged timer. */
  updated_at: number
  devices: number
}

export type DevicesPayload = {
  devices: Device[]
  blocked: BlockedDevice[]
  access: DeviceAccessState
  traffic?: DeviceTraffic
}

export type DnsProfile = {
  id: string
  label: string
  description: string
}

export type DnsFilterState = {
  profiles: string[]
  custom_blocked: string[]
  allowed: string[]
  domain_count: number
  updated_at: number
  last_error: string
  enabled: boolean
  refreshing: boolean
  available_profiles: DnsProfile[]
}

export type TorPolicyState = {
  exit_country: string
  rotation_seconds: number
  last_rotation: number
  next_rotation: number
  countries: Array<{ code: string; name: string }>
  rotation_choices: number[]
}

export type TorCircuit = {
  id: string
  purpose: string
  nodes: TorNode[]
}

export type OnionClient = {
  name: string
  added_at: number
}

export type OnionState = {
  enabled: boolean
  published: boolean
  address: string
  has_key: boolean
  target: string
  client_auth: boolean
  clients: OnionClient[]
  max_clients: number
}

/** Returned once, when the access is created. The key is never stored in clear
 *  and no endpoint hands it out a second time. */
export type OnionClientCreated = {
  onion: OnionState
  name: string
  private_key: string
}

export type TorAdvancedPayload = {
  policy: TorPolicyState
  circuits: TorCircuit[]
  onion: OnionState
}

export type SpeedTestResult = {
  download_mbps: number
  latency_ms: number
  bytes: number
  seconds: number
}

export type SystemAction = {
  id: string
  label: string
}

export type SystemActionsPayload = {
  available: boolean
  actions: SystemAction[]
}

export type DiagnosticStatus = 'ok' | 'warning' | 'error'

export type DiagnosticCheck = {
  id: string
  label: string
  status: DiagnosticStatus
  detail: string
  remedy: string
}

export type DiagnosticsPayload = {
  generated_at: number
  status: DiagnosticStatus
  checks: DiagnosticCheck[]
  database: {
    users: number
    sessions: number
    messages: number
    activity: number
    settings: number
    bytes: number
  }
}

export type AuditSeverity = 'critical' | 'high' | 'medium' | 'low'

export type AuditFinding = {
  id: string
  title: string
  severity: AuditSeverity
  ok: boolean
  detail: string
  remedy: string
  /** Page of the interface that owns the setting, when there is one. */
  page: string
  /** Privileged verb that repairs the finding without leaving the report. */
  action: string
}

export type SecurityAudit = {
  generated_at: number
  score: number
  level: 'excellent' | 'solide' | 'renforcer' | 'faible'
  label: string
  summary: string
  counts: Record<AuditSeverity | 'total' | 'pending', number>
  findings: AuditFinding[]
}

export type UpdateChannel = 'stable' | 'edge'

export type OnboardingState = {
  complete: boolean
  steps: {
    password: boolean
    interfaces: boolean
    firewall: boolean
    clock: boolean
    recovery: boolean
  }
  interfaces: { wan: string; access_point: string }
  clock_synchronised: boolean
  maintenance: { active: boolean; expires_at: number | null; source: string }
}

export type BackupEnvelope = {
  schema: string
  created_at: number
  kdf: { name: string; n: number; r: number; p: number; salt: string }
  cipher: { name: string; nonce: string }
  payload: string
}

export type BackupPreview = {
  valid: boolean
  document_version: number
  changes: Array<{ section: string; before: unknown; after: unknown }>
}

export type UpdateState = {
  /** False when the update client is not installed on this appliance. */
  supported: boolean
  installed: string
  available: string
  update_pending: boolean
  running: boolean
  repository: string
  over_tor: boolean
  channel: UpdateChannel
  /** One or more "HH:MM" separated by commas. */
  schedule: string
  enabled: boolean
  auto_apply: boolean
  next_run: number | null
  last_check: number | null
  last_check_status: string
  last_check_message: string
  last_apply: number | null
  last_apply_status: string
  last_apply_message: string
  history: string[]
  message?: string
  warning?: string
}

export type SharedFile = {
  name: string
  path: string
  is_directory: boolean
  size: number
  modified_at: number
  mime: string
}

export type FilesPayload = {
  path: string
  items: SharedFile[]
  storage: { used: number; total: number; free: number }
}

/** `pending` = un nœud distant sans adresse onion, donc jamais interrogé.
 *  `unknown` = une adresse connue mais aucune réponse encore reçue. */
export type RackNodeStatus = 'online' | 'offline' | 'isolated' | 'pending' | 'unknown'

export type RackEgress = 'tor-only' | 'direct'

/** Une redirection locale vers un pair, comme `ssh -L`. C’est le mode par
 *  défaut du plan de données: un flux onion est du TCP, et y faire passer de
 *  l’IP empilerait deux contrôles de congestion. */
export type RackMeshForward = { listen: number; node: string; port: number }

export type RackMeshRules = {
  enabled: boolean
  /** Ports que ce nœud accepte de présenter à ses pairs. */
  ports: number[]
  forwards: RackMeshForward[]
}

export type RackNodeRules = {
  access: 'allowed' | 'blocked'
  egress: RackEgress
  exit_country: string
  /** Ports laissés joignables en entrée. Le 22 y est par défaut. */
  keep_open_ports: number[]
  schedule: DeviceSchedule | null
  mesh: RackMeshRules
}

/** Dernière lecture renvoyée par l’agent du nœud. Vide tant qu’il n’a pas
 *  répondu: aucun champ n’est garanti. */
export type RackNodeState = {
  agent_version?: string
  hostname?: string
  uptime_seconds?: number
  load?: number
  memory_percent?: number
  storage_percent?: number
  tor?: { connected: boolean; bootstrap: number; summary?: string; exit_country?: string }
  policy?: { digest: string; egress: string; applied_at: number }
  services?: Array<{ id: string; label: string; active: boolean }>
  platform?: { system: string; release: string; machine: string; policy_mode: string }
  /** Ce que le nœud annonce de son identité de maillage. Les moitiés publiques
   *  seulement: la clé est engendrée sur le nœud et n’en sort jamais. */
  mesh?: {
    identity?: string
    static?: string
    address?: string
    port?: number
    direct?: string
    netmap_serial?: number
    peers?: number
    sessions?: number
    locked?: boolean
  }
  netmap?: { digest: string; serial: number; issued_at: number; peers: number; forwards: number }
}

export type RackAlert = { level: 'info' | 'warning' | 'danger'; message: string }

/** Ce que le Wi-Fi dit d’un nœud local. Absent pour une machine distante. */
export type RackLink = { ip: string; online: boolean; download: number; upload: number }

export type RackNode = {
  id: string
  rack_id: string
  /** 0 = hors baie: le nœud existe et garde ses règles, sans emplacement. */
  position: number
  kind: 'local' | 'remote'
  name: string
  role: string
  mac: string
  onion: string
  address: string
  agent_port: number
  token_epoch: number
  client_auth: boolean
  notes: string
  rules: RackNodeRules
  state: RackNodeState
  last_seen: number
  last_error: string
  created_at: number
  updated_at: number
  /** Empreinte de la politique voulue. Comparée à celle que le nœud applique. */
  policy_digest: string
  status: RackNodeStatus
  link: RackLink | null
  alerts: RackAlert[]
  /** Identité de maillage annoncée par le nœud, jamais dérivée ici. */
  mesh_identity: string
  mesh_static: string
  mesh_static_signature: string
  /** Adresse `fd7a:…` déduite de la clé d’identité: rien à attribuer. */
  mesh_address: string
  mesh_endorsements: Record<string, string>
  netmap_serial: number
  /** Adresse `10.43.X.Y` du lien radio, quand le nœud en a une. */
  mesh_v4: string
}

export type RackFrame = {
  id: string
  name: string
  location: string
  units: number
  created_at: number
  occupied: number
  alerts: number
}

export type RackProfile = {
  id: string
  name: string
  rules: RackNodeRules
  created_at: number
  updated_at: number
}

export type RackCableColor = 'amber' | 'cyan' | 'violet' | 'green'
export type RackCableStatus = 'online' | 'warning' | 'offline'

/** A logical patch lead between two faceplate ports. It documents topology;
 *  enforcement remains owned by each node's rule sheet. */
export type RackCable = {
  id: string
  rack_id: string
  source_node_id: string
  source_port: number
  source_name: string
  target_node_id: string
  target_port: number
  target_name: string
  label: string
  color: RackCableColor
  speed: '100-mbps' | '1-gbps' | '10-gbps'
  status: RackCableStatus
  created_at: number
  updated_at: number
}

/** Un client du Wi-Fi qui n’est pas encore un nœud de la baie. */
export type RackDiscovered = { mac: string; name: string; ip: string; online: boolean }

export type RackPayload = {
  racks: RackFrame[]
  nodes: RackNode[]
  cables: RackCable[]
  profiles: RackProfile[]
  discovered: RackDiscovered[]
  health: { warnings: number; failures: number }
  limits: {
    max_racks: number
    max_nodes: number
    max_units: number
    default_units: number
    max_profiles: number
    max_cables: number
  }
  verbs: Array<{ id: string; label: string }>
  egress_modes: RackEgress[]
  mesh: RackMesh
  now: number
}

export type RackMeshLock = { enabled: boolean; threshold: number; trustees: string[] }

export type RackMeshMember = {
  id: string
  name: string
  enabled: boolean
  identity: string
  address: string
  direct: string
  ports: number[]
  forwards: RackMeshForward[]
  endorsed: number
  /** Faux quand il manque une clé, une adresse onion ou l’activation: le nœud
   *  n’est alors dans aucune carte, et c’est visible. */
  in_map: boolean
  netmap_serial: number
  netmap_peers: number
  netmap_issued_at: number
}

export type RackMesh = {
  coordinator: string
  lock: RackMeshLock
  revoked: string[]
  members: RackMeshMember[]
  mesh_port: number
  limits: { max_ports: number; max_forwards: number }
}

export type RackEndorsementRequest = {
  node_id: string
  name: string
  identity: string
  /** Le message exact qu’un garant signe, en hexadécimal. */
  message: string
  command: string
  lock: RackMeshLock
}

export type RackBulkAnswer = {
  snapshot: RackPayload
  applied: number
  failures: Array<{ id: string; name: string; message: string }>
}

export type RackSample = {
  at: number
  reachable: number
  load: number
  memory_percent: number
  storage_percent: number
  bootstrap: number
}

export type RackHistory = {
  node_id: string
  name: string
  window: number
  samples: RackSample[]
  readings: number
  /** Part des sondages ayant obtenu une réponse. `null` avant tout sondage. */
  availability: number | null
}

/** Rendu à la demande, jamais stocké: le jeton est dérivé du secret de baie. */
export type RackEnrollment = {
  node_id: string
  name: string
  token: string
  token_epoch: number
  agent_port: number
  client_public_key: string
  /** Empreinte de l’agent que la baie exécute, vide sans copie de référence. */
  bundle_digest: string
  onion: string
  command: string
  commands: {
    linux: string
    macos: string
    windows: string
  }
}

export type RackJournal = { unit: string; lines: string[] }

export type ChatMessage = {
  id: number
  author: string
  body: string
  created_at: number
}
import type { StatusResponse as GeneratedStatusResponse } from './generated/api-v1'
