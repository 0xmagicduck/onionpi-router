export function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0 o'
  const units = ['o', 'Ko', 'Mo', 'Go', 'To']
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1000)), units.length - 1)
  const amount = value / 1000 ** index
  return `${amount.toLocaleString('fr-FR', { maximumFractionDigits: index > 2 ? 1 : 0 })} ${units[index]}`
}

export function relativeTime(timestamp: number): string {
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - timestamp))
  if (seconds < 45) return 'À l’instant'
  if (seconds < 3600) return `Il y a ${Math.floor(seconds / 60)} min`
  if (seconds < 86400) return `Il y a ${Math.floor(seconds / 3600)} h`
  return new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short' }).format(timestamp * 1000)
}

export function formatDuration(seconds: number): string {
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return [hours, minutes, rest].map((part) => String(part).padStart(2, '0')).join(':')
}

/** Human reading of an uptime, where `formatDuration` gives a stopwatch. */
export function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days) return `${days} j ${hours} h`
  if (hours) return `${hours} h ${String(minutes).padStart(2, '0')}`
  return `${minutes} min`
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('')
}
