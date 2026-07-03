import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bot, CheckCircle2, Info, KeyRound, LogOut, Mail, Radar, Route, Send, UserRound, XCircle,
} from 'lucide-react'
import { api, ApiError } from '../lib/api'
import MilestonesCard from '../components/MilestonesCard'

interface ConfigItem {
  key: string
  label: string
  section: string
  secret: boolean
  kind: 'str' | 'int' | 'bool'
  restart_required: boolean
  set: boolean
  value: string | number | boolean | null
  hint: string | null
}

const SECTIONS: {
  id: string
  title: string
  icon: typeof Bot
  blurb: string
  help: React.ReactNode
  tests?: { label: string; endpoint: string }[]
}[] = [
  {
    id: 'ai',
    title: 'AI — Anthropic',
    icon: Bot,
    blurb: 'Powers the Agent chat, free-text desire scoring and area narratives.',
    help: (
      <ol className="list-decimal space-y-1 pl-4">
        <li>Go to <b>platform.claude.com</b> and sign in (or create an account).</li>
        <li>Open <b>Settings → API keys → Create key</b>.</li>
        <li>Copy the key (starts with <code>sk-ant-</code>) and paste it here.</li>
        <li>Costs are pay-as-you-go — HouseSpotter caches every response, so typical usage is a few pounds a month.</li>
      </ol>
    ),
    tests: [{ label: 'Test key', endpoint: '/api/config/test/anthropic' }],
  },
  {
    id: 'telegram',
    title: 'Telegram bot (server)',
    icon: Send,
    blurb: 'One bot serves the whole house — each user picks their own chat in "My alert targets".',
    help: (
      <ol className="list-decimal space-y-1 pl-4">
        <li>In Telegram, open <b>t.me/BotFather</b> (blue verified tick) and press <b>Start</b>.</li>
        <li>Send <code>/newbot</code> — pick any display name, then a unique username ending in <code>bot</code>.</li>
        <li>Copy the token BotFather replies with (looks like <code>71234…:AAH…</code>) into the field here.</li>
        <li>Each user then messages the bot and hits <b>Detect chat ID</b> in their own <b>My alert targets</b> card — that's where delivery is configured and tested.</li>
      </ol>
    ),
    tests: [{ label: 'Test bot token', endpoint: '/api/config/test/telegram-bot' }],
  },
  {
    id: 'email',
    title: 'Email server (SMTP)',
    icon: Mail,
    blurb: 'Outgoing mail server for the whole house — each user sets their own address in "My alert targets".',
    help: (
      <div className="space-y-1.5">
        <p>Any SMTP account works. For Gmail:</p>
        <ol className="list-decimal space-y-1 pl-4">
          <li>Host <code>smtp.gmail.com</code>, port <code>587</code>.</li>
          <li>Username = your Gmail address.</li>
          <li>Password: create an <b>App Password</b> at myaccount.google.com → Security → 2-Step Verification → App passwords (normal passwords won't work).</li>
          <li>From = your Gmail address. Recipients are per-user — everyone enters their own address in <b>My alert targets</b>.</li>
        </ol>
        <p>The test button sends to <i>your</i> address from My alert targets.</p>
      </div>
    ),
    tests: [{ label: 'Send test email', endpoint: '/api/config/test/email' }],
  },
  {
    id: 'routing',
    title: 'Travel times — OpenRouteService',
    icon: Route,
    blurb: 'Real drive/cycle/walk times to your Milestones. Free, no card needed.',
    help: (
      <ol className="list-decimal space-y-1 pl-4">
        <li>Sign up free at <b>openrouteservice.org</b> (email only, no card).</li>
        <li>In the dashboard, request a <b>token</b> (standard/free plan).</li>
        <li>Paste the key here — it's a long <code>eyJ…</code> string.</li>
        <li>Free quota (2,000 routes/day) is far beyond what HouseSpotter uses; without a key, travel times fall back to distance estimates.</li>
      </ol>
    ),
    tests: [{ label: 'Test key', endpoint: '/api/config/test/ors' }],
  },
  {
    id: 'scraping',
    title: 'Scanning',
    icon: Radar,
    blurb: 'Portal scanning behaviour.',
    help: (
      <div className="space-y-1.5">
        <p><b>Automatic scanning</b> polls the portals every ~30 minutes. Turning it off leaves manual "Scan now" working.</p>
        <p><b>Zoopla adapter</b> needs a headless browser (Playwright + Chromium) installed on the server and is experimental — Rightmove and OnTheMarket work without it.</p>
        <p>Changes here need a service restart: <code>sudo systemctl restart housespotter</code>.</p>
      </div>
    ),
  },
]

interface Me {
  username: string
  is_admin: boolean
  telegram_chat_id: string
  email_to: string
  channels: {
    telegram: { server: boolean; user: boolean }
    email: { server: boolean; user: boolean }
  }
}

export default function ConfigPage() {
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<Me>('/api/auth/me'),
  })
  const isAdmin = me.data?.is_admin ?? false

  const config = useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<ConfigItem[]>('/api/config'),
    enabled: isAdmin,
  })

  // Deep links like /config#my-alerts or /config#server-telegram scroll to the section
  const location = useLocation()
  useEffect(() => {
    if (!location.hash || !me.data) return
    const t = setTimeout(() => {
      const target =
        document.getElementById(location.hash.slice(1)) ?? document.getElementById('my-alerts')
      target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 250)
    return () => clearTimeout(t)
  }, [location.hash, me.data])

  return (
    <div className="mx-auto max-w-2xl space-y-5 p-4 md:p-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Settings</h1>
        <p className="text-sm text-stone-500">
          Your milestones, alerts and account{isAdmin ? ' — plus server connections and users (admin)' : ''}.
        </p>
      </div>

      <MilestonesCard />

      {me.data && <MyAlertsCard me={me.data} />}

      {isAdmin &&
        SECTIONS.map((section) => (
          <ConfigSection
            key={section.id}
            section={section}
            items={(config.data ?? []).filter((i) => i.section === section.id)}
          />
        ))}

      {isAdmin && <UsersCard />}

      <AccountSection />
    </div>
  )
}

