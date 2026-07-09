import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { LayoutGrid, Loader2, Map as MapIcon, RefreshCw, Sparkles, X } from 'lucide-react'
import { api } from '../lib/api'
import type { Profile, PropertyCard } from '../lib/types'
import PropertyCardView from '../components/PropertyCardView'
import MapView from '../components/MapView'

interface ScanStatus {
  state: 'idle' | 'running' | 'done' | 'error'
  progress?: string | null
  error?: string | null
  failures?: number
  steps?: number
  started_at?: string
  finished_at?: string
  next_scheduled?: string | null
}

function ago(iso?: string): string {
  if (!iso) return ''
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const hrs = Math.round(mins / 60)
  return hrs < 24 ? `${hrs}h ago` : `${Math.round(hrs / 24)}d ago`
}

function at(iso?: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

const SORTS = [
  { id: 'score', label: 'Best match' },
  { id: 'newest', label: 'Newest' },
  { id: 'price_asc', label: 'Price ↑' },
  { id: 'price_desc', label: 'Price ↓' },
  { id: 'access', label: 'Access' },
]

// Feed view state survives navigating away and back (sessionStorage = per-tab)
const FEED_STATE_KEY = 'hs-feed-state'
const FEED_SCROLL_KEY = 'hs-feed-scroll'

function loadFeedState(): {
  sort?: string
  view?: 'grid' | 'map'
  profileId?: number | null
  area?: string | null
  onlyNew?: boolean
} {
  try {
    return JSON.parse(sessionStorage.getItem(FEED_STATE_KEY) ?? '{}')
  } catch {
    return {}
  }
}

const NEW_WINDOW_MS = 48 * 3600 * 1000

// A card counts as "new" the same way its badge does: recently first-seen and unviewed
function isNewCard(card: PropertyCard): boolean {
  if (card.viewed) return false
  if (!card.first_seen) return false
  return Date.now() - new Date(card.first_seen).getTime() < NEW_WINDOW_MS
}

export default function FeedPage() {
  const stored = loadFeedState()
  const [profileId, setProfileId] = useState<number | null>(stored.profileId ?? null)
  const [sort, setSort] = useState(stored.sort ?? 'score')
  const [view, setView] = useState<'grid' | 'map'>(stored.view ?? 'grid')
  const [onlyNew, setOnlyNew] = useState<boolean>(stored.onlyNew ?? false)
  const [searchParams, setSearchParams] = useSearchParams()
  const urlArea = searchParams.get('area')
  const [areaFilter, setAreaFilter] = useState<string | null>(urlArea ?? stored.area ?? null)

  // Arriving via an Areas-page link (?area=CB1) overrides whatever was stored
  useEffect(() => {
    if (urlArea && urlArea !== areaFilter) setAreaFilter(urlArea)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [urlArea])

  function clearArea() {
    setAreaFilter(null)
    if (urlArea) setSearchParams({})
  }

  useEffect(() => {
    sessionStorage.setItem(
      FEED_STATE_KEY,
      JSON.stringify({ sort, view, profileId, area: areaFilter, onlyNew }),
    )
  }, [sort, view, profileId, areaFilter, onlyNew])

  // Scroll persistence: save as the user scrolls, restore once the feed has rendered
  const restoredScroll = useRef(false)
  useEffect(() => {
    const el = document.getElementById('hs-main')
    if (!el) return
    const onScroll = () => sessionStorage.setItem(FEED_SCROLL_KEY, String(el.scrollTop))
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  const qc = useQueryClient()
  const profiles = useQuery({
    queryKey: ['profiles'],
    queryFn: () => api.get<Profile[]>('/api/profiles'),
  })

  const activeProfile =
    profiles.data?.find((p) => p.id === profileId) ?? profiles.data?.find((p) => p.active) ?? null

  const scan = useQuery({
    queryKey: ['scan-status'],
    queryFn: () => api.get<ScanStatus>('/api/system/scan-status'),
    refetchInterval: (query) => (query.state.data?.state === 'running' ? 4000 : 30000),
  })
  const scanning = scan.data?.state === 'running'

  // The map is an overview: plot every match (incl. saved ones ranked far down the
  // list) plus any saved property that no longer matches this profile. The grid stays
  // a manageable top slice of current matches only.
  const isMap = view === 'map'
  const feedLimit = isMap ? 2000 : 60
  const feed = useQuery({
    queryKey: ['feed', activeProfile?.id ?? 'all', sort, areaFilter, feedLimit, isMap],
    queryFn: () =>
      api.get<{ total: number; items: PropertyCard[] }>(
        `/api/properties?sort=${sort}&limit=${feedLimit}${activeProfile ? `&profile_id=${activeProfile.id}` : ''}${areaFilter ? `&outcode=${encodeURIComponent(areaFilter)}` : ''}${isMap ? '&include_saved=true' : ''}`,
      ),
    refetchInterval: scanning ? 10000 : false,
  })

  const scanNow = useMutation({
    mutationFn: () => api.post('/api/system/poll-now'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scan-status'] }),
  })

  useEffect(() => {
    if (restoredScroll.current || !feed.data) return
    restoredScroll.current = true
    const el = document.getElementById('hs-main')
    const saved = Number(sessionStorage.getItem(FEED_SCROLL_KEY) || 0)
    if (el && saved > 0) {
      requestAnimationFrame(() => {
        el.scrollTop = saved
      })
    }
  }, [feed.data])

  const savedIds = useQuery({
    queryKey: ['saved-ids'],
    queryFn: () => api.get<number[]>('/api/lists/saved-property-ids'),
  })
  const savedSet = new Set(savedIds.data ?? [])

  const allItems = feed.data?.items ?? []
  const newCount = allItems.filter(isNewCard).length
  const items = onlyNew ? allItems.filter(isNewCard) : allItems

  return (
    <div className="mx-auto max-w-7xl p-4 md:p-6">
      <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-2xl font-bold tracking-tight">Homes for you</h1>
            {areaFilter && (
              <button
                onClick={clearArea}
                title="Clear area filter"
                className="flex items-center gap-1 rounded-full bg-brand-600 px-3 py-1 text-xs font-semibold text-white hover:bg-brand-700"
              >
                in {areaFilter} <X size={13} />
              </button>
            )}
          </div>
          <p className="text-sm text-stone-500">
            {feed.data
              ? onlyNew
                ? `${items.length} new of ${feed.data.total}`
                : `${feed.data.total} matching properties`
              : 'Loading…'}
            {!scanning && scan.data?.finished_at && (
              <span className="text-stone-400"> · last scan {ago(scan.data.finished_at)}</span>
            )}
            {!scanning && scan.data?.next_scheduled && (
              <span className="text-stone-400"> · next auto-scan ~{at(scan.data.next_scheduled)}</span>
            )}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => scanNow.mutate()}
            disabled={scanning}
            title="Scan the portals for new listings now"
            className="flex items-center gap-1.5 rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-xs font-semibold text-stone-600 hover:bg-stone-50 disabled:opacity-60 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300"
          >
            {scanning ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            {scanning ? 'Scanning…' : 'Scan now'}
          </button>
          <button
            onClick={() => setOnlyNew((v) => !v)}
            title="Show only new, unseen listings (added in the last 48h)"
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
              onlyNew
                ? 'border-brand-600 bg-brand-600 text-white'
                : 'border-stone-300 bg-white text-stone-600 hover:bg-stone-50 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300'
            }`}
          >
            <Sparkles size={13} /> Only new
            {newCount > 0 && (
              <span
                className={`rounded-full px-1.5 text-[10px] ${
                  onlyNew ? 'bg-white/25' : 'bg-brand-100 text-brand-700 dark:bg-brand-900 dark:text-brand-300'
                }`}
              >
                {newCount}
              </span>
            )}
          </button>
          {profiles.data && profiles.data.length > 0 && (
            <select
              className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-sm dark:border-stone-700 dark:bg-stone-900"
              value={activeProfile?.id ?? ''}
              onChange={(e) => setProfileId(Number(e.target.value))}
            >
              {profiles.data.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.mode})
                </option>
              ))}
            </select>
          )}
          <div className="flex rounded-lg border border-stone-300 bg-white p-0.5 dark:border-stone-700 dark:bg-stone-900">
            {SORTS.map((s) => (
              <button
                key={s.id}
                onClick={() => setSort(s.id)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
                  sort === s.id
                    ? 'bg-brand-600 text-white'
                    : 'text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <div className="flex rounded-lg border border-stone-300 bg-white p-0.5 dark:border-stone-700 dark:bg-stone-900">
            <button
              onClick={() => setView('grid')}
              aria-label="Grid view"
              className={`rounded-md px-2 py-1 ${view === 'grid' ? 'bg-brand-600 text-white' : 'text-stone-500'}`}
            >
              <LayoutGrid size={15} />
            </button>
            <button
              onClick={() => setView('map')}
              aria-label="Map view"
              className={`rounded-md px-2 py-1 ${view === 'map' ? 'bg-brand-600 text-white' : 'text-stone-500'}`}
            >
              <MapIcon size={15} />
            </button>
          </div>
        </div>
      </header>

      {scanning && (
        <div className="mb-4 flex items-center gap-2.5 rounded-xl bg-brand-50 px-4 py-3 text-sm text-brand-800 dark:bg-brand-950 dark:text-brand-200">
          <Loader2 size={16} className="shrink-0 animate-spin" />
          <span>
            Scanning the portals — <b>{scan.data?.progress ?? 'starting'}</b>. New homes appear
            here as each portal finishes; scoring runs at the end. You can leave this page.
          </span>
        </div>
      )}
      {scan.data?.state === 'error' && (
        <p className="mb-4 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          Last scan hit a problem: {scan.data.error ?? 'unknown'} — check the Status page.
        </p>
      )}
      {scan.data?.state === 'done' && (scan.data.failures ?? 0) > 0 && (
        <p className="mb-4 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200">
          Last scan completed, but {scan.data.failures} of {scan.data.steps} portal searches
          failed — see the Status page for details.
        </p>
      )}

      {profiles.data && profiles.data.length === 0 && (
        <div className="rounded-2xl border border-dashed border-stone-300 p-10 text-center dark:border-stone-700">
          <h2 className="text-lg font-semibold">No search profile yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-stone-500">
            Tell the agent what you're looking for and it will set everything up — or create one
            manually under Search Profiles.
          </p>
          <a
            href="/chat"
            className="mt-4 inline-block rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white hover:bg-brand-700"
          >
            Talk to the agent
          </a>
        </div>
      )}

      {feed.data && feed.data.items.length === 0 && profiles.data && profiles.data.length > 0 && (
        <div className="rounded-2xl border border-dashed border-stone-300 p-10 text-center text-sm text-stone-500 dark:border-stone-700">
          {scanning
            ? 'Scan in progress — matches will appear here shortly.'
            : 'Nothing found yet — hit "Scan now" above, or wait for the next automatic scan.'}
        </div>
      )}

      {onlyNew && feed.data && items.length === 0 && allItems.length > 0 && (
        <div className="rounded-2xl border border-dashed border-stone-300 p-10 text-center text-sm text-stone-500 dark:border-stone-700">
          Nothing new right now — every match here has been seen before.{' '}
          <button onClick={() => setOnlyNew(false)} className="font-semibold text-brand-600 hover:underline">
            Show all {allItems.length}
          </button>
        </div>
      )}

      {view === 'map' && feed.data ? (
        <MapView cards={items} profileId={activeProfile?.id} savedSet={savedSet} />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {items.map((card) => (
            <PropertyCardView
              key={card.id}
              card={card}
              profileId={activeProfile?.id}
              saved={savedSet.has(card.id)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
