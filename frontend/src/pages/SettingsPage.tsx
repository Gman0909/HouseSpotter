import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown, ChevronRight, History, ListChecks, Plus, RotateCcw, Settings2, Sparkles, X,
} from 'lucide-react'
import { api, ApiError } from '../lib/api'
import type { Profile } from '../lib/types'
import ChatPanel, { UsageChip } from '../components/ChatPanel'

interface ChannelReadiness {
  server: boolean
  user: boolean
}
interface MeInfo {
  username: string
  is_admin: boolean
  channels: {
    telegram: ChannelReadiness
    email: ChannelReadiness
    ai: { configured: boolean; provider: string }
  }
}

interface Vocabulary {
  criteria: { key: string; label: string }[]
  exclusions: { key: string; label: string }[]
  property_types: string[]
  tenures: string[]
}

// Friendly labels for the structured criteria keys (fallback if vocabulary not loaded)
const CRITERIA_LABELS: Record<string, string> = {
  parking: 'Parking',
  garden: 'Garden',
  garage: 'Garage',
  chain_free: 'Chain-free',
  new_build: 'New build',
  ensuite: 'En-suite',
  epc_c: 'EPC C or better',
  value: 'Price value',
  extra_beds: 'Extra bedrooms',
  milestone_access: 'Milestone access',
}

const QOL_KEYS: { key: string; label: string }[] = [
  { key: 'transport', label: 'Transport links' },
  { key: 'safety', label: 'Low crime' },
  { key: 'amenities', label: 'Shops & amenities' },
  { key: 'green', label: 'Green space' },
  { key: 'schools', label: 'Schools' },
  { key: 'quiet', label: 'Peace & quiet' },
]

function criterionLabel(c: { key?: string; text?: string }): string {
  if (c.text) return c.text
  return CRITERIA_LABELS[c.key ?? ''] ?? c.key ?? '?'
}

interface Snapshot {
  id: number
  criteria_version: number
  source: string
  created_at: string
  data: Partial<Profile>
}

function snapshotSummary(d: Partial<Profile>): string {
  const parts: string[] = []
  if (d.locations?.length) parts.push(d.locations.map((l) => l.label).join(' + '))
  if (d.max_price) parts.push(`≤£${Number(d.max_price).toLocaleString('en-GB')}${d.mode === 'rent' ? ' pcm' : ''}`)
  if (d.min_beds) parts.push(`${d.min_beds}+ beds`)
  if (d.property_types?.length) parts.push(d.property_types.join('/'))
  if (d.nice_to_haves?.length) parts.push(`${d.nice_to_haves.length} nice-to-haves`)
  return parts.join(' · ') || '—'
}

function agoShort(iso: string): string {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  return hrs < 24 ? `${hrs}h ago` : `${Math.round(hrs / 24)}d ago`
}

const KM_PER_MILE = 1.60934
const RADIUS_MILES = [1, 3, 5, 10, 15, 20, 30]

function nearestRadiusMiles(km: number | undefined): number {
  if (!km) return 5
  return RADIUS_MILES.reduce((best, m) =>
    Math.abs(m * KM_PER_MILE - km) < Math.abs(best * KM_PER_MILE - km) ? m : best,
  RADIUS_MILES[0])
}

