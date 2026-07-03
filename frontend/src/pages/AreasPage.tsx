import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle2, Compass, Home, Loader2, MapPin, Plus, RefreshCw, X,
} from 'lucide-react'
import { api, ApiError } from '../lib/api'
import type { AreaInfo, AreaSearchInfo, Profile, ResearchStatus } from '../lib/types'

const SUBSCORE_LABELS: Record<string, string> = {
  transport: 'Transport',
  safety: 'Safety',
  amenities: 'Amenities',
  green: 'Green space',
  schools: 'Schools',
  affordability: 'Affordability',
}

const RANGES = [5, 10, 15, 20, 30]

function ago(iso?: string | null): string {
  if (!iso) return ''
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.round(hrs / 24)}d ago`
}

export default function AreasPage() {
  const qc = useQueryClient()
  const [profileId, setProfileId] = useState<number | null>(null)
  const [selectedSearchId, setSelectedSearchId] = useState<number | null>(null)
  const [showNew, setShowNew] = useState(false)
  const [location, setLocation] = useState('')
  const [radius, setRadius] = useState(15)
  const [submitError, setSubmitError] = useState('')
  const [withMatchesOnly, setWithMatchesOnly] = useState(false)

  const profiles = useQuery({
    queryKey: ['profiles'],
    queryFn: () => api.get<Profile[]>('/api/profiles'),
  })
  const activeProfile = profiles.data?.find((p) => p.id === profileId) ?? profiles.data?.[0] ?? null

  const searches = useQuery({
    queryKey: ['area-searches', activeProfile?.id],
    queryFn: () => api.get<AreaSearchInfo[]>(`/api/areas/searches?profile_id=${activeProfile!.id}`),
    enabled: !!activeProfile,
  })
  const selected =
    searches.data?.find((s) => s.id === selectedSearchId) ??
    searches.data?.find((s) => s.result_count > 0 || s.source === 'profile') ??
    searches.data?.[0] ??
    null

  const status = useQuery({
    queryKey: ['research-status', selected?.id],
    queryFn: () => api.get<ResearchStatus>(`/api/areas/status?search_id=${selected!.id}`),
    enabled: !!selected,
    refetchInterval: (query) => (query.state.data?.state === 'running' ? 3000 : false),
  })
  const running = status.data?.state === 'running'

  const areas = useQuery({
    queryKey: ['areas', selected?.id],
    queryFn: () => api.get<AreaInfo[]>(`/api/areas?search_id=${selected!.id}`),
    enabled: !!selected,
    refetchInterval: running ? 8000 : false,
  })

  function refreshSearches() {
    qc.invalidateQueries({ queryKey: ['area-searches', activeProfile?.id] })
  }

  const createSearch = useMutation({
    mutationFn: () =>
      api.post<AreaSearchInfo>('/api/areas/searches', {
        profile_id: activeProfile!.id,
        location: location.trim(),
        radius_miles: radius,
      }),
    onMutate: () => setSubmitError(''),
    onSuccess: (created) => {
      setSelectedSearchId(created.id)
      setShowNew(false)
      setLocation('')
      refreshSearches()
      qc.invalidateQueries({ queryKey: ['research-status', created.id] })
    },
    onError: (err) => setSubmitError(err instanceof ApiError ? err.message : 'Failed to start'),
  })

  const rerun = useMutation({
    mutationFn: (searchId: number) => api.post(`/api/areas/searches/${searchId}/run`),
    onMutate: () => setSubmitError(''),
    onSuccess: () => {
      refreshSearches()
      qc.invalidateQueries({ queryKey: ['research-status', selected?.id] })
    },
    onError: (err) => setSubmitError(err instanceof ApiError ? err.message : 'Failed to start'),
  })

  const deleteSearch = useMutation({
    mutationFn: (searchId: number) => api.delete(`/api/areas/searches/${searchId}`),
    onSuccess: () => {
      setSelectedSearchId(null)
      refreshSearches()
    },
  })

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Neighbourhood research</h1>
          <p className="text-sm text-stone-500">
            Saved area searches, ranked by your priorities and budget.
          </p>
        </div>
        {profiles.data && profiles.data.length > 1 && (
          <select
            className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm dark:border-stone-700 dark:bg-stone-900"
            value={activeProfile?.id ?? ''}
            onChange={(e) => {
              setProfileId(Number(e.target.value))
              setSelectedSearchId(null)
            }}
          >
            {profiles.data.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Saved searches */}
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {searches.data?.map((s) => {
          const isSelected = selected?.id === s.id
          const isRunning = s.status?.state === 'running'
          return (
            <span
              key={s.id}
              className={`flex items-stretch overflow-hidden rounded-full text-sm font-medium ring-1 transition ${
                isSelected
                  ? 'bg-brand-600 text-white ring-brand-600'
                  : 'bg-white text-stone-600 ring-stone-300 dark:bg-stone-900 dark:text-stone-300 dark:ring-stone-700'
              }`}
            >
              <button
                onClick={() => setSelectedSearchId(s.id)}
                className={`flex items-center gap-1.5 py-1.5 pl-3.5 ${
                  s.source === 'custom' ? 'pr-1.5' : 'pr-3.5'
                } ${isSelected ? '' : 'hover:bg-stone-50 dark:hover:bg-stone-800'}`}
              >
                {s.source === 'profile' && <MapPin size={13} />}
                {isRunning && <Loader2 size={13} className="animate-spin" />}
                {s.name}
              </button>
              {s.source === 'custom' && (
                <button
                  onClick={() => deleteSearch.mutate(s.id)}
                  title="Delete this saved search"
                  className={`flex items-center pl-1 pr-2.5 transition ${
                    isSelected
                      ? 'text-white/70 hover:text-white'
                      : 'text-stone-400 hover:text-red-500'
                  }`}
                >
                  <X size={13} />
                </button>
              )}
            </span>
          )
        })}
        <button
          onClick={() => setShowNew((v) => !v)}
          className="flex items-center gap-1 rounded-full border border-dashed border-stone-400 px-3.5 py-1.5 text-sm text-stone-500 hover:border-brand-500 hover:text-brand-600"
        >
          <Plus size={14} /> New search
        </button>
      </div>

      {/* New search form */}
      {showNew && (
        <div className="mb-4 flex flex-wrap items-center gap-2 rounded-2xl border border-stone-200 bg-white p-3 dark:border-stone-800 dark:bg-stone-900">
          <input
            className="min-w-40 flex-1 rounded-lg border border-stone-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-brand-500 dark:border-stone-700"
            placeholder="Town or postcode district — e.g. Cambridge, CB1"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && location.trim() && createSearch.mutate()}
            autoFocus
          />
          <label className="flex items-center gap-1.5 text-sm text-stone-500">
            within
            <select
              className="rounded-lg border border-stone-300 bg-white px-2 py-2 text-sm dark:border-stone-700 dark:bg-stone-900"
              value={radius}
              onChange={(e) => setRadius(Number(e.target.value))}
            >
              {RANGES.map((r) => (
                <option key={r} value={r}>
                  {r} miles
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => location.trim() && createSearch.mutate()}
            disabled={!location.trim() || createSearch.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
          >
            <Compass size={15} />
            Research & save
          </button>
        </div>
      )}
      {submitError && <p className="mb-3 text-sm text-red-500">{submitError}</p>}

      {/* Selected search: status line + refresh */}
      {selected && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs text-stone-400">
            {running ? (
              <span className="flex items-center gap-2 rounded-xl bg-brand-50 px-3 py-2 text-sm text-brand-800 dark:bg-brand-950 dark:text-brand-200">
                <Loader2 size={14} className="animate-spin" />
                Researching… <b>{status.data?.progress ?? 'starting'}</b> — you can leave this page.
              </span>
            ) : status.data?.state === 'error' ? (
              <span className="flex items-center gap-1.5 rounded-xl bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
                <AlertTriangle size={14} /> {status.data.error ?? 'Research failed.'}
              </span>
            ) : selected.last_run_at ? (
              <span className="flex items-center gap-1.5">
                <CheckCircle2 size={13} />
                Last run {ago(selected.last_run_at)} · {areas.data?.length ?? selected.result_count} areas
                {selected.stale && (
                  <span className="ml-1 rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                    profile location changed — refresh
                  </span>
                )}
              </span>
            ) : (
              <span>Never run yet.</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {(areas.data?.length ?? 0) > 0 && (
              <button
                onClick={() => setWithMatchesOnly((v) => !v)}
                className={`flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                  withMatchesOnly
                    ? 'bg-brand-600 text-white'
                    : 'bg-white text-stone-600 ring-1 ring-stone-300 hover:bg-stone-50 dark:bg-stone-900 dark:text-stone-300 dark:ring-stone-700'
                }`}
              >
                <Home size={13} />
                With matching homes only
              </button>
            )}
            <button
              onClick={() => rerun.mutate(selected.id)}
              disabled={running || rerun.isPending}
              className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-1.5 text-xs font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
            >
              <RefreshCw size={13} className={running ? 'animate-spin' : ''} />
              {selected.last_run_at ? 'Refresh' : 'Run research'}
            </button>
          </div>
        </div>
      )}

      <div className="space-y-4">
        {areas.data
          ?.filter((a) => !withMatchesOnly || a.match_count > 0)
          .map((area, i) => (
            <div
              key={area.id}
              className="rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="flex items-center gap-2 font-semibold">
                    <span className="text-stone-400">#{i + 1}</span>
                    {area.name || area.code}
                    <span
                      className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                        area.match_count > 0
                          ? 'bg-brand-100 text-brand-700 dark:bg-brand-900 dark:text-brand-300'
                          : 'bg-stone-100 text-stone-400 dark:bg-stone-800'
                      }`}
                    >
                      {area.match_count} matching {area.match_count === 1 ? 'home' : 'homes'}
                    </span>
                  </h2>
                  <p className="text-xs text-stone-400">
                    {area.listing_stats?.median_price
                      ? `median £${Number(area.listing_stats.median_price).toLocaleString('en-GB')}`
                      : 'no price data yet'}
                  </p>
                </div>
                <div className="text-right">
                  <span className="text-2xl font-bold text-brand-600">
                    {Math.round((area.scores.total ?? 0) * 100)}
                  </span>
                  <span className="text-xs text-stone-400"> / 100</span>
                </div>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1.5 sm:grid-cols-3">
                {Object.entries(SUBSCORE_LABELS).map(([key, label]) =>
                  area.scores[key] !== undefined ? (
                    <div key={key} className="flex items-center gap-2 text-xs">
                      <span className="w-20 shrink-0 text-stone-500">{label}</span>
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700">
                        <div
                          className="h-full rounded-full bg-brand-500"
                          style={{ width: `${(area.scores[key] ?? 0) * 100}%` }}
                        />
                      </div>
                    </div>
                  ) : null,
                )}
              </div>
              {area.narrative && (
                <p className="mt-3 whitespace-pre-line text-[15px] leading-relaxed text-stone-700 dark:text-stone-300">
                  {area.narrative}
                </p>
              )}
              {area.match_count > 0 && (
                <Link
                  to={`/?area=${encodeURIComponent(area.code)}`}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-semibold text-white hover:bg-brand-700"
                >
                  <Home size={14} />
                  View {area.match_count} {area.match_count === 1 ? 'home' : 'homes'} in {area.code}
                </Link>
              )}
            </div>
          ))}
        {areas.data?.length === 0 && !running && (
          <p className="rounded-2xl border border-dashed border-stone-300 p-10 text-center text-sm text-stone-500 dark:border-stone-700">
            {selected?.source === 'profile'
              ? 'Your profile’s pinned search hasn’t run yet — hit "Run research" above.'
              : 'No results for this search yet.'}
          </p>
        )}
      </div>
    </div>
  )
}
