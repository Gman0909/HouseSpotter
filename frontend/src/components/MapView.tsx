import { MapContainer, TileLayer, CircleMarker, Popup, Tooltip } from 'react-leaflet'
import { Link } from 'react-router-dom'
import { BedDouble, Bath } from 'lucide-react'
import type { PropertyCard } from '../lib/types'
import { formatPrice } from './PropertyCardView'

function scoreColor(score: number | null): string {
  if (score === null) return '#78716c'
  const hue = 8 + (Math.max(0, Math.min(100, score)) / 100) * 130
  return `hsl(${hue} 70% 42%)`
}

function HoverCard({ card }: { card: PropertyCard }) {
  return (
    <div className="w-56 overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-stone-200 dark:bg-stone-900 dark:ring-stone-700">
      {card.image && (
        <div className="relative h-28 w-full">
          <img src={card.image} alt="" className="h-full w-full object-cover" />
          {card.score !== null && (
            <span
              className="absolute right-1.5 top-1.5 rounded-full px-2 py-0.5 text-xs font-bold text-white shadow"
              style={{ backgroundColor: scoreColor(card.score) }}
            >
              {Math.round(card.score)}
            </span>
          )}
        </div>
      )}
      <div className="p-2.5">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-sm font-bold text-stone-900 dark:text-stone-100">
            {formatPrice(card)}
          </span>
          {!card.image && card.score !== null && (
            <span
              className="rounded-full px-1.5 text-[11px] font-bold text-white"
              style={{ backgroundColor: scoreColor(card.score) }}
            >
              {Math.round(card.score)}
            </span>
          )}
        </div>
        <p className="mt-0.5 truncate text-xs text-stone-500">{card.address}</p>
        <div className="mt-1 flex items-center gap-2.5 text-[11px] text-stone-500">
          {card.beds != null && (
            <span className="flex items-center gap-0.5">
              <BedDouble size={12} /> {card.beds}
            </span>
          )}
          {card.baths != null && (
            <span className="flex items-center gap-0.5">
              <Bath size={12} /> {card.baths}
            </span>
          )}
          {card.property_type && <span className="capitalize">{card.property_type}</span>}
        </div>
        {card.rationale && (
          <p className="mt-1.5 line-clamp-2 text-[11px] leading-snug text-stone-600 dark:text-stone-400">
            {card.rationale}
          </p>
        )}
      </div>
    </div>
  )
}

export default function MapView({ cards, profileId }: { cards: PropertyCard[]; profileId?: number }) {
  const located = cards.filter((c) => c.lat !== null && c.lng !== null)
  if (located.length === 0) {
    return (
      <p className="rounded-2xl border border-dashed border-stone-300 p-10 text-center text-sm text-stone-500 dark:border-stone-700">
        No mappable properties.
      </p>
    )
  }
  const centerLat = located.reduce((s, c) => s + c.lat!, 0) / located.length
  const centerLng = located.reduce((s, c) => s + c.lng!, 0) / located.length

  return (
    <div className="h-[70vh] overflow-hidden rounded-2xl border border-stone-200 dark:border-stone-800">
      <MapContainer center={[centerLat, centerLng]} zoom={12} className="h-full w-full">
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {located.map((card) => (
          <CircleMarker
            key={card.id}
            center={[card.lat!, card.lng!]}
            radius={9}
            pathOptions={{
              color: '#fff',
              weight: 1.5,
              fillColor: scoreColor(card.score),
              fillOpacity: 0.9,
            }}
          >
            {/* Hover card (desktop); click still opens the popup with a details link */}
            <Tooltip direction="top" offset={[0, -10]} opacity={1} className="hs-hovercard">
              <HoverCard card={card} />
            </Tooltip>
            <Popup>
              <div className="min-w-40">
                {card.image && (
                  <img src={card.image} alt="" className="mb-1.5 h-20 w-full rounded object-cover" />
                )}
                <strong>{formatPrice(card)}</strong>
                {card.score !== null && <span> · score {Math.round(card.score)}</span>}
                <br />
                <span className="text-xs">{card.address}</span>
                <br />
                <Link to={`/property/${card.id}${profileId ? `?profile=${profileId}` : ''}`}>
                  Details →
                </Link>
                {' · '}
                <a
                  href={`https://www.google.com/maps/search/?api=1&query=${card.lat},${card.lng}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  Google Maps
                </a>
              </div>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}
