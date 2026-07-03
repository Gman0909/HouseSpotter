import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Bike, Car, Footprints, RefreshCw, TrainFront, Zap } from 'lucide-react'
import { api } from '../lib/api'
import type { TravelInfo, TravelMode } from '../lib/types'

const MODE_META = [
  { key: 'car' as const, icon: Car, gmode: 'driving' },
  { key: 'cycle' as const, icon: Bike, gmode: 'bicycling' },
  { key: 'walk' as const, icon: Footprints, gmode: 'walking' },
]

function fmtMinutes(mins: number | null): string {
  if (mins === null) return '—'
  if (mins < 60) return `${Math.round(mins)} min`
  const h = Math.floor(mins / 60)
  const m = Math.round(mins % 60)
  return `${h}h ${String(m).padStart(2, '0')}`
}

function dirUrl(from: { lat: number; lng: number }, to: { lat: number; lng: number }, gmode: string) {
  return `https://www.google.com/maps/dir/?api=1&origin=${from.lat},${from.lng}&destination=${to.lat},${to.lng}&travelmode=${gmode}`
}

export default function TravelPanel({
  propertyId,
  propertyLat,
  propertyLng,
}: {
  propertyId: number
  propertyLat: number | null
  propertyLng: number | null
}) {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)

  const travel = useQuery({
    queryKey: ['travel', propertyId],
    queryFn: () => api.get<TravelInfo>(`/api/properties/${propertyId}/travel`),
    staleTime: Infinity,
  })

  if (travel.isLoading) {
    return (
      <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
        <h2 className="mb-2 flex items-center gap-2 font-semibold">
          <Zap size={16} /> Getting to your places
        </h2>
        <p className="text-sm text-stone-400">Working out travel times…</p>
      </section>
    )
  }
  if (!travel.data || (travel.data.milestones.length === 0 && !travel.data.station)) return null

  const { milestones, station, access_score, access_peak, access_offpeak } = travel.data
  const anyEstimate = milestones.some((m) =>
    Object.values(m.modes).some((mode: TravelMode) => mode.provider === 'estimate'),
  )
  const origin = propertyLat !== null && propertyLng !== null ? { lat: propertyLat, lng: propertyLng } : null

  async function recompute() {
    setRefreshing(true)
    try {
      await api.get(`/api/properties/${propertyId}/travel?force=true`)
      qc.invalidateQueries({ queryKey: ['travel', propertyId] })
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-semibold">
          <Zap size={16} /> Getting to your places
        </h2>
        <div className="flex items-center gap-2.5">
          {access_score !== null && (
            <span
              className="rounded-full bg-brand-100 px-2.5 py-1 text-xs font-bold text-brand-700 dark:bg-brand-900 dark:text-brand-300"
              title="Typical score, with modelled rush-hour (peak) and quiet-road (off-peak) variants"
            >
              Access score {access_score}
              {access_peak !== null && access_offpeak !== null && (
                <span className="ml-1.5 font-medium opacity-75">
                  · peak {access_peak} / off-peak {access_offpeak}
                </span>
              )}
            </span>
          )}
          <button
            onClick={recompute}
            disabled={refreshing}
            title="Recompute travel times"
            className="text-stone-400 hover:text-stone-600 disabled:opacity-50"
          >
            <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {station && (
        <p className="mb-2.5 flex flex-wrap items-center gap-1.5 text-sm">
          <TrainFront size={15} className="shrink-0 text-brand-600" />
          <span className="font-medium">{station.name}</span>
          <span className="text-stone-500">
            {station.walk_minutes !== null
              ? `— ${Math.round(station.walk_minutes)} min walk${station.provider === 'estimate' ? '*' : ''}${
                  station.km != null ? ` · ${station.km.toFixed(1)} km` : ''
                }`
              : `— nearest station, ${station.km} km away`}
          </span>
        </p>
      )}

      {milestones.length > 0 && (
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-stone-400">
              <th className="py-1.5 pr-3">Place</th>
              {MODE_META.map(({ key, icon: Icon }) => (
                <th key={key} className="px-3 py-1.5">
                  <Icon size={15} />
                </th>
              ))}
              <th className="px-3 py-1.5">
                <TrainFront size={15} />
              </th>
            </tr>
          </thead>
          <tbody>
            {milestones.map((m) => (
              <tr key={m.id} className="border-t border-stone-100 dark:border-stone-800/60">
                <td className="py-2 pr-3 font-medium">
                  {m.label}
                  {m.weight !== 2 && (
                    <span className="ml-1.5 text-[10px] text-stone-400">×{m.weight}</span>
                  )}
                </td>
                {MODE_META.map(({ key, gmode }) => {
                  const mode = m.modes[key]
                  const cell = (
                    <span className={mode.provider === 'estimate' ? 'text-stone-400' : ''}>
                      {fmtMinutes(mode.minutes)}
                      {mode.provider === 'estimate' && mode.minutes !== null && '*'}
                    </span>
                  )
                  return (
                    <td key={key} className="px-3 py-2">
                      {origin && mode.minutes !== null ? (
                        <a
                          href={dirUrl(origin, m, gmode)}
                          target="_blank"
                          rel="noreferrer"
                          className="hover:underline"
                          title="Open directions in Google Maps"
                        >
                          {cell}
                        </a>
                      ) : (
                        cell
                      )}
                    </td>
                  )
                })}
                <td className="px-3 py-2">
                  {origin ? (
                    <a
                      href={dirUrl(origin, m, 'transit')}
                      target="_blank"
                      rel="noreferrer"
                      className="text-brand-600 hover:underline"
                      title="Live public transport directions in Google Maps"
                    >
                      view
                    </a>
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
      {(anyEstimate || station?.provider === 'estimate') && (
        <p className="mt-2 text-[11px] text-stone-400">
          * distance-based estimate — real routed times appear once computed (nightly, or hit ↻).
        </p>
      )}
      <p className="mt-1 text-[11px] text-stone-400">
        Peak/off-peak are modelled from typical UK congestion (routing data carries no live
        traffic): peak ≈ drive × 1.35 + 2 min, off-peak ≈ drive × 0.85.
      </p>
    </section>
  )
}
