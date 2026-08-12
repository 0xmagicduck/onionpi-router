import { useEffect, useMemo, useRef, useState } from 'react'
import { CornerDownLeft, Search, type LucideIcon } from 'lucide-react'
import { NAV_GROUPS, type Page } from '../navigation'

export type Command = {
  id: string
  label: string
  icon: LucideIcon
  group: string
  hint?: string
  keywords?: string
  run: () => void
}

type Props = {
  open: boolean
  onClose: () => void
  onNavigate: (page: Page) => void
  actions: Command[]
}

/** Accent-insensitive matching: nobody types « identité » with its accent when
 *  they are in a hurry. */
function normalise(value: string): string {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('fr')
}

export function CommandPalette({ open, onClose, onNavigate, actions }: Props) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const commands = useMemo<Command[]>(() => {
    const pages = NAV_GROUPS.flatMap((group) =>
      group.items.map<Command>((item) => ({
        id: `page:${item.id}`,
        label: item.label,
        icon: item.icon,
        group: 'Aller à',
        hint: item.hint,
        keywords: item.keywords,
        run: () => onNavigate(item.id),
      })),
    )
    return [...pages, ...actions]
  }, [actions, onNavigate])

  /** Ranks a command against the query: a label match beats a keyword match,
   *  so typing « identité » offers the action before the Tor page. */
  const grouped = useMemo(() => {
    const needle = normalise(query.trim())
    const scored = commands
      .map((command) => {
        if (!needle) return { command, score: 0 }
        const label = normalise(command.label)
        if (label.startsWith(needle)) return { command, score: 0 }
        if (label.includes(needle)) return { command, score: 1 }
        if (normalise(command.hint ?? '').includes(needle)) return { command, score: 2 }
        if (normalise(`${command.group} ${command.keywords ?? ''}`).includes(needle)) return { command, score: 3 }
        return { command, score: 9 }
      })
      .filter((entry) => entry.score < 9)
      .sort((left, right) => left.score - right.score)

    const groups = new Map<string, Command[]>()
    for (const { command } of scored) {
      const bucket = groups.get(command.group)
      if (bucket) bucket.push(command)
      else groups.set(command.group, [command])
    }
    return [...groups.entries()]
  }, [commands, query])

  const results = useMemo(() => grouped.flatMap(([, items]) => items), [grouped])

  useEffect(() => {
    if (!open) {
      setQuery('')
      setActive(0)
    } else {
      inputRef.current?.focus()
    }
  }, [open])

  useEffect(() => setActive(0), [query])

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = overflow
      previous?.focus?.()
    }
  }, [open])

  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active, results])

  if (!open) return null

  const run = (command: Command) => {
    onClose()
    command.run()
  }

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape') { onClose(); return }
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      setActive((index) => (results.length ? (index + 1) % results.length : 0))
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      setActive((index) => (results.length ? (index - 1 + results.length) % results.length : 0))
    }
    if (event.key === 'Enter' && results[active]) {
      event.preventDefault()
      run(results[active])
    }
  }

  let index = -1
  return (
    <div className="palette-backdrop" role="presentation" onMouseDown={onClose}>
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Palette de commandes"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={onKeyDown}
      >
        <div className="palette-input">
          <Search size={18} />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Rechercher une page ou une action…"
            aria-label="Rechercher une page ou une action"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={results[active] ? `palette-${results[active].id}` : undefined}
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        <div className="palette-list" id="palette-list" role="listbox" aria-label="Résultats" ref={listRef}>
          {grouped.map(([group, items]) => (
            <div key={group}>
              <p className="palette-group section-label">{group}</p>
              {items.map((command) => {
                index += 1
                const position = index
                const Icon = command.icon
                return (
                  <button
                    key={command.id}
                    id={`palette-${command.id}`}
                    className="palette-option"
                    role="option"
                    aria-selected={position === active}
                    onMouseMove={() => setActive(position)}
                    onClick={() => run(command)}
                  >
                    <Icon size={17} />
                    <span>{command.label}</span>
                    {command.hint && <small>{command.hint}</small>}
                  </button>
                )
              })}
            </div>
          ))}
          {!results.length && <p className="palette-empty">Aucun résultat pour « {query} ».</p>}
        </div>
        <div className="palette-foot">
          <span><span className="kbd">↑</span><span className="kbd">↓</span> naviguer</span>
          <span><span className="kbd"><CornerDownLeft size={11} /></span> ouvrir</span>
          <span><span className="kbd">Échap</span> fermer</span>
        </div>
      </div>
    </div>
  )
}
