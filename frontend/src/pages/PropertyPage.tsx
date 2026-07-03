import { useEffect } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MapContainer, TileLayer, CircleMarker } from 'react-leaflet'
import { ArrowLeft, Check, ExternalLink, MapPin } from 'lucide-react'
import { api } from '../lib/api'
import type { PropertyDetail } from '../lib/types'
import ScoreRing from '../components/ScoreRing'
import { formatPrice } from '../components/PropertyCardView'
import AddToListButton from '../components/AddToListButton'
import TravelPanel from '../components/TravelPanel'

export default function PropertyPage() {
  const { id } = useParams()
  const [params] = useSearchParams()
  const profileId = params.get('profile')

  const qc = useQueryClient()
  const detail = useQuery({
    queryKey: ['property', id, profileId],
    queryFn: () =>
      api.get<PropertyDetail>(
        `/api/properties/${id}${profileId ? `?profile_id=${profileId}` : ''}`,
      ),
  })

  // Viewing marks the property as seen server-side — refresh the cached feed so
  // the "New" badge is gone when the user navigates back
  useEffect(() => {
    if (detail.data) qc.invalidateQueries({ queryKey: ['feed'] })
  }, [detail.data, qc])

  if (detail.isLoading) return <div className="p-6 text-sm text-stone-500">Loading…</div>
  if (!detail.data) return <div className="p-6 text-sm text-stone-500">Not found.</div>

  const { property, listings, match } = detail.data
  const live = listings.find((l) => l.status !== 'removed') ?? listings[0]

  // Precise pin when we have coordinates; otherwise Google's best guess at the address
  const gmapsUrl =
    property.lat != null && property.lng != null
      ? `https://www.google.com/maps/search/?api=1&query=${property.lat},${property.lng}`
      : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(
          [property.address, property.postcode, 'UK'].filter(Boolean).join(', '),
        )}`

  return (
    <div className="mx-auto max-w-5xl p-4 md:p-6">
      <Link to="/" className="mb-4 inline-flex items-center gap-1 text-sm text-stone-500 hover:text-stone-800 dark:hover:text-stone-200">
        <ArrowLeft size={16} /> Back to homes
      </Link>

      {(property.image_urls ?? []).length > 0 && (
        <div className="mb-5 grid grid-cols-2 gap-2 md:grid-cols-3">
          {property.image_urls.slice(0, 6).map((url, i) => (
            <img
              key={url}
              src={url}
              alt=""
              className={`w-full rounded-xl object-cover ${i === 0 ? 'col-span-2 row-span-2 aspect-[4/3] md:col-span-2' : 'aspect-[4/3]'}`}
            />
          ))}
        </div>
      )}

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {live ? formatPrice({ price: live.price, mode: live.mode }) : 'POA'}
          </h1>
          <p className="mt-1 text-stone-600 dark:text-stone-400">{property.address}</p>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            {property.beds != null && <Badge>{property.beds} bed</Badge>}
            {property.baths != null && <Badge>{property.baths} bath</Badge>}
            {property.property_type && <Badge>{property.property_type}</Badge>}
            {property.tenure && <Badge>{property.tenure}</Badge>}
            {property.epc && <Badge>EPC {property.epc}</Badge>}
            {property.floor_area_sqm && <Badge>{Math.round(property.floor_area_sqm)} m²</Badge>}
          </div>
        </div>
        <div className="flex items-center gap-3">
          {match && <ScoreRing score={match.score} size={64} />}
          <AddToListButton propertyId={property.id} />
          {live && (
            <a
              href={live.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-semibold text-white hover:bg-brand-700"
            >
              {live.portal} <ExternalLink size={14} />
            </a>
          )}
        </div>
      </div>

      <TravelPanel propertyId={property.id} propertyLat={property.lat} propertyLng={property.lng} />

      {match && (match.breakdown ?? []).length > 0 && (
        <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
          <h2 className="mb-1 font-semibold">Why this scores {Math.round(match.score)}</h2>
          {match.rationale && <p className="mb-3 text-sm text-stone-600 dark:text-stone-400">{match.rationale}</p>}

          {match.breakdown.some((b) => b.kind === 'must') && (
            <div
              className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-xl bg-brand-50 px-3.5 py-2.5 dark:bg-brand-950"
              title="Hard requirements — properties without these never appear, so they gate rather than weight the score"
            >
              <span className="text-xs font-semibold uppercase tracking-wide text-brand-700 dark:text-brand-300">
                Requirements met
              </span>
              {match.breakdown
                .filter((b) => b.kind === 'must')
                .map((b) => (
                  <span key={b.label} className="flex items-center gap-1 text-xs text-brand-800 dark:text-brand-200">
                    <Check size={13} className="shrink-0" />
                    <b>{b.label}</b>
                    {b.reason && <span className="text-brand-600/80 dark:text-brand-300/80">— {b.reason}</span>}
                  </span>
                ))}
            </div>
          )}

          <div className="space-y-2">
            {match.breakdown
              .filter((b) => b.kind !== 'must')
              .map((entry) => (
                <div key={entry.label} className="flex items-center gap-3 text-sm">
                  <div className="w-40 shrink-0 truncate font-medium">{entry.label}</div>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-stone-200 dark:bg-stone-700">
                    <div
                      className="h-full rounded-full bg-brand-500"
                      style={{ width: `${entry.satisfaction * 100}%` }}
                    />
                  </div>
                  <div className="w-24 shrink-0 text-right text-xs text-stone-500">
                    {entry.reason ?? `${Math.round(entry.satisfaction * 100)}%`}
                  </div>
                </div>
              ))}
          </div>
        </section>
      )}

      {property.description && (
        <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
          <h2 className="mb-2 font-semibold">Description</h2>
          <p className="whitespace-pre-line text-sm leading-relaxed text-stone-700 dark:text-stone-300">
            {property.description}
          </p>
        </section>
      )}

      <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="font-semibold">Map</h2>
          <a
            href={gmapsUrl}
            target="_blank"
            rel="noreferrer"
            title={property.lat != null ? 'Open this location in Google Maps' : 'Approximate location (address lookup) in Google Maps'}
            className="flex items-center gap-1.5 rounded-lg border border-stone-300 px-3.5 py-1.5 text-sm font-semibold hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
          >
            <MapPin size={14} /> Open in Maps
          </a>
        </div>
        {property.lat != null && property.lng != null ? (
          <div className="h-72 overflow-hidden rounded-xl">
            <MapContainer
              center={[property.lat, property.lng]}
              zoom={15}
              className="h-full w-full"
              scrollWheelZoom={false}
            >
              <TileLayer
                attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
                url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
              />
              <CircleMarker
                center={[property.lat, property.lng]}
                radius={10}
                pathOptions={{ color: '#fff', weight: 2, fillColor: '#2b6e5a', fillOpacity: 0.95 }}
              />
            </MapContainer>
          </div>
        ) : (
          <p className="text-sm text-stone-400">
            No precise coordinates for this listing — "Open in Maps" searches the address instead.
          </p>
        )}
      </section>

      {(property.features ?? []).length > 0 && (
        <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
          <h2 className="mb-2 font-semibold">Features</h2>
          <ul className="grid grid-cols-1 gap-1.5 text-sm text-stone-700 sm:grid-cols-2 dark:text-stone-300">
            {property.features.map((f) => (
              <li key={f} className="flex items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
                {f}
              </li>
            ))}
          </ul>
        </section>
      )}

      {live && (live.price_history ?? []).length > 1 && (
        <section className="mt-6 rounded-2xl border border-stone-200 bg-white p-5 dark:border-stone-800 dark:bg-stone-900">
          <h2 className="mb-2 font-semibold">Price history</h2>
          <ul className="space-y-1 text-sm">
            {live.price_history.map((h) => (
              <li key={h.date} className="flex justify-between">
                <span className="text-stone-500">{h.date}</span>
                <span className="font-medium">£{h.price.toLocaleString('en-GB')}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full bg-stone-100 px-2.5 py-1 font-medium capitalize text-stone-700 dark:bg-stone-800 dark:text-stone-300">
      {children}
    </span>
  )
}
