export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand" aria-label="OnionPi">
      <img src="/onionpi-mark.png" alt="" className="brand-mark" />
      {!compact && <span>OnionPi</span>}
    </div>
  )
}
