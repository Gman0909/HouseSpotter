import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MapPin, Plus, Trash2, Zap } from 'lucide-react'
import { api, ApiError } from '../lib/api'
import type { MilestoneInfo } from '../lib/types'

export default function MilestonesCard() {
  const qc = useQueryClient()
  const [label, setLabel] = useState('')
  const [place, setPlace] = useState('')
  const [weight, setWeight] = useState(2)
  const [error, setError] = useState('')

  const milestones = useQuery({
    queryKey: ['milestones'],
    queryFn: () => api.get<MilestoneInfo[]>('/api/milestones'),
  })

  function refresh() {
    qc.invalidateQueries({ queryKey: ['milestones'] })
    qc.invalidateQueries({ queryKey: ['feed'] })
    qc.invalidateQueries({ queryKey: ['travel'] })
  }

  const add = useMutation({
    mutationFn: () => api.post('/api/milestones', { label: label.trim(), place: place.trim(), weight }),
    onMutate: () => setError(''),
    onSuccess: () => {
      setLabel('')
      setPlace('')
      setWeight(2)
      refresh()
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : 'Failed to add'),
  })
  const setW = useMutation({
    mutationFn: ({ id, w }: { id: number; w: number }) => api.patch(`/api/milestones/${id}`, { weight: w }),
    onSuccess: refresh,
  })
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/api/milestones/${id}`),
    onSuccess: refresh,
  })

  return (
    <div className="rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
      <h2 className="mb-1 flex items-center gap-2 font-semibold">
        <Zap size={16} /> Milestones
      </h2>
      <p className="mb-3 text-xs text-stone-400">
        Your favourite places — universal, shared by every search profile. Every property gets
        travel times to these and a Milestone Access Score; weight ×3 places count most.
      </p>

      <div className="space-y-1.5">
        {milestones.data?.map((m) => (
          <div
            key={m.id}
            className="flex items-center justify-between gap-3 rounded-lg px-2 py-1.5 text-sm hover:bg-stone-50 dark:hover:bg-stone-800/50"
          >
            <div className="min-w-0">
              <span className="font-medium">{m.label}</span>
              <a
                href={`https://www.google.com/maps/search/?api=1&query=${m.lat},${m.lng}`}
                target="_blank"
                rel="noreferrer"
                className="ml-2 inline-flex items-center gap-0.5 text-xs text-stone-400 hover:text-brand-600"
                title="Check the pin in Google Maps"
              >
                <MapPin size={11} />
                {m.place}
              </a>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <select
                value={m.weight}
                onChange={(e) => setW.mutate({ id: m.id, w: Number(e.target.value) })}
                className="rounded-lg border border-stone-300 bg-transparent px-1.5 py-1 text-xs dark:border-stone-700"
                title="Importance"
              >
                <option value={1}>×1</option>
                <option value={2}>×2</option>
                <option value={3}>×3</option>
              </select>
              <button onClick={() => remove.mutate(m.id)} className="text-stone-400 hover:text-red-500">
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
        {milestones.data?.length === 0 && (
          <p className="px-2 py-1 text-sm text-stone-400">No milestones yet — add your first below.</p>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-stone-200 pt-3 dark:border-stone-700">
        <input
          className="input w-36"
          placeholder="Name, e.g. Office"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <input
          className="input min-w-40 flex-1"
          placeholder="Place — address, town or postcode"
          value={place}
          onChange={(e) => setPlace(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && label.trim() && place.trim() && add.mutate()}
        />
        <select value={weight} onChange={(e) => setWeight(Number(e.target.value))} className="input">
          <option value={1}>×1</option>
          <option value={2}>×2</option>
          <option value={3}>×3</option>
        </select>
        <button
          onClick={() => add.mutate()}
          disabled={!label.trim() || !place.trim() || add.isPending}
          className="flex items-center gap-1 rounded-lg bg-brand-600 px-3 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
        >
          <Plus size={14} /> Add
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-500">{error}</p>}
    </div>
  )
}
