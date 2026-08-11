import type {
  BlockedDevice,
  CircumventionPayload,
  ConnectionMode,
  DevicesPayload,
  DnsFilterState,
  FilesPayload,
  OnionState,
  Session,
  SpeedTestResult,
  StatusPayload,
  SystemActionsPayload,
  TorAdvancedPayload,
  TorPolicyState,
  TrafficSample,
  UpdateChannel,
  UpdateState,
} from './types'

let csrfToken = ''

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export function setCsrf(token: string) {
  csrfToken = token
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (init.method && init.method !== 'GET') headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new ApiError(payload.detail || 'Une erreur est survenue', response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  async login(username: string, password: string): Promise<Session> {
    const session = await request<Session>('/api/v1/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    setCsrf(session.csrf)
    return session
  },
  async session(): Promise<Session> {
    const session = await request<Session>('/api/v1/auth/session')
    setCsrf(session.csrf)
    return session
  },
  logout: () => request<void>('/api/v1/auth/logout', { method: 'POST' }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ ok: boolean; message: string }>('/api/v1/auth/password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
  status: () => request<StatusPayload>('/api/v1/status'),
  traffic: async () => (await request<{ samples: TrafficSample[] }>('/api/v1/traffic')).samples,
  devices: async () => (await request<DevicesPayload>('/api/v1/devices')).devices,
  devicesPayload: () => request<DevicesPayload>('/api/v1/devices'),
  setDeviceBlocked: (mac: string, blocked: boolean, label = '') =>
    request<{ blocked: BlockedDevice[] }>('/api/v1/devices/block', {
      method: 'POST',
      body: JSON.stringify({ mac, blocked, label }),
    }),
  dnsFilter: () => request<DnsFilterState>('/api/v1/dns-filter'),
  setDnsFilter: (body: { profiles: string[]; custom_blocked: string[]; allowed: string[] }) =>
    request<DnsFilterState>('/api/v1/dns-filter', { method: 'POST', body: JSON.stringify(body) }),
  refreshDnsFilter: () => request<DnsFilterState>('/api/v1/dns-filter/refresh', { method: 'POST' }),
  torAdvanced: () => request<TorAdvancedPayload>('/api/v1/tor/advanced'),
  setTorPolicy: (body: { exit_country: string; rotation_seconds: number }) =>
    request<TorPolicyState>('/api/v1/tor/policy', { method: 'POST', body: JSON.stringify(body) }),
  speedTest: () => request<SpeedTestResult>('/api/v1/tor/speedtest', { method: 'POST' }),
  setOnion: (enabled: boolean) =>
    request<OnionState>('/api/v1/onion', { method: 'POST', body: JSON.stringify({ enabled }) }),
  rotateOnion: () => request<OnionState>('/api/v1/onion/rotate', { method: 'POST' }),
  systemActions: () => request<SystemActionsPayload>('/api/v1/system/actions'),
  runSystemAction: (action: string) =>
    request<{ action: string; status: string; message: string }>('/api/v1/system/action', {
      method: 'POST',
      body: JSON.stringify({ action }),
    }),
  updateState: () => request<UpdateState>('/api/v1/system/update'),
  saveUpdateSettings: (body: {
    channel: UpdateChannel
    schedule: string
    enabled: boolean
    apply: boolean
  }) => request<UpdateState>('/api/v1/system/update/settings', { method: 'POST', body: JSON.stringify(body) }),
  checkForUpdate: () => request<UpdateState>('/api/v1/system/update/check', { method: 'POST' }),
  runUpdate: () => request<UpdateState>('/api/v1/system/update/run', { method: 'POST' }),
  exportConfig: () => request<Record<string, unknown>>('/api/v1/system/config'),
  importConfig: (document: Record<string, unknown>) =>
    request<{ applied: string[]; failures: string[] }>('/api/v1/system/config', {
      method: 'POST',
      body: JSON.stringify({ document }),
    }),
  newIdentity: () => request<{ ok: boolean; message: string }>('/api/v1/tor/new-identity', { method: 'POST' }),
  circumvention: () => request<CircumventionPayload>('/api/v1/circumvention'),
  setCircumvention: (body: {
    mode: ConnectionMode
    transport: string
    country: string
    custom_bridges: string[]
  }) => request<CircumventionPayload>('/api/v1/circumvention', { method: 'POST', body: JSON.stringify(body) }),
  refreshBridges: () => request<CircumventionPayload>('/api/v1/circumvention/refresh', { method: 'POST' }),
  setSnowflakeRelay: (enabled: boolean) =>
    request<CircumventionPayload>('/api/v1/relay/snowflake', { method: 'POST', body: JSON.stringify({ enabled }) }),
  files: (path = '') => request<FilesPayload>(`/api/v1/files?path=${encodeURIComponent(path)}`),
  createFolder: (parent: string, name: string) =>
    request('/api/v1/files/folders', { method: 'POST', body: JSON.stringify({ parent, name }) }),
  deleteFile: (path: string) => request<void>(`/api/v1/files?path=${encodeURIComponent(path)}`, { method: 'DELETE' }),
  logs: (service: string) => request<{ service: string; lines: string[] }>(`/api/v1/logs?service=${encodeURIComponent(service)}`),
}

export function uploadFile(
  file: File,
  path: string,
  onProgress: (progress: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/v1/files/upload')
    xhr.withCredentials = true
    xhr.setRequestHeader('X-CSRF-Token', csrfToken)
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else {
        let message = 'Échec de l’importation'
        try {
          message = JSON.parse(xhr.responseText).detail || message
        } catch {
          // Keep the generic message for non-JSON proxy errors.
        }
        reject(new ApiError(message, xhr.status))
      }
    }
    xhr.onerror = () => reject(new ApiError('Connexion interrompue', 0))
    const form = new FormData()
    form.append('path', path)
    form.append('file', file)
    xhr.send(form)
  })
}