export default function SettingsPage() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [saveError, setSaveError] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [agentOpen, setAgentOpen] = useState(searchParams.get('agent') === '1')

  useEffect(() => {
    if (searchParams.get('agent') === '1') {
      setAgentOpen(true)
      setSearchParams({}, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<MeInfo>('/api/auth/me'),
  })
  const aiReady = me.data?.channels?.ai?.configured ?? false

  const vocab = useQuery({
    queryKey: ['vocabulary'],
    queryFn: () => api.get<Vocabulary>('/api/profiles/vocabulary'),
    staleTime: Infinity,
  })

  const profiles = useQuery({
    queryKey: ['profiles'],
    queryFn: () => api.get<Profile[]>('/api/profiles'),
  })
  const profile = profiles.data?.find((p) => p.id === selectedId) ?? profiles.data?.[0] ?? null

  const save = useMutation({
    mutationFn: (patch: Partial<Profile>) => api.patch(`/api/profiles/${profile!.id}`, patch),
    onMutate: () => setSaveError(''),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['profile-history'] })
    },
    onError: (err) => setSaveError(err instanceof Error ? err.message : 'Save failed'),
  })
  const deleteProfile = useMutation({
    mutationFn: () => api.delete(`/api/profiles/${profile!.id}`),
    onSuccess: () => {
      setSelectedId(null)
      qc.invalidateQueries({ queryKey: ['profiles'] })
    },
    onError: (err) => setSaveError(err instanceof Error ? err.message : 'Delete failed'),
  })

  const [showAllHistory, setShowAllHistory] = useState(false)
  const history = useQuery({
    queryKey: ['profile-history', profile?.id],
    queryFn: () => api.get<Snapshot[]>(`/api/profiles/${profile!.id}/history`),
    enabled: !!profile,
  })
  const revert = useMutation({
    mutationFn: (snapshotId: number) =>
      api.post(`/api/profiles/${profile!.id}/revert/${snapshotId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      qc.invalidateQueries({ queryKey: ['profile-history', profile?.id] })
    },
    onError: (err) => setSaveError(err instanceof Error ? err.message : 'Restore failed'),
  })

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <div className="mb-4 flex items-center justify-between gap-2">
        <h1 className="text-2xl font-bold tracking-tight">Search Profiles</h1>
        {aiReady ? (
          <button
            onClick={() => setAgentOpen(true)}
            className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-semibold text-white hover:bg-brand-700"
          >
            <Sparkles size={15} /> AI agent
          </button>
        ) : (
          <button
            onClick={() => me.data?.is_admin && navigate('/config#server-ai')}
            title={
              me.data?.is_admin
                ? 'No AI provider configured — click to set one up in Settings'
                : 'No AI provider configured — ask the admin. Everything works manually meanwhile.'
            }
            className="flex items-center gap-1.5 rounded-lg border border-dashed border-stone-300 px-3.5 py-2 text-sm font-medium text-stone-400 opacity-70 hover:opacity-100 dark:border-stone-600"
          >
            <Sparkles size={15} /> AI agent <Settings2 size={12} />
          </button>
        )}
      </div>

      <div className="mb-5 flex flex-wrap items-center gap-2">
        {profiles.data?.map((p) => (
          <button
            key={p.id}
            onClick={() => setSelectedId(p.id)}
            className={`rounded-full px-3.5 py-1.5 text-sm font-medium ${
              profile?.id === p.id
                ? 'bg-brand-600 text-white'
                : 'bg-white text-stone-600 dark:bg-stone-900 dark:text-stone-300'
            }`}
          >
            {p.name}
          </button>
        ))}
        <button
          onClick={() => setShowCreate((v) => !v)}
          className="rounded-full border border-dashed border-stone-400 px-3.5 py-1.5 text-sm text-stone-500 hover:border-brand-500 hover:text-brand-600"
        >
          + New profile
        </button>
      </div>

      {showCreate && (
        <CreateProfileCard
          onCreated={(p) => {
            setShowCreate(false)
            setSelectedId(p.id)
          }}
        />
      )}

      {saveError && (
        <p className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-300">
          {saveError}
        </p>
      )}

      {profile && (
        <div className="space-y-5 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
          <Row label="Name">
            <input
              className="input"
              defaultValue={profile.name}
              key={`name-${profile.id}`}
              onBlur={(e) => e.target.value !== profile.name && save.mutate({ name: e.target.value })}
            />
          </Row>
          <Row label="Mode">
            <div className="flex rounded-lg border border-stone-300 p-0.5 dark:border-stone-700">
              {(['buy', 'rent'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => save.mutate({ mode: m })}
                  className={`rounded-md px-4 py-1 text-sm font-medium capitalize ${
                    profile.mode === m ? 'bg-brand-600 text-white' : 'text-stone-500'
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </Row>

          <LocationsEditor profile={profile} save={save} />

          <Row label={profile.mode === 'rent' ? 'Rent range (pcm)' : 'Price range'}>
            <div className="flex items-center gap-2">
              <input
                className="input w-32"
                type="number"
                placeholder="Min"
                key={`minp-${profile.id}`}
                defaultValue={profile.min_price ?? ''}
                onBlur={(e) => save.mutate({ min_price: e.target.value ? +e.target.value : null })}
              />
              <span className="text-stone-400">to</span>
              <input
                className="input w-32"
                type="number"
                placeholder="Max"
                key={`maxp-${profile.id}`}
                defaultValue={profile.max_price ?? ''}
                onBlur={(e) => save.mutate({ max_price: e.target.value ? +e.target.value : null })}
              />
            </div>
          </Row>
          <Row label="Bedrooms">
            <div className="flex items-center gap-2">
              <input
                className="input w-20"
                type="number"
                placeholder="Min"
                key={`beds-${profile.id}`}
                defaultValue={profile.min_beds ?? ''}
                onBlur={(e) => save.mutate({ min_beds: e.target.value ? +e.target.value : null })}
              />
              <span className="text-stone-400">to</span>
              <input
                className="input w-20"
                type="number"
                placeholder="Max"
                key={`maxbeds-${profile.id}`}
                defaultValue={profile.max_beds ?? ''}
                onBlur={(e) => save.mutate({ max_beds: e.target.value ? +e.target.value : null })}
              />
            </div>
          </Row>
          <Row label="Bathrooms (min)">
            <input
              className="input w-20"
              type="number"
              key={`baths-${profile.id}`}
              defaultValue={profile.min_baths ?? ''}
              onBlur={(e) => save.mutate({ min_baths: e.target.value ? +e.target.value : null })}
            />
          </Row>
          <Row label="Floor area (min m²)">
            <input
              className="input w-24"
              type="number"
              placeholder="any"
              key={`area-${profile.id}`}
              defaultValue={profile.min_floor_area ?? ''}
              onBlur={(e) => save.mutate({ min_floor_area: e.target.value ? +e.target.value : null })}
            />
          </Row>

          <Row label="Property types">
            <div className="flex max-w-md flex-wrap justify-end gap-1.5">
              {(vocab.data?.property_types ?? []).map((t) => {
                const on = (profile.property_types ?? []).includes(t)
                return (
                  <button
                    key={t}
                    onClick={() =>
                      save.mutate({
                        property_types: on
                          ? profile.property_types.filter((x) => x !== t)
                          : [...(profile.property_types ?? []), t],
                      })
                    }
                    title={on ? 'Click to allow all types again' : 'Restrict to selected types'}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium capitalize ${
                      on ? 'bg-brand-600 text-white' : 'bg-stone-100 text-stone-500 dark:bg-stone-800'
                    }`}
                  >
                    {t}
                  </button>
                )
              })}
            </div>
          </Row>
          {(profile.property_types ?? []).length === 0 && (
            <p className="-mt-3 text-right text-[11px] text-stone-400">no selection = all types allowed</p>
          )}
          {profile.mode === 'buy' && (
            <Row label="Tenure">
              <div className="flex flex-wrap justify-end gap-1.5">
                {(vocab.data?.tenures ?? []).map((t) => {
                  const on = (profile.tenures ?? []).includes(t)
                  return (
                    <button
                      key={t}
                      onClick={() =>
                        save.mutate({
                          tenures: on
                            ? profile.tenures.filter((x) => x !== t)
                            : [...(profile.tenures ?? []), t],
                        })
                      }
                      className={`rounded-full px-2.5 py-1 text-xs font-medium capitalize ${
                        on ? 'bg-brand-600 text-white' : 'bg-stone-100 text-stone-500 dark:bg-stone-800'
                      }`}
                    >
                      {t.replace(/-/g, ' ')}
                    </button>
                  )
                })}
              </div>
            </Row>
          )}
          <Row label="Don't show">
            <div className="flex max-w-md flex-wrap justify-end gap-1.5">
              {(vocab.data?.exclusions ?? []).map(({ key, label }) => {
                const on = (profile.exclusions ?? []).includes(key)
                return (
                  <button
                    key={key}
                    onClick={() =>
                      save.mutate({
                        exclusions: on
                          ? (profile.exclusions ?? []).filter((x) => x !== key)
                          : [...(profile.exclusions ?? []), key],
                      })
                    }
                    title={on ? `Excluding ${label.toLowerCase()} listings — click to allow` : `Exclude ${label.toLowerCase()} listings`}
                    className={`rounded-full px-2.5 py-1 text-xs font-medium ${
                      on
                        ? 'bg-red-100 text-red-600 line-through dark:bg-red-950 dark:text-red-300'
                        : 'bg-stone-100 text-stone-500 dark:bg-stone-800'
                    }`}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
          </Row>

          <Row label="Alert threshold">
            <div className="flex items-center gap-3">
              <input
                type="range"
                min={0}
                max={100}
                key={`thr-${profile.id}`}
                defaultValue={profile.alert_threshold}
                onMouseUp={(e) => save.mutate({ alert_threshold: +(e.target as HTMLInputElement).value })}
                onTouchEnd={(e) => save.mutate({ alert_threshold: +(e.target as HTMLInputElement).value })}
                className="accent-brand-600"
              />
              <span className="text-sm font-medium">{profile.alert_threshold}+</span>
            </div>
          </Row>
          <Row label="Alert channels">
            <div className="flex gap-2">
              {(['telegram', 'email'] as const).map((ch) => {
                const on = profile.alert_channels.includes(ch)
                const readiness = me.data?.channels?.[ch]
                const ready = !!(readiness?.server && readiness?.user)
                const fixHash = !readiness?.user ? 'my-alerts' : `server-${ch}`
                const toggle = () =>
                  save.mutate({
                    alert_channels: on
                      ? profile.alert_channels.filter((c) => c !== ch)
                      : [...profile.alert_channels, ch],
                  })
                if (ready) {
                  return (
                    <button
                      key={ch}
                      onClick={toggle}
                      className={`rounded-full px-3 py-1 text-sm font-medium capitalize ${
                        on ? 'bg-brand-600 text-white' : 'bg-stone-100 text-stone-500 dark:bg-stone-800'
                      }`}
                    >
                      {ch}
                    </button>
                  )
                }
                return (
                  <button
                    key={ch}
                    onClick={() => (on ? toggle() : navigate(`/config#${fixHash}`))}
                    title={
                      on
                        ? `${ch} isn't configured — alerts on this channel won't send. Click to disable it.`
                        : `${ch} isn't set up yet — click to configure it in Settings`
                    }
                    className={`flex items-center gap-1 rounded-full border border-dashed px-3 py-1 text-sm font-medium capitalize ${
                      on
                        ? 'border-amber-400 bg-amber-50 text-amber-600 dark:bg-amber-950 dark:text-amber-400'
                        : 'border-stone-300 bg-transparent text-stone-400 opacity-70 hover:opacity-100 dark:border-stone-600'
                    }`}
                  >
                    {ch}
                    <Settings2 size={12} />
                  </button>
                )
              })}
            </div>
          </Row>
          <Row label="Active">
            <button
              onClick={() => save.mutate({ active: !profile.active })}
              className={`h-6 w-11 rounded-full transition ${profile.active ? 'bg-brand-600' : 'bg-stone-300 dark:bg-stone-700'}`}
            >
              <span
                className={`block h-5 w-5 rounded-full bg-white shadow transition ${profile.active ? 'translate-x-5' : 'translate-x-0.5'}`}
              />
            </button>
          </Row>

          <AreaPreferences profile={profile} save={save} />

          <div className="border-t border-stone-200 pt-4 dark:border-stone-800">
            <button
              onClick={() => confirm('Delete this profile?') && deleteProfile.mutate()}
              className="text-xs text-red-400 hover:text-red-600"
            >
              Delete profile
            </button>
          </div>
        </div>
      )}

      {profile && (
        <RequirementsCard profile={profile} save={save} vocab={vocab.data} aiReady={aiReady} />
      )}

      {profile && (history.data?.length ?? 0) > 0 && (
        <div className="mt-5 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
          <h2 className="mb-3 flex items-center gap-2 font-semibold">
            <History size={16} /> Criteria history
          </h2>
          <div className="space-y-1.5">
            {(showAllHistory ? history.data! : history.data!.slice(0, 8)).map((snap, i) => (
              <div
                key={snap.id}
                className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-stone-50 dark:hover:bg-stone-800/50"
              >
                <div className="min-w-0">
                  <span className="font-medium">
                    v{snap.criteria_version}
                    {i === 0 && <span className="ml-1.5 rounded-full bg-brand-100 px-1.5 py-0.5 text-[10px] font-semibold text-brand-700 dark:bg-brand-900 dark:text-brand-300">current</span>}
                  </span>
                  <span className="ml-2 text-xs text-stone-400">
                    {snap.source} · {agoShort(snap.created_at)}
                  </span>
                  <p className="truncate text-xs text-stone-500">{snapshotSummary(snap.data)}</p>
                </div>
                {i > 0 && (
                  <button
                    onClick={() =>
                      confirm(`Restore this profile to v${snap.criteria_version}? Current criteria are snapshotted first, so this is reversible.`) &&
                      revert.mutate(snap.id)
                    }
                    disabled={revert.isPending}
                    className="flex shrink-0 items-center gap-1 rounded-lg border border-stone-300 px-2.5 py-1 text-xs font-semibold text-stone-600 hover:bg-stone-100 disabled:opacity-50 dark:border-stone-700 dark:text-stone-300 dark:hover:bg-stone-800"
                  >
                    <RotateCcw size={12} /> Restore
                  </button>
                )}
              </div>
            ))}
          </div>
          {(history.data?.length ?? 0) > 8 && !showAllHistory && (
            <button
              onClick={() => setShowAllHistory(true)}
              className="mt-2 text-xs text-stone-400 hover:text-stone-600"
            >
              Show all {history.data!.length} versions
            </button>
          )}
        </div>
      )}

      {/* AI agent slide-over */}
      {agentOpen && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/30" onClick={() => setAgentOpen(false)}>
          <div
            className="flex h-full w-full max-w-lg flex-col bg-stone-50 p-4 shadow-2xl dark:bg-stone-950"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 flex items-center justify-between gap-2">
              <h2 className="flex items-center gap-2 text-lg font-bold">
                <Sparkles size={17} className="text-brand-600" /> AI agent
              </h2>
              <div className="flex items-center gap-2">
                <UsageChip />
                <button
                  onClick={() => setAgentOpen(false)}
                  className="rounded-full p-1.5 text-stone-400 hover:bg-stone-200 hover:text-stone-700 dark:hover:bg-stone-800"
                >
                  <X size={18} />
                </button>
              </div>
            </div>
            <p className="mb-3 text-xs text-stone-400">
              Describe what you're looking for and I'll set up or update a search profile — everything
              I do can also be done manually on this page.
            </p>
            <div className="min-h-0 flex-1">
              <ChatPanel />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function CreateProfileCard({ onCreated }: { onCreated: (p: Profile) => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [mode, setMode] = useState<'buy' | 'rent'>('buy')
  const [location, setLocation] = useState('')
  const [maxPrice, setMaxPrice] = useState('')
  const [minBeds, setMinBeds] = useState('')
  const [error, setError] = useState('')

  const create = useMutation({
    mutationFn: () =>
      api.post<Profile>('/api/profiles', {
        name: name.trim() || (location.trim() ? `${location.trim()} ${mode === 'rent' ? 'rentals' : 'search'}` : 'New search'),
        mode,
        max_price: maxPrice ? +maxPrice : null,
        min_beds: minBeds ? +minBeds : null,
        locations: location.trim() ? [{ label: location.trim(), radius_km: 8 }] : [],
      }),
    onMutate: () => setError(''),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ['profiles'] })
      onCreated(p)
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Could not create the profile'),
  })

  return (
    <div className="mb-5 rounded-2xl border-2 border-dashed border-brand-300 bg-white p-5 dark:border-brand-800 dark:bg-stone-900">
      <h2 className="mb-3 flex items-center gap-2 font-semibold">
        <Plus size={16} /> New search profile
      </h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <input className="input col-span-2 sm:col-span-1" placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} />
        <div className="flex rounded-lg border border-stone-300 p-0.5 dark:border-stone-700">
          {(['buy', 'rent'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 rounded-md px-3 py-1 text-sm font-medium capitalize ${mode === m ? 'bg-brand-600 text-white' : 'text-stone-500'}`}
            >
              {m}
            </button>
          ))}
        </div>
        <input className="input" placeholder="Location, e.g. Cambridge" value={location} onChange={(e) => setLocation(e.target.value)} />
        <input className="input" type="number" placeholder={mode === 'rent' ? 'Max pcm' : 'Max price'} value={maxPrice} onChange={(e) => setMaxPrice(e.target.value)} />
        <input className="input" type="number" placeholder="Min beds" value={minBeds} onChange={(e) => setMinBeds(e.target.value)} />
        <button
          onClick={() => create.mutate()}
          disabled={create.isPending || !location.trim()}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-40"
        >
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-500">{error}</p>}
      <p className="mt-2 text-xs text-stone-400">
        Everything else — property types, requirements, exclusions — can be set after creating.
      </p>
    </div>
  )
}

function LocationsEditor({
  profile,
  save,
}: {
  profile: Profile
  save: { mutate: (patch: Partial<Profile>) => void }
}) {
  const [adding, setAdding] = useState('')

  function updateLocations(next: Profile['locations']) {
    save.mutate({ locations: next })
  }

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">Locations</span>
        <div className="flex max-w-md flex-wrap items-center justify-end gap-1.5">
          {(profile.locations ?? []).map((loc, idx) => (
            <span
              key={`${loc.label}-${idx}`}
              className="flex items-stretch overflow-hidden rounded-full bg-brand-100 text-xs font-semibold text-brand-700 dark:bg-brand-900 dark:text-brand-300"
              title={loc.lat != null ? `pinned at ${loc.lat.toFixed(3)}, ${loc.lng.toFixed(3)}` : 'not geocoded'}
            >
              <span className="flex items-center py-1 pl-3">{loc.label}</span>
              <select
                value={nearestRadiusMiles(loc.radius_km)}
                onChange={(e) => {
                  const next = profile.locations.map((l, i) =>
                    i === idx ? { ...l, radius_km: Math.round(+e.target.value * KM_PER_MILE * 10) / 10 } : l,
                  )
                  updateLocations(next)
                }}
                title="Search radius"
                className="ml-1.5 cursor-pointer border-0 bg-transparent py-1 pr-0.5 text-[11px] font-medium text-brand-600 outline-none dark:text-brand-300"
              >
                {RADIUS_MILES.map((m) => (
                  <option key={m} value={m}>
                    {m} mi
                  </option>
                ))}
              </select>
              <button
                onClick={() => updateLocations(profile.locations.filter((_, i) => i !== idx))}
                title={`Remove ${loc.label}`}
                className="flex items-center px-1.5 text-brand-500 hover:bg-brand-200 hover:text-brand-900 dark:hover:bg-brand-800"
              >
                <X size={12} />
              </button>
            </span>
          ))}
          <span className="flex items-center gap-1">
            <input
              className="input w-36 py-1 text-xs"
              placeholder="Add town or CB1…"
              value={adding}
              onChange={(e) => setAdding(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && adding.trim()) {
                  updateLocations([...(profile.locations ?? []), { label: adding.trim(), radius_km: 8 } as never])
                  setAdding('')
                }
              }}
            />
            <button
              onClick={() => {
                if (adding.trim()) {
                  updateLocations([...(profile.locations ?? []), { label: adding.trim(), radius_km: 8 } as never])
                  setAdding('')
                }
              }}
              disabled={!adding.trim()}
              className="rounded-lg bg-stone-100 p-1.5 text-stone-500 hover:bg-brand-100 hover:text-brand-700 disabled:opacity-40 dark:bg-stone-800"
              title="Add location"
            >
              <Plus size={14} />
            </button>
          </span>
        </div>
      </div>
      {(profile.locations ?? []).length === 0 && (
        <p className="mt-1 text-right text-[11px] text-stone-400">no locations = search everywhere scanned</p>
      )}
    </div>
  )
}

function RequirementsCard({
  profile,
  save,
  vocab,
  aiReady,
}: {
  profile: Profile
  save: { mutate: (patch: Partial<Profile>) => void }
  vocab: Vocabulary | undefined
  aiReady: boolean
}) {
  const navigate = useNavigate()
  const [desire, setDesire] = useState('')
  const [desireWeight, setDesireWeight] = useState(2)

  const activeMusts = Object.entries(profile.must_haves ?? {}).filter(([, v]) => v).map(([k]) => k)
  const usedNiceKeys = new Set((profile.nice_to_haves ?? []).filter((c) => c.key).map((c) => c.key))
  const criteria = vocab?.criteria ?? []
  const mustCandidates = criteria.filter((c) => !activeMusts.includes(c.key) && !['extra_beds', 'milestone_access'].includes(c.key))
  const niceCandidates = criteria.filter((c) => !usedNiceKeys.has(c.key))

  function addDesire() {
    const text = desire.trim()
    if (!text) return
    save.mutate({
      nice_to_haves: [...(profile.nice_to_haves ?? []), { text, kind: 'desire', weight: desireWeight }],
    })
    setDesire('')
  }

  return (
    <div className="mt-5 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
      <h2 className="mb-1 flex items-center gap-2 font-semibold">
        <ListChecks size={16} /> Requirements
      </h2>
      <p className="mb-3 text-xs text-stone-400">
        Everything this search filters and scores on. Changes re-score all properties; History below can undo.
      </p>

      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-stone-400">
        Must-haves <span className="normal-case">(hard filters)</span>
      </h3>
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        {activeMusts.map((key) => (
          <span
            key={key}
            className="flex items-center gap-1 rounded-full bg-brand-100 py-1 pl-3 pr-1.5 text-xs font-semibold text-brand-700 dark:bg-brand-900 dark:text-brand-300"
          >
            {CRITERIA_LABELS[key] ?? key}
            <button
              onClick={() => {
                const next = { ...profile.must_haves }
                delete next[key]
                save.mutate({ must_haves: next })
              }}
              title={`Remove must-have: ${CRITERIA_LABELS[key] ?? key}`}
              className="rounded-full p-0.5 text-brand-500 hover:bg-brand-200 hover:text-brand-900 dark:hover:bg-brand-800"
            >
              <X size={12} />
            </button>
          </span>
        ))}
        {mustCandidates.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) {
                save.mutate({ must_haves: { ...(profile.must_haves ?? {}), [e.target.value]: true } })
              }
            }}
            className="cursor-pointer rounded-full border border-dashed border-stone-300 bg-transparent px-2.5 py-1 text-xs font-medium text-stone-500 outline-none hover:border-brand-500 hover:text-brand-600 dark:border-stone-600"
            title="Add a hard requirement — properties without it are filtered out entirely"
          >
            <option value="">+ Add must-have…</option>
            {mustCandidates.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        )}
        {activeMusts.length === 0 && (
          <span className="text-xs text-stone-400">None — everything passes the hard filters.</span>
        )}
      </div>

      <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-stone-400">
        Nice-to-haves <span className="normal-case">(weighted scoring)</span>
      </h3>
      <div className="space-y-1">
        {(profile.nice_to_haves ?? []).map((c, idx) => (
          <div
            key={`${c.kind}-${c.key ?? c.text}-${idx}`}
            className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-stone-50 dark:hover:bg-stone-800/50"
          >
            <span className="min-w-0 truncate">
              {criterionLabel(c)}
              <span
                className={`ml-2 rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                  c.kind === 'desire'
                    ? 'bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-300'
                    : 'bg-stone-100 text-stone-500 dark:bg-stone-800'
                }`}
                title={c.kind === 'desire' ? 'Free-text desire, judged by AI per listing' : 'Structured criterion, checked automatically'}
              >
                {c.kind === 'desire' ? 'AI-judged' : 'structured'}
              </span>
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <select
                value={c.weight}
                onChange={(e) => {
                  const next = profile.nice_to_haves.map((n, i) =>
                    i === idx ? { ...n, weight: Number(e.target.value) } : n,
                  )
                  save.mutate({ nice_to_haves: next })
                }}
                title="Weight — how much this matters"
                className="rounded-lg border border-stone-300 bg-transparent px-1.5 py-1 text-xs dark:border-stone-700"
              >
                <option value={1}>×1</option>
                <option value={2}>×2</option>
                <option value={3}>×3</option>
              </select>
              <button
                onClick={() => {
                  const next = profile.nice_to_haves.filter((_, i) => i !== idx)
                  save.mutate({ nice_to_haves: next })
                }}
                title={`Remove: ${criterionLabel(c)}`}
                className="text-stone-400 hover:text-red-500"
              >
                <X size={14} />
              </button>
            </span>
          </div>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        {niceCandidates.length > 0 && (
          <select
            value=""
            onChange={(e) => {
              if (e.target.value) {
                save.mutate({
                  nice_to_haves: [
                    ...(profile.nice_to_haves ?? []),
                    { key: e.target.value, kind: 'structured', weight: 2 },
                  ],
                })
              }
            }}
            className="cursor-pointer rounded-full border border-dashed border-stone-300 bg-transparent px-2.5 py-1 text-xs font-medium text-stone-500 outline-none hover:border-brand-500 hover:text-brand-600 dark:border-stone-600"
            title="Add a weighted preference — checked automatically, no AI needed"
          >
            <option value="">+ Add nice-to-have…</option>
            {niceCandidates.map((c) => (
              <option key={c.key} value={c.key}>
                {c.label}
              </option>
            ))}
          </select>
        )}
      </div>

      <div className="mt-3 border-t border-stone-200 pt-3 dark:border-stone-700">
        {aiReady ? (
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="input min-w-48 flex-1"
              placeholder='Free-text desire, e.g. "light and airy", "period features"'
              value={desire}
              onChange={(e) => setDesire(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && addDesire()}
            />
            <select
              value={desireWeight}
              onChange={(e) => setDesireWeight(+e.target.value)}
              className="rounded-lg border border-stone-300 bg-transparent px-1.5 py-1.5 text-xs dark:border-stone-700"
            >
              <option value={1}>×1</option>
              <option value={2}>×2</option>
              <option value={3}>×3</option>
            </select>
            <button
              onClick={addDesire}
              disabled={!desire.trim()}
              className="rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-700 disabled:opacity-40"
              title="AI reads every listing and scores how well it satisfies this"
            >
              + AI-judged desire
            </button>
          </div>
        ) : (
          <button
            onClick={() => navigate('/config#server-ai')}
            className="text-xs text-stone-400 hover:text-brand-600"
            title="Free-text desires are judged by AI per listing — set up an AI provider to use them"
          >
            💡 Free-text desires ("light and airy"…) need an AI provider — set one up in Settings →
          </button>
        )}
      </div>
    </div>
  )
}

function AreaPreferences({
  profile,
  save,
}: {
  profile: Profile
  save: { mutate: (patch: Partial<Profile>) => void }
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-t border-stone-200 pt-3 dark:border-stone-800">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-sm font-medium text-stone-600 hover:text-stone-900 dark:text-stone-300 dark:hover:text-stone-100"
      >
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        Area preferences & notes
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          <p className="text-xs text-stone-400">
            How much each factor matters when researching neighbourhoods (Areas tab).
          </p>
          <div className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
            {QOL_KEYS.map(({ key, label }) => (
              <label key={key} className="flex items-center justify-between gap-2 text-sm">
                <span className="text-stone-600 dark:text-stone-300">{label}</span>
                <select
                  value={profile.qol_weights?.[key] ?? 0}
                  onChange={(e) =>
                    save.mutate({ qol_weights: { ...(profile.qol_weights ?? {}), [key]: +e.target.value } })
                  }
                  className="rounded-lg border border-stone-300 bg-transparent px-1.5 py-1 text-xs dark:border-stone-700"
                >
                  <option value={0}>—</option>
                  <option value={1}>+</option>
                  <option value={2}>++</option>
                  <option value={3}>+++</option>
                </select>
              </label>
            ))}
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-stone-500">Search brief (context for AI scoring)</p>
            <textarea
              className="input w-full"
              rows={2}
              key={`brief-${profile.id}`}
              defaultValue={profile.brief}
              placeholder="A short summary of what you're really after…"
              onBlur={(e) => e.target.value !== profile.brief && save.mutate({ brief: e.target.value })}
            />
          </div>
        </div>
      )}
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-sm font-medium">{label}</span>
      {children}
    </div>
  )
}
