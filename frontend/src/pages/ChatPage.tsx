import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Send } from 'lucide-react'
import { api, ApiError } from '../lib/api'

interface Message {
  id?: number
  role: 'user' | 'assistant'
  content: string
}

const SESSION_KEY = 'hs-chat-session'

function sessionId(): string {
  let sid = localStorage.getItem(SESSION_KEY)
  if (!sid) {
    // crypto.randomUUID is unavailable in non-HTTPS contexts (e.g. plain HTTP over a tailnet)
    sid =
      typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
    localStorage.setItem(SESSION_KEY, sid)
  }
  return sid
}

export default function ChatPage() {
  const sid = sessionId()
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const [pendingUser, setPendingUser] = useState<string | null>(null)
  const [error, setError] = useState('')
  const bottomRef = useRef<HTMLDivElement>(null)

  const history = useQuery({
    queryKey: ['chat', sid],
    queryFn: () => api.get<Message[]>(`/api/chat/${sid}`),
  })

  const send = useMutation({
    mutationFn: (message: string) => api.post<Message>(`/api/chat/${sid}`, { message }),
    onMutate: (message) => {
      setPendingUser(message)
      setError('')
    },
    onSettled: () => {
      setPendingUser(null)
      qc.invalidateQueries({ queryKey: ['chat', sid] })
      qc.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.message : 'Something went wrong')
    },
  })

  const messages: Message[] = [
    ...(history.data ?? []),
    ...(pendingUser ? [{ role: 'user' as const, content: pendingUser }] : []),
  ]

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, send.isPending])

  function submit() {
    const text = draft.trim()
    if (!text || send.isPending) return
    setDraft('')
    send.mutate(text)
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col p-4 md:p-6">
      <h1 className="mb-1 text-2xl font-bold tracking-tight">Your agent</h1>
      <p className="mb-4 text-sm text-stone-500">
        Describe what you're looking for — budget, areas, must-haves — and I'll set up your search.
      </p>

      <div className="flex-1 space-y-3 overflow-y-auto rounded-2xl border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900">
        {messages.length === 0 && !send.isPending && (
          <div className="flex h-full items-center justify-center text-center text-sm text-stone-400">
            <p>
              Try: "We're looking to buy a 3-bed house up to £450k around Guildford,
              <br />
              must have a garden, ideally parking and a short walk to the station."
            </p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={msg.id ?? `p${i}`} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div
              className={`max-w-[85%] whitespace-pre-line rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                msg.role === 'user'
                  ? 'rounded-br-md bg-brand-600 text-white'
                  : 'rounded-bl-md bg-stone-100 text-stone-800 dark:bg-stone-800 dark:text-stone-200'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}
        {send.isPending && (
          <div className="flex justify-start">
            <div className="rounded-2xl rounded-bl-md bg-stone-100 px-4 py-3 dark:bg-stone-800">
              <div className="flex gap-1">
                <Dot delay="0ms" /> <Dot delay="150ms" /> <Dot delay="300ms" />
              </div>
            </div>
          </div>
        )}
        {error && <p className="text-center text-sm text-red-500">{error}</p>}
        <div ref={bottomRef} />
      </div>

      <div className="mt-3 flex gap-2">
        <textarea
          rows={1}
          className="flex-1 resize-none rounded-xl border border-stone-300 bg-white px-4 py-2.5 text-sm outline-none focus:border-brand-500 dark:border-stone-700 dark:bg-stone-900"
          placeholder="Tell me what you're looking for…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <button
          onClick={submit}
          disabled={send.isPending || !draft.trim()}
          className="rounded-xl bg-brand-600 px-4 text-white hover:bg-brand-700 disabled:opacity-50"
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  )
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="h-2 w-2 animate-bounce rounded-full bg-stone-400"
      style={{ animationDelay: delay }}
    />
  )
}
