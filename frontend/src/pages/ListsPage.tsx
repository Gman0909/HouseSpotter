import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bath, BedDouble, ExternalLink, Filter, Plus, Ruler, TrainFront, Trash2, TrendingDown, X, Zap,
} from 'lucide-react'
import { api } from '../lib/api'
import type { PropertyCard, SavedListInfo } from '../lib/types'
import { formatPrice, ScrubGallery } from '../components/PropertyCardView'

interface ListItemRow {
  item: { id: number; note: string; status: string; added_at: string }
  card: PropertyCard
  description: string
  delisted: boolean
  property_id: number
}

const STATUSES = ['', 'want to view', 'viewing booked', 'viewed', 'offer made', 'ruled out']

// Hidden statuses persist per-tab, like the other view state
const HIDDEN_KEY = 'hs-lists-hidden-statuses'
const SORT_KEY = 'hs-lists-sort'

function loadHidden(): Set<string> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(HIDDEN_KEY) ?? '[]'))
  } catch {
    return new Set()
  }
}

const SORTS: { id: string; label: string; cmp: (a: ListItemRow, b: ListItemRow) => number }[] = [
  {
    id: 'saved_desc',
    label: 'Newest saved',
    cmp: (a, b) => b.item.added_at.localeCompare(a.item.added_at),
  },
  {
    id: 'saved_asc',
    label: 'Oldest saved',
    cmp: (a, b) => a.item.added_at.localeCompare(b.item.added_at),
  },
  {
    id: 'price_asc',
    label: 'Price ↑',
    cmp: (a, b) => (a.card.price ?? Infinity) - (b.card.price ?? Infinity),
  },
  {
    id: 'price_desc',
    label: 'Price ↓',
    cmp: (a, b) => (b.card.price ?? -Infinity) - (a.card.price ?? -Infinity),
  },
  {
    id: 'access',
    label: 'Access',
    cmp: (a, b) => (b.card.access_score ?? -1) - (a.card.access_score ?? -1),
  },
]

function savedAgo(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (days < 1) return 'today'
  if (days === 1) return 'yesterday'
  return `${days}d ago`
}

function hasPriceDrop(history: { price: number }[] | null | undefined): boolean {
  if (!history || history.length < 2) return false
  return history[history.length - 1].price < history[history.length - 2].price
}

