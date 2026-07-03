import { Link } from 'react-router-dom'
import { BedDouble, Bath, Heart, MapPin, Sparkles, TrendingDown, Zap } from 'lucide-react'
import type { PropertyCard } from '../lib/types'
import ScoreRing from './ScoreRing'

export function formatPrice(card: Pick<PropertyCard, 'price' | 'mode'>): string {
  if (card.price == null) return 'POA'
  const gbp = card.price.toLocaleString('en-GB')
  return card.mode === 'rent' ? `£${gbp} pcm` : `£${gbp}`
}

function isNew(firstSeen: string | null): boolean {
  if (!firstSeen) return false
  return Date.now() - new Date(firstSeen).getTime() < 48 * 3600 * 1000
}

function hasPriceDrop(history: { price: number }[]): boolean {
  if (history.length < 2) return false
  return history[history.length - 1].price < history[history.length - 2].price
}

export default function PropertyCardView({
  card,
  profileId,
  saved = false,
}: {
  card: PropertyCard
  profileId?: number
  saved?: boolean
}) {
  return (
    <Link
      to={`/property/${card.id}${profileId ? `?profile=${profileId}` : ''}`}
      className="group overflow-hidden rounded-2xl border border-stone-200 bg-white shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg dark:border-stone-800 dark:bg-stone-900"
    >
      <div className="relative aspect-[4/3] overflow-hidden bg-stone-200 dark:bg-stone-800">
        {card.image ? (
          <img
            src={card.image}
            alt={card.address}
            loading="lazy"
            className="h-full w-full object-cover transition duration-300 group-hover:scale-105"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-stone-400">No photo</div>
        )}
        <div className="absolute left-2 top-2 flex gap-1.5">
          {isNew(card.first_seen) && (
            <span className="flex items-center gap-1 rounded-full bg-brand-600 px-2 py-0.5 text-[11px] font-semibold text-white shadow">
              <Sparkles size={11} /> New
            </span>
          )}
          {hasPriceDrop(card.price_history) && (
            <span className="flex items-center gap-1 rounded-full bg-amber-500 px-2 py-0.5 text-[11px] font-semibold text-white shadow">
              <TrendingDown size={11} /> Price drop
            </span>
          )}
        </div>
        {card.score !== null && (
          <div className="absolute right-2 top-2 rounded-full bg-white/95 p-0.5 shadow dark:bg-stone-900/95">
            <ScoreRing score={card.score} />
          </div>
        )}
        {saved && (
          <span className="absolute bottom-2 right-2 rounded-full bg-white/95 p-1.5 text-brand-600 shadow dark:bg-stone-900/95">
            <Heart size={14} fill="currentColor" />
          </span>
        )}
      </div>
      <div className="p-3.5">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-lg font-bold tracking-tight">{formatPrice(card)}</span>
          {card.price_qualifier && card.price_qualifier !== 'pcm' && (
            <span className="text-[11px] text-stone-500">{card.price_qualifier}</span>
          )}
        </div>
        <p className="mt-0.5 line-clamp-1 flex items-center gap-1 text-sm text-stone-600 dark:text-stone-400">
          <MapPin size={13} className="shrink-0" />
          {card.address}
        </p>
        <div className="mt-2 flex items-center gap-3 text-xs text-stone-500">
          {card.beds != null && (
            <span className="flex items-center gap-1">
              <BedDouble size={14} /> {card.beds}
            </span>
          )}
          {card.baths != null && (
            <span className="flex items-center gap-1">
              <Bath size={14} /> {card.baths}
            </span>
          )}
          {card.property_type && <span className="capitalize">{card.property_type}</span>}
          {card.epc && <span>EPC {card.epc}</span>}
          {card.access_score !== null && (
            <span
              className="ml-auto flex items-center gap-0.5 font-semibold text-brand-600 dark:text-brand-400"
              title={
                card.access_peak !== null && card.access_offpeak !== null
                  ? `Milestone access score ${card.access_score} (peak traffic ${card.access_peak} – off-peak ${card.access_offpeak})`
                  : 'Milestone access score'
              }
            >
              <Zap size={12} />
              {card.access_peak !== null && card.access_offpeak !== null && card.access_peak !== card.access_offpeak
                ? `${card.access_peak}–${card.access_offpeak}`
                : card.access_score}
            </span>
          )}
        </div>
      </div>
    </Link>
  )
}
