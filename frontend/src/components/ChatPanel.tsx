import { MessageCircle, Send } from 'lucide-react'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { initials, relativeTime } from '../lib'
import type { ChatMessage } from '../types'
import { Panel } from './Panel'
import { Badge, EmptyState } from './ui'

type Props = {
  messages: ChatMessage[]
  online: number
  connected: boolean
  send: (body: string) => boolean
  compact?: boolean
  /** The dedicated chat screen already carries the title in its page header. */
  withHeading?: boolean
}

export function ChatPanel({ messages, online, connected, send, compact = false, withHeading = true }: Props) {
  const [body, setBody] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest' })
  }, [messages.length])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = body.trim()
    if (value && send(value)) setBody('')
  }

  const visible = compact ? messages.slice(-4) : messages
  const inputId = compact ? 'chat-compact' : 'chat-main'
  return (
    <Panel
      title={withHeading ? 'Chat du réseau' : undefined}
      className={`chat-panel ${compact ? 'chat-compact' : ''}`}
      action={
        withHeading
          ? connected
            ? <Badge tone="success" live>{online} en ligne</Badge>
            : <Badge tone="warning" dot>Reconnexion…</Badge>
          : undefined
      }
    >
      <div className="messages" aria-live="polite">
        {visible.map((message) => (
          <article className="message" key={message.id}>
            <span className="avatar" aria-hidden="true">{initials(message.author)}</span>
            <div>
              <header>
                <strong>{message.author}</strong>
                <time>{relativeTime(message.created_at)}</time>
              </header>
              <p>{message.body}</p>
            </div>
          </article>
        ))}
        {!visible.length && (
          <EmptyState icon={MessageCircle} title="Aucun message">
            Les personnes connectées au Wi-Fi OnionPi peuvent échanger ici, sans que rien ne quitte le réseau local.
          </EmptyState>
        )}
        <div ref={endRef} />
      </div>
      <form className="chat-form" onSubmit={submit}>
        <label className="sr-only" htmlFor={inputId}>Message</label>
        <input
          id={inputId}
          value={body}
          onChange={(event) => setBody(event.target.value)}
          placeholder={connected ? 'Écrire un message…' : 'Connexion au salon…'}
          maxLength={2000}
          disabled={!connected}
        />
        <button className="button-send" aria-label="Envoyer le message" disabled={!connected || !body.trim()}>
          <Send size={17} />
        </button>
      </form>
    </Panel>
  )
}
