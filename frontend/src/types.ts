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

export type Device = {
  name: string
  ip: string
  mac: string
  download: number
  upload: number
  online: boolean
  blocked?: boolean
}

export type BlockedDevice = {
  mac: string
  label: string
  blocked_at: number
}

export type DevicesPayload = {
  devices: Device[]
  blocked: BlockedDevice[]
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

export type OnionState = {
  enabled: boolean
  published: boolean
  address: string
  has_key: boolean
  target: string
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

export type ChatMessage = {
  id: number
  author: string
  body: string
  created_at: number
}
import type { StatusResponse as GeneratedStatusResponse } from './generated/api-v1'
