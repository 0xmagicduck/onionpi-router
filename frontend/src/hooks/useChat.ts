import { useEffect, useRef, useState } from 'react'
import type { ChatMessage } from '../types'

export function useChat(active = true) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [online, setOnline] = useState(0)
  const [connected, setConnected] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!active) return
    let retry: number | undefined
    let stopped = false

    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const socket = new WebSocket(`${protocol}//${window.location.host}/api/v1/chat/ws`)
      socketRef.current = socket
      socket.onopen = () => setConnected(true)
      socket.onmessage = (event) => {
        const payload = JSON.parse(event.data)
        if (payload.type === 'history') setMessages(payload.messages)
        if (payload.type === 'message') setMessages((current) => [...current.slice(-199), payload.message])
        if (payload.type === 'presence') setOnline(payload.online)
      }
      socket.onclose = () => {
        setConnected(false)
        if (!stopped) retry = window.setTimeout(connect, 2000)
      }
    }
    connect()
    return () => {
      stopped = true
      if (retry) window.clearTimeout(retry)
      socketRef.current?.close()
    }
  }, [active])

  const send = (body: string) => {
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({ type: 'message', body }))
      return true
    }
    return false
  }

  return { messages, online, connected, send }
}
