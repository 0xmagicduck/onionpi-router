import { Send } from 'lucide-react'
import { FormEvent, useEffect, useRef, useState } from 'react'
import { initials, relativeTime } from '../lib'
import type { ChatMessage } from '../types'

type Props = {
  messages: ChatMessage[]
  online: number
  connected: boolean
  send: (body: string) => boolean
  compact?: boolean
}

export function ChatPanel({ messages, online, connected, send, compact = false }: Props) {
  const [body, setBody] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [messages.length])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const value = body.trim()
    if (value && send(value)) setBody('')
  }

  const visible = compact ? messages.slice(-3) : messages
  return (
    <section className={`panel chat-panel ${compact ? 'chat-compact' : ''}`}>
      <div className="panel-heading chat-heading">
        <div><h2>Chat du réseau</h2><span className="online"><i />{connected ? `${online} en ligne` : 'Reconnexion…'}</span></div>
      </div>
      <div className="messages" aria-live="polite">
        {visible.map((message) => (
          <article className="message" key={message.id}>
            <span className="avatar">{initials(message.author)}</span>
            <div><header><strong>{message.author}</strong><time>{relativeTime(message.created_at)}</time></header><p>{message.body}</p></div>
          </article>
        ))}
        {!visible.length && <div className="chat-empty">Écrivez le premier message du réseau.</div>}
        <div ref={endRef} />
      </div>
      <form className="chat-form" onSubmit={submit}>
        <label className="sr-only" htmlFor={compact ? 'chat-compact' : 'chat-main'}>Message</label>
        <input id={compact ? 'chat-compact' : 'chat-main'} value={body} onChange={(event) => setBody(event.target.value)} placeholder="Écrire un message…" maxLength={2000} />
        <button className="icon-button button-send" aria-label="Envoyer" disabled={!connected || !body.trim()}><Send size={18} /></button>
      </form>
    </section>
  )
}
