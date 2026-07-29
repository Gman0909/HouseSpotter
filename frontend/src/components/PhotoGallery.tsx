import { useCallback, useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, Images, LandPlot, X } from 'lucide-react'

/** Photo grid + full-screen lightbox. Floorplans join the same lightbox after the
 * photos so everything is browsable in one pass. */
export default function PhotoGallery({
  images,
  floorplans,
  pending,
}: {
  images: string[]
  floorplans: string[]
  pending?: boolean
}) {
  const items = [...images, ...floorplans]
  const [open, setOpen] = useState<number | null>(null)

  if (items.length === 0) return null
  const tiles = images.slice(0, 6)
  const hidden = images.length - tiles.length

  return (
    <div className="mb-5">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {tiles.map((url, i) => (
          <button
            key={url}
            type="button"
            onClick={() => setOpen(i)}
            className={`group relative overflow-hidden rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
              i === 0 ? 'col-span-2 row-span-2 aspect-[4/3] md:col-span-2' : 'aspect-[4/3]'
            }`}
          >
            <img
              src={url}
              alt=""
              loading={i === 0 ? 'eager' : 'lazy'}
              className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
            />
            {i === tiles.length - 1 && hidden > 0 && (
              <span className="absolute inset-0 flex items-center justify-center bg-black/50 text-lg font-semibold text-white">
                +{hidden} photos
              </span>
            )}
          </button>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen(0)}
          className="flex items-center gap-1.5 rounded-lg border border-stone-300 px-3 py-1.5 text-sm font-semibold hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
        >
          <Images size={15} /> All {images.length} photos
        </button>
        {floorplans.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen(images.length)}
            className="flex items-center gap-1.5 rounded-lg border border-stone-300 px-3 py-1.5 text-sm font-semibold hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800"
          >
            <LandPlot size={15} /> Floorplan{floorplans.length > 1 ? 's' : ''}
          </button>
        )}
        {pending && (
          <span className="text-xs text-stone-400">Fetching the full gallery…</span>
        )}
      </div>
      {open !== null && (
        <Lightbox
          items={items}
          floorplanFrom={images.length}
          index={open}
          setIndex={setOpen}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  )
}

function Lightbox({
  items,
  floorplanFrom,
  index,
  setIndex,
  onClose,
}: {
  items: string[]
  floorplanFrom: number
  index: number
  setIndex: (i: number) => void
  onClose: () => void
}) {
  const count = items.length
  const step = useCallback(
    (delta: number) => setIndex((index + delta + count) % count),
    [index, count, setIndex],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') step(-1)
      else if (e.key === 'ArrowRight') step(1)
    }
    window.addEventListener('keydown', onKey)
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = ''
    }
  }, [onClose, step])

  // keep neighbours warm so arrowing feels instant
  useEffect(() => {
    for (const i of [index - 1, index + 1]) {
      const url = items[(i + count) % count]
      if (url) new Image().src = url
    }
  }, [index, items, count])

  const isFloorplan = index >= floorplanFrom
  return (
    <div
      className="fixed inset-0 z-[1200] flex flex-col bg-black/95"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div className="flex items-center justify-between p-3 text-sm text-white/80">
        <span>
          {isFloorplan
            ? `Floorplan ${index - floorplanFrom + 1} of ${count - floorplanFrom}`
            : `${index + 1} of ${floorplanFrom}`}
        </span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="rounded-full p-2 hover:bg-white/10"
        >
          <X size={20} />
        </button>
      </div>
      <div className="relative flex min-h-0 flex-1 items-center justify-center px-12">
        <img
          key={items[index]}
          src={items[index]}
          alt=""
          onClick={(e) => e.stopPropagation()}
          className={`max-h-full max-w-full rounded-lg object-contain ${isFloorplan ? 'bg-white' : ''}`}
        />
        {count > 1 && (
          <>
            <button
              type="button"
              aria-label="Previous photo"
              onClick={(e) => {
                e.stopPropagation()
                step(-1)
              }}
              className="absolute left-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2.5 text-white hover:bg-white/25"
            >
              <ChevronLeft size={24} />
            </button>
            <button
              type="button"
              aria-label="Next photo"
              onClick={(e) => {
                e.stopPropagation()
                step(1)
              }}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-full bg-white/10 p-2.5 text-white hover:bg-white/25"
            >
              <ChevronRight size={24} />
            </button>
          </>
        )}
      </div>
      <div
        className="flex gap-1.5 overflow-x-auto p-3"
        onClick={(e) => e.stopPropagation()}
      >
        {items.map((url, i) => (
          <button
            key={url}
            type="button"
            onClick={() => setIndex(i)}
            className={`h-14 w-20 shrink-0 overflow-hidden rounded-md ring-2 ${
              i === index ? 'ring-brand-500' : 'ring-transparent opacity-60 hover:opacity-100'
            }`}
          >
            <img src={url} alt="" loading="lazy" className="h-full w-full object-cover" />
          </button>
        ))}
      </div>
    </div>
  )
}