export default function ListsPage() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<number | null>(null)
  const [newName, setNewName] = useState('')
  const [showFilters, setShowFilters] = useState(false)
  const [hidden, setHidden] = useState<Set<string>>(loadHidden)

  function toggleStatus(status: string) {
    setHidden((prev) => {
      const next = new Set(prev)
      if (next.has(status)) next.delete(status)
      else next.add(status)
      sessionStorage.setItem(HIDDEN_KEY, JSON.stringify([...next]))
      return next
    })
  }

  function clearFilters() {
    setHidden(new Set())
    sessionStorage.removeItem(HIDDEN_KEY)
  }

  const lists = useQuery({
    queryKey: ['lists'],
    queryFn: () => api.get<SavedListInfo[]>('/api/lists'),
  })
  const activeId = selected ?? lists.data?.[0]?.id ?? null

  const items = useQuery({
    queryKey: ['list-items', activeId],
    queryFn: () => api.get<ListItemRow[]>(`/api/lists/${activeId}/items`),
    enabled: activeId !== null,
  })

  const createList = useMutation({
    mutationFn: (name: string) => api.post('/api/lists', { name }),
    onSuccess: () => {
      setNewName('')
      qc.invalidateQueries({ queryKey: ['lists'] })
    },
  })
  const deleteList = useMutation({
    mutationFn: (id: number) => api.delete(`/api/lists/${id}`),
    onSuccess: () => {
      setSelected(null)
      qc.invalidateQueries({ queryKey: ['lists'] })
      qc.invalidateQueries({ queryKey: ['saved-ids'] })
      qc.invalidateQueries({ queryKey: ['saved'] })
    },
  })
  const removeItem = useMutation({
    mutationFn: (itemId: number) => api.delete(`/api/lists/${activeId}/items/${itemId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['list-items', activeId] })
      qc.invalidateQueries({ queryKey: ['lists'] })
      qc.invalidateQueries({ queryKey: ['saved-ids'] })
      qc.invalidateQueries({ queryKey: ['saved'] })
    },
  })
  const patchItem = useMutation({
    mutationFn: ({ itemId, ...body }: { itemId: number; note?: string; status?: string }) =>
      api.patch(`/api/lists/${activeId}/items/${itemId}`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['list-items', activeId] }),
  })

  const [sortId, setSortId] = useState<string>(() => sessionStorage.getItem(SORT_KEY) ?? 'saved_desc')

  function changeSort(id: string) {
    setSortId(id)
    sessionStorage.setItem(SORT_KEY, id)
  }

  const filtersActive = hidden.size > 0
  const sort = SORTS.find((s) => s.id === sortId) ?? SORTS[0]
  const visibleRows = (items.data ?? [])
    .filter((r) => !hidden.has(r.item.status))
    .sort(sort.cmp)

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6">
      <h1 className="mb-4 text-2xl font-bold tracking-tight">Saved lists</h1>

      <div className="mb-5 flex flex-wrap items-center gap-2">
        {lists.data?.map((list) => (
          <button
            key={list.id}
            onClick={() => setSelected(list.id)}
            className={`rounded-full px-3.5 py-1.5 text-sm font-medium transition ${
              activeId === list.id
                ? 'bg-brand-600 text-white'
                : 'bg-white text-stone-600 hover:bg-stone-50 dark:bg-stone-900 dark:text-stone-300'
            }`}
          >
            {list.name} <span className="opacity-60">({list.count})</span>
          </button>
        ))}
        <div className="flex items-center gap-1">
          <input
            className="w-32 rounded-full border border-stone-300 bg-transparent px-3 py-1.5 text-sm dark:border-stone-700"
            placeholder="New list…"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && newName.trim() && createList.mutate(newName.trim())}
          />
          <button
            onClick={() => newName.trim() && createList.mutate(newName.trim())}
            className="rounded-full bg-brand-600 p-1.5 text-white hover:bg-brand-700"
          >
            <Plus size={16} />
          </button>
        </div>
      </div>

      {activeId !== null && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-2.5">
            <div className="flex rounded-lg border border-stone-300 bg-white p-0.5 dark:border-stone-700 dark:bg-stone-900">
              {SORTS.map((s) => (
                <button
                  key={s.id}
                  onClick={() => changeSort(s.id)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${
                    sortId === s.id
                      ? 'bg-brand-600 text-white'
                      : 'text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => setShowFilters((v) => !v)}
              className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition ${
                filtersActive
                  ? 'border-brand-600 bg-brand-600 text-white'
                  : showFilters
                    ? 'border-brand-400 bg-white text-brand-700 dark:bg-stone-900'
                    : 'border-stone-300 bg-white text-stone-600 hover:bg-stone-50 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300'
              }`}
            >
              <Filter size={13} /> Display filters
              {filtersActive && (
                <span className="rounded-full bg-white/25 px-1.5 text-[10px]">{hidden.size} hidden</span>
              )}
            </button>
            {filtersActive && (
              <span className="flex items-center gap-2 text-xs font-medium text-brand-700 dark:text-brand-300">
                Showing {visibleRows.length} of {items.data?.length ?? 0} saved
                <button
                  onClick={clearFilters}
                  className="flex items-center gap-0.5 rounded-full bg-brand-100 px-2 py-0.5 font-semibold text-brand-700 hover:bg-brand-200 dark:bg-brand-900 dark:text-brand-300"
                >
                  clear <X size={11} />
                </button>
              </span>
            )}
          </div>

          {showFilters && (
            <div className="mb-4 flex flex-wrap gap-x-5 gap-y-2 rounded-2xl border border-stone-200 bg-white p-4 dark:border-stone-800 dark:bg-stone-900">
              {STATUSES.map((status) => {
                const count = items.data?.filter((r) => r.item.status === status).length ?? 0
                return (
                  <label
                    key={status || 'none'}
                    className="flex cursor-pointer items-center gap-1.5 text-sm text-stone-700 dark:text-stone-300"
                  >
                    <input
                      type="checkbox"
                      checked={!hidden.has(status)}
                      onChange={() => toggleStatus(status)}
                      className="accent-brand-600"
                    />
                    <span className={hidden.has(status) ? 'text-stone-400 line-through' : ''}>
                      {status || 'no status'}
                    </span>
                    <span className="text-xs text-stone-400">({count})</span>
                  </label>
                )
              })}
            </div>
          )}

          <div className="space-y-4">
            {visibleRows.map((row) => {
              const c = row.card
              return (
                <div
                  key={row.item.id}
                  className="group flex flex-col gap-4 rounded-2xl border border-stone-200 bg-white p-4 sm:flex-row dark:border-stone-800 dark:bg-stone-900"
                >
                  <Link
                    to={`/property/${row.property_id}`}
                    className="group relative block h-44 w-full shrink-0 overflow-hidden rounded-xl bg-stone-200 sm:h-40 sm:w-56 dark:bg-stone-800"
                  >
                    <ScrubGallery
                      images={c.images?.length ? c.images : c.image ? [c.image] : []}
                      alt={c.address}
                    />
                    {row.delisted && (
                      <span className="absolute left-2 top-2 rounded-full bg-stone-800/90 px-2 py-0.5 text-[11px] font-semibold text-white">
                        No longer listed
                      </span>
                    )}
                    {!row.delisted && hasPriceDrop(c.price_history) && (
                      <span className="absolute left-2 top-2 flex items-center gap-1 rounded-full bg-amber-500 px-2 py-0.5 text-[11px] font-semibold text-white shadow">
                        <TrendingDown size={11} /> Price drop
                      </span>
                    )}
                  </Link>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-baseline gap-x-2">
                          <span className="text-lg font-bold tracking-tight">{formatPrice(c)}</span>
                          {c.price_qualifier && c.price_qualifier !== 'pcm' && (
                            <span className="text-[11px] text-stone-500">{c.price_qualifier}</span>
                          )}
                          {hasPriceDrop(c.price_history) && (
                            <span className="text-xs font-medium text-amber-600">
                              was £{c.price_history[c.price_history.length - 2].price.toLocaleString('en-GB')}
                            </span>
                          )}
                        </div>
                        <Link
                          to={`/property/${row.property_id}`}
                          className="mt-0.5 block truncate text-sm font-medium text-stone-700 hover:underline dark:text-stone-300"
                        >
                          {c.address}
                        </Link>
                      </div>
                      <button
                        onClick={() => removeItem.mutate(row.item.id)}
                        title="Remove from list"
                        className="shrink-0 text-stone-400 hover:text-red-500"
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>

                    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-500">
                      {c.beds != null && (
                        <span className="flex items-center gap-1"><BedDouble size={14} /> {c.beds}</span>
                      )}
                      {c.baths != null && (
                        <span className="flex items-center gap-1"><Bath size={14} /> {c.baths}</span>
                      )}
                      {c.property_type && <span className="capitalize">{c.property_type}</span>}
                      {c.tenure && <span className="capitalize">{c.tenure}</span>}
                      {c.epc && <span>EPC {c.epc}</span>}
                      {c.floor_area_sqm != null && (
                        <span className="flex items-center gap-1">
                          <Ruler size={13} /> {Math.round(c.floor_area_sqm)} m²
                          {c.price != null && c.mode === 'buy' && (
                            <span className="text-stone-400">
                              (£{Math.round(c.price / c.floor_area_sqm).toLocaleString('en-GB')}/m²)
                            </span>
                          )}
                        </span>
                      )}
                      {c.station_walk_minutes !== null && c.station_walk_minutes <= 25 && (
                        <span
                          className="flex items-center gap-0.5 font-medium"
                          title={`${c.station_name} — about ${Math.round(c.station_walk_minutes)} min walk`}
                        >
                          <TrainFront size={12} /> {c.station_name} {Math.round(c.station_walk_minutes)}′
                        </span>
                      )}
                      {c.access_score !== null && (
                        <span
                          className="flex items-center gap-0.5 font-semibold text-brand-600 dark:text-brand-400"
                          title="Milestone access score"
                        >
                          <Zap size={12} /> {c.access_score}
                        </span>
                      )}
                    </div>

                    {row.description && (
                      <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-stone-500 dark:text-stone-400">
                        {row.description}
                      </p>
                    )}

                    <div className="mt-2.5 flex flex-wrap items-center gap-2">
                      <select
                        value={row.item.status}
                        onChange={(e) => patchItem.mutate({ itemId: row.item.id, status: e.target.value })}
                        className={`rounded-lg border px-2 py-1 text-xs font-medium ${
                          row.item.status
                            ? 'border-brand-300 bg-brand-50 text-brand-700 dark:border-brand-800 dark:bg-brand-950 dark:text-brand-300'
                            : 'border-stone-300 bg-transparent text-stone-500 dark:border-stone-700'
                        }`}
                      >
                        {STATUSES.map((s) => (
                          <option key={s} value={s}>
                            {s || 'no status'}
                          </option>
                        ))}
                      </select>
                      <input
                        className="min-w-40 flex-1 rounded-lg border border-transparent bg-stone-50 px-2 py-1 text-xs text-stone-600 focus:border-stone-300 dark:bg-stone-800 dark:text-stone-300"
                        placeholder="Add a note…"
                        defaultValue={row.item.note}
                        key={`note-${row.item.id}`}
                        onBlur={(e) => {
                          if (e.target.value !== row.item.note)
                            patchItem.mutate({ itemId: row.item.id, note: e.target.value })
                        }}
                      />
                      <span className="text-[11px] text-stone-400">saved {savedAgo(row.item.added_at)}</span>
                      {c.url && (
                        <a
                          href={c.url}
                          target="_blank"
                          rel="noreferrer"
                          className="flex items-center gap-1 text-[11px] font-semibold capitalize text-stone-500 hover:text-brand-600"
                        >
                          {c.portal} <ExternalLink size={11} />
                        </a>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
            {items.data?.length === 0 && (
              <p className="rounded-2xl border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500 dark:border-stone-700">
                Nothing saved here yet. Tap Save on any property.
              </p>
            )}
            {(items.data?.length ?? 0) > 0 && visibleRows.length === 0 && (
              <p className="rounded-2xl border border-dashed border-brand-300 p-8 text-center text-sm text-stone-500 dark:border-brand-800">
                All {items.data!.length} saved {items.data!.length === 1 ? 'item is' : 'items are'} hidden
                by your display filters.{' '}
                <button onClick={clearFilters} className="font-semibold text-brand-600 hover:underline">
                  Clear filters
                </button>
              </p>
            )}
          </div>
          <button
            onClick={() => deleteList.mutate(activeId)}
            className="mt-6 text-xs text-stone-400 hover:text-red-500"
          >
            Delete this list
          </button>
        </>
      )}
    </div>
  )
}