function MyAlertsCard({ me }: { me: Me }) {
  const qc = useQueryClient()
  const [chatId, setChatId] = useState(me.telegram_chat_id)
  const [emailTo, setEmailTo] = useState(me.email_to)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  const save = useMutation({
    mutationFn: () =>
      api.patch('/api/auth/me/alerts', { telegram_chat_id: chatId.trim(), email_to: emailTo.trim() }),
    onSuccess: () => {
      setResult({ ok: true, text: 'Saved.' })
      qc.invalidateQueries({ queryKey: ['me'] })
    },
    onError: (err) => setResult({ ok: false, text: err instanceof ApiError ? err.message : 'Save failed' }),
  })
  const test = useMutation({
    mutationFn: () => api.post<{ detail: string }>('/api/auth/me/test-telegram'),
    onSuccess: (r) => setResult({ ok: true, text: r.detail }),
    onError: (err) => setResult({ ok: false, text: err instanceof ApiError ? err.message : 'Test failed' }),
  })
  const detect = useMutation({
    mutationFn: () => api.post<{ detail: string; chat_id: string }>('/api/auth/me/detect-telegram'),
    onSuccess: (r) => {
      setChatId(r.chat_id)
      setResult({ ok: true, text: r.detail })
      qc.invalidateQueries({ queryKey: ['me'] })
    },
    onError: (err) => setResult({ ok: false, text: err instanceof ApiError ? err.message : 'Detection failed' }),
  })

  const telegramServer = me.channels?.telegram?.server ?? false
  const emailServer = me.channels?.email?.server ?? false
  const dirty = chatId.trim() !== me.telegram_chat_id || emailTo.trim() !== me.email_to

  const secondaryBtn =
    'rounded-lg border border-stone-300 px-3.5 py-1.5 text-sm font-semibold text-stone-600 hover:bg-stone-50 disabled:opacity-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800'

  return (
    <div id="my-alerts" className="scroll-mt-4 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
      <h2 className="mb-1 flex items-center gap-2 font-semibold">
        <Send size={16} /> My alert targets
      </h2>
      <p className="mb-3 text-xs text-stone-400">
        Where <b>your</b> alerts go — every user has their own. The admin sets up the shared bot
        and email server{me.is_admin ? ' in the sections below' : ''}.
      </p>
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-medium">Telegram chat ID</span>
          {telegramServer ? (
            <span className="flex items-center gap-2">
              <input className="input w-44" value={chatId} onChange={(e) => setChatId(e.target.value)} placeholder="e.g. 5098965168" />
              <button
                onClick={() => detect.mutate()}
                disabled={detect.isPending}
                title="Message the house bot in Telegram (press Start), then click — your chat ID is filled in automatically"
                className={secondaryBtn}
              >
                {detect.isPending ? '…' : 'Detect chat ID'}
              </button>
            </span>
          ) : (
            <span className="text-sm text-stone-400">
              {me.is_admin ? 'Set up the Telegram bot below first.' : 'Not available — ask the admin to set up the Telegram bot.'}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm font-medium">Email address</span>
          {emailServer ? (
            <input className="input w-64" value={emailTo} onChange={(e) => setEmailTo(e.target.value)} placeholder="you@example.com" />
          ) : (
            <span className="text-sm text-stone-400">
              {me.is_admin ? 'Set up the email server below first.' : 'Not available — ask the admin to set up the email server.'}
            </span>
          )}
        </div>
      </div>
      {(telegramServer || emailServer) && (
        <div className="mt-4 flex items-center gap-2">
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="rounded-lg bg-brand-600 px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
          >
            Save
          </button>
          {telegramServer && (
            <button onClick={() => test.mutate()} disabled={test.isPending} className={secondaryBtn}>
              Send test
            </button>
          )}
        </div>
      )}
      {result && (
        <p className={`mt-2.5 rounded-lg px-3 py-2 text-sm ${result.ok ? 'bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-300' : 'bg-red-50 text-red-600 dark:bg-red-950 dark:text-red-300'}`}>
          {result.text}
        </p>
      )}
    </div>
  )
}

interface UserRow {
  id: number
  username: string
  is_admin: boolean
  created_at: string
}

function UsersCard() {
  const qc = useQueryClient()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const users = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<UserRow[]>('/api/auth/users'),
  })
  const create = useMutation({
    mutationFn: () => api.post('/api/auth/users', { username: username.trim(), password }),
    onMutate: () => setError(''),
    onSuccess: () => {
      setUsername('')
      setPassword('')
      qc.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Failed to create user'),
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/api/auth/users/${id}`),
    onMutate: () => setError(''),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Failed to delete user'),
  })

  return (
    <div className="rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
      <h2 className="mb-1 flex items-center gap-2 font-semibold">
        <UserRound size={16} /> Users
      </h2>
      <p className="mb-3 text-xs text-stone-400">
        Each user has their own login, search profiles, lists, milestones, chat and alerts.
        Deleting a user removes all their data.
      </p>
      <div className="space-y-1.5">
        {users.data?.map((u) => (
          <div key={u.id} className="flex items-center justify-between rounded-lg px-2 py-1.5 text-sm hover:bg-stone-50 dark:hover:bg-stone-800/50">
            <span className="font-medium">
              {u.username}
              {u.is_admin && (
                <span className="ml-2 rounded-full bg-brand-100 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700 dark:bg-brand-900 dark:text-brand-300">
                  admin
                </span>
              )}
            </span>
            {!u.is_admin && (
              <button
                onClick={() => confirm(`Delete user "${u.username}" and ALL their data?`) && remove.mutate(u.id)}
                className="text-xs text-stone-400 hover:text-red-500"
              >
                Delete
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-stone-200 pt-3 dark:border-stone-700">
        <input className="input w-36" placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" />
        <input className="input w-44" type="password" placeholder="Password (8+ chars)" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
        <button
          onClick={() => create.mutate()}
          disabled={!username.trim() || password.length < 8 || create.isPending}
          className="rounded-lg bg-brand-600 px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
        >
          Add user
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-500">{error}</p>}
    </div>
  )
}

function ConfigSection({
  section,
  items,
}: {
  section: (typeof SECTIONS)[number]
  items: ConfigItem[]
}) {
  const qc = useQueryClient()
  const [showHelp, setShowHelp] = useState(false)
  const [draft, setDraft] = useState<Record<string, string | boolean>>({})
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const Icon = section.icon

  const save = useMutation({
    mutationFn: () => api.patch('/api/config', { values: draft }),
    onSuccess: () => {
      setDraft({})
      setResult({ ok: true, text: 'Saved and applied.' })
      qc.invalidateQueries({ queryKey: ['config'] })
    },
    onError: (err) =>
      setResult({ ok: false, text: err instanceof ApiError ? err.message : 'Save failed' }),
  })

  const runTest = useMutation({
    mutationFn: (endpoint: string) => api.post<{ detail?: string; chat_id?: string }>(endpoint),
    onMutate: () => setResult(null),
    onSuccess: (res) => {
      setResult({ ok: true, text: res.detail ?? (res.chat_id ? `Found chat ${res.chat_id} — saved.` : 'OK') })
      qc.invalidateQueries({ queryKey: ['config'] })
    },
    onError: (err) =>
      setResult({ ok: false, text: err instanceof ApiError ? err.message : 'Test failed' }),
  })

  const dirty = Object.keys(draft).length > 0
  const needsRestart = items.some((i) => i.restart_required && i.key in draft)

  return (
    <div id={`server-${section.id}`} className="scroll-mt-4 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold">
          <Icon size={16} /> {section.title}
        </h2>
        <button
          onClick={() => setShowHelp((v) => !v)}
          title="How to set this up"
          className={`rounded-full p-1 transition ${showHelp ? 'bg-brand-100 text-brand-700 dark:bg-brand-900 dark:text-brand-300' : 'text-stone-400 hover:text-brand-600'}`}
        >
          <Info size={15} />
        </button>
      </div>
      <p className="mb-3 text-xs text-stone-400">{section.blurb}</p>

      {showHelp && (
        <div className="mb-4 rounded-xl bg-stone-50 p-3.5 text-sm leading-relaxed text-stone-600 dark:bg-stone-800/60 dark:text-stone-300">
          {section.help}
        </div>
      )}

      <div className="space-y-3">
        {items.map((item) => (
          <div key={item.key} className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-1.5 text-sm font-medium">
              {item.label}
              {item.kind !== 'bool' && (
                item.set ? (
                  <CheckCircle2 size={13} className="text-brand-500" />
                ) : (
                  <XCircle size={13} className="text-stone-300 dark:text-stone-600" />
                )
              )}
            </span>
            {item.kind === 'bool' ? (
              <button
                onClick={() =>
                  setDraft((d) => ({
                    ...d,
                    [item.key]: !(item.key in d ? (d[item.key] as boolean) : (item.value as boolean)),
                  }))
                }
                className={`h-6 w-11 rounded-full transition ${
                  (item.key in draft ? draft[item.key] : item.value)
                    ? 'bg-brand-600'
                    : 'bg-stone-300 dark:bg-stone-700'
                }`}
              >
                <span
                  className={`block h-5 w-5 rounded-full bg-white shadow transition ${
                    (item.key in draft ? draft[item.key] : item.value) ? 'translate-x-5' : 'translate-x-0.5'
                  }`}
                />
              </button>
            ) : (
              <input
                className="input w-64 max-w-full"
                type={item.secret ? 'password' : 'text'}
                placeholder={
                  item.secret
                    ? item.set
                      ? `set (${item.hint}) — paste to replace`
                      : 'not set'
                    : String(item.value ?? '')
                }
                value={(draft[item.key] as string) ?? ''}
                onChange={(e) => setDraft((d) => ({ ...d, [item.key]: e.target.value }))}
              />
            )}
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <button
          onClick={() => save.mutate()}
          disabled={!dirty || save.isPending}
          className="rounded-lg bg-brand-600 px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
        >
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
        {section.tests?.map((t) => (
          <button
            key={t.endpoint}
            onClick={() => runTest.mutate(t.endpoint)}
            disabled={runTest.isPending}
            className="rounded-lg border border-stone-300 px-3.5 py-1.5 text-sm font-semibold text-stone-600 hover:bg-stone-50 disabled:opacity-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
          >
            {runTest.isPending ? '…' : t.label}
          </button>
        ))}
        {needsRestart && (
          <span className="text-xs text-amber-600 dark:text-amber-400">needs a service restart</span>
        )}
      </div>
      {result && (
        <p
          className={`mt-2.5 rounded-lg px-3 py-2 text-sm ${
            result.ok
              ? 'bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-300'
              : 'bg-red-50 text-red-600 dark:bg-red-950 dark:text-red-300'
          }`}
        >
          {result.text}
        </p>
      )}
    </div>
  )
}

function AccountSection() {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null)

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ username: string }>('/api/auth/me'),
  })

  const save = useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; username: string; changed: string[] }>('/api/auth/account', {
        current_password: currentPassword,
        new_username: newUsername.trim() || null,
        new_password: newPassword || null,
      }),
    onSuccess: (res) => {
      setMessage({ ok: true, text: `Updated: ${res.changed.join(' and ')}.` })
      setCurrentPassword('')
      setNewUsername('')
      setNewPassword('')
      setConfirmPassword('')
      me.refetch()
    },
    onError: (err) =>
      setMessage({ ok: false, text: err instanceof ApiError ? err.message : 'Update failed' }),
  })

  const logout = useMutation({
    mutationFn: () => api.post('/api/auth/logout'),
    onSettled: () => {
      window.location.href = '/'
    },
  })

  function submit() {
    setMessage(null)
    if (!currentPassword) return setMessage({ ok: false, text: 'Enter your current password to make changes.' })
    if (!newUsername.trim() && !newPassword) return setMessage({ ok: false, text: 'Enter a new username and/or a new password.' })
    if (newPassword && newPassword !== confirmPassword) return setMessage({ ok: false, text: 'New passwords don’t match.' })
    if (newPassword && newPassword.length < 8) return setMessage({ ok: false, text: 'New password must be at least 8 characters.' })
    save.mutate()
  }

  return (
    <div className="rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="flex items-center gap-2 font-semibold">
          <KeyRound size={16} /> Account
        </h2>
        <span className="flex items-center gap-1.5 text-xs text-stone-400">
          <UserRound size={13} /> {me.data?.username ?? '…'}
        </span>
      </div>
      <div className="space-y-3">
        <input
          type="password"
          className="input w-full"
          placeholder="Current password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          autoComplete="current-password"
        />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <input
            className="input"
            placeholder="New username (optional)"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            autoComplete="username"
          />
          <input
            type="password"
            className="input"
            placeholder="New password (optional)"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            autoComplete="new-password"
          />
          <input
            type="password"
            className="input"
            placeholder="Confirm new password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            autoComplete="new-password"
          />
        </div>
        {message && (
          <p
            className={`rounded-lg px-3 py-2 text-sm ${
              message.ok
                ? 'bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-300'
                : 'bg-red-50 text-red-600 dark:bg-red-950 dark:text-red-300'
            }`}
          >
            {message.text}
          </p>
        )}
        <div className="flex items-center justify-between">
          <button
            onClick={submit}
            disabled={save.isPending}
            className="rounded-lg bg-brand-600 px-3.5 py-1.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
          >
            {save.isPending ? 'Saving…' : 'Save changes'}
          </button>
          <button
            onClick={() => logout.mutate()}
            className="flex items-center gap-1.5 rounded-lg border border-stone-300 px-3.5 py-1.5 text-sm font-semibold text-stone-700 hover:bg-stone-100 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
          >
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </div>
    </div>
  )
}
