import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Send, Wallet } from 'lucide-react'
import { api, ApiError } from '../lib/api'

interface AiUsage {
  month: string
  provider: string
  input_tokens: number
  output_tokens: number
  calls: number
  cost_usd: number
  budget_usd: number
  remaining_usd: number | null
}

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

export default function ChatPanel() {
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
      qc.invalidateQueries({ queryKey: ['profile-history'] })
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
    <div className="flex h-full flex-col">
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

export function UsageChip() {
  const usage = useQuery({
    queryKey: ['ai-usage'],
    queryFn: () => api.get<AiUsage>('/api/system/usage'),
    refetchInterval: 60_000,
  })
  const u = usage.data
  // Local models are free — the budget chip only makes sense for Anthropic
  if (!u || u.provider !== 'anthropic' || !u.budget_usd) return null
  const pctLeft = Math.max(0, Math.round(((u.remaining_usd ?? 0) / u.budget_usd) * 100))
  const low = pctLeft < 15
  const tokens = u.input_tokens + u.output_tokens
  const tokensLabel = tokens >= 1_000_000 ? `${(tokens / 1_000_000).toFixed(1)}M` : `${Math.round(tokens / 1000)}k`
  return (
    <span
      title={`This month: ${u.calls} AI calls, ${tokensLabel} tokens, ~$${u.cost_usd.toFixed(2)} of your $${u.budget_usd} budget. Anthropic doesn't expose account balance, so this tracks what HouseSpotter itself spends.`}
      className={`flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${
        low
          ? 'bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
          : 'bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400'
      }`}
    >
      <Wallet size={13} />
      ${u.remaining_usd?.toFixed(2)} left
    </span>
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
