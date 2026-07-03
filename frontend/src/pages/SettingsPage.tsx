import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { History, ListChecks, RotateCcw, X } from 'lucide-react'
import { api } from '../lib/api'
import type { Profile } from '../lib/types'

// Friendly labels for the structured criteria keys the agent uses
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

export default function SettingsPage() {
  const qc = useQueryClient()
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [saveError, setSaveError] = useState('')

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
  const createProfile = useMutation({
    mutationFn: () => api.post<Profile>('/api/profiles', { name: 'New search', mode: 'buy' }),
    onSuccess: (created) => {
      setSelectedId(created.id)
      qc.invalidateQueries({ queryKey: ['profiles'] })
    },
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

  const location = profile?.locations?.[0]
  const radiusMiles = location?.radius_km
    ? RADIUS_MILES.reduce((best, m) =>
        Math.abs(m * KM_PER_MILE - location.radius_km) < Math.abs(best * KM_PER_MILE - location.radius_km) ? m : best,
      RADIUS_MILES[0])
    : 5

  function saveLocation(label: string, miles: number) {
    if (!label.trim()) {
      save.mutate({ locations: [] })
      return
    }
    save.mutate({
      locations: [{ label: label.trim(), radius_km: Math.round(miles * KM_PER_MILE * 10) / 10 } as never],
    })
  }

  return (
    <div className="mx-auto max-w-3xl p-4 md:p-6">
      <h1 className="mb-4 text-2xl font-bold tracking-tight">Search Profiles</h1>

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
          onClick={() => createProfile.mutate()}
          className="rounded-full border border-dashed border-stone-400 px-3.5 py-1.5 text-sm text-stone-500 hover:border-brand-500 hover:text-brand-600"
        >
          + New profile
        </button>
      </div>

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
          <Row label="Location">
            <div className="flex flex-wrap items-center gap-2">
              <input
                className="input w-44"
                placeholder="Town or district, e.g. CB1"
                key={`loc-${profile.id}-${location?.label ?? ''}`}
                defaultValue={location?.label ?? ''}
                onBlur={(e) => {
                  if (e.target.value.trim() !== (location?.label ?? '')) {
                    saveLocation(e.target.value, radiusMiles)
                  }
                }}
              />
              <span className="text-sm text-stone-400">within</span>
              <select
                className="input"
                key={`rad-${profile.id}-${radiusMiles}`}
                defaultValue={radiusMiles}
                onChange={(e) => location?.label && saveLocation(location.label, Number(e.target.value))}
              >
                {RADIUS_MILES.map((m) => (
                  <option key={m} value={m}>
                    {m} mi
                  </option>
                ))}
              </select>
            </div>
          </Row>
          {location?.lat != null && (
            <p className="-mt-3 text-right text-[11px] text-stone-400">
              📍 pinned at {location.lat.toFixed(3)}, {location.lng.toFixed(3)}
            </p>
          )}
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
          <Row label="Bedrooms (min)">
            <input
              className="input w-24"
              type="number"
              key={`beds-${profile.id}`}
              defaultValue={profile.min_beds ?? ''}
              onBlur={(e) => save.mutate({ min_beds: e.target.value ? +e.target.value : null })}
            />
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
              {['telegram', 'email'].map((ch) => {
                const on = profile.alert_channels.includes(ch)
                return (
                  <button
                    key={ch}
                    onClick={() =>
                      save.mutate({
                        alert_channels: on
                          ? profile.alert_channels.filter((c) => c !== ch)
                          : [...profile.alert_channels, ch],
                      })
                    }
                    className={`rounded-full px-3 py-1 text-sm font-medium capitalize ${
                      on
                        ? 'bg-brand-600 text-white'
                        : 'bg-stone-100 text-stone-500 dark:bg-stone-800'
                    }`}
                  >
                    {ch}
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

          <div className="border-t border-stone-200 pt-4 dark:border-stone-800">
            <p className="mb-2 text-xs text-stone-400">
              Must-haves and nice-to-haves are easiest to set via the Agent chat — it fills in
              the structured criteria for you. Criteria edits automatically re-score all
              properties.
            </p>
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
        <div className="mt-5 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
          <h2 className="mb-1 flex items-center gap-2 font-semibold">
            <ListChecks size={16} /> Requirements
          </h2>
          <p className="mb-3 text-xs text-stone-400">
            Everything this search filters and scores on. Remove items directly — no need to ask
            the agent. Changes re-score all properties; the History section below can undo.
          </p>

          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-stone-400">
            Must-haves <span className="normal-case">(hard filters)</span>
          </h3>
          <div className="mb-4 flex flex-wrap gap-1.5">
            {Object.entries(profile.must_haves ?? {}).filter(([, v]) => v).map(([key]) => (
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
            {Object.values(profile.must_haves ?? {}).filter(Boolean).length === 0 && (
              <span className="text-sm text-stone-400">None — everything passes the hard filters.</span>
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
            {(profile.nice_to_haves ?? []).length === 0 && (
              <p className="px-2 text-sm text-stone-400">
                None — scoring uses price value only. Add preferences via the Agent chat.
              </p>
            )}
          </div>
        </div>
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

