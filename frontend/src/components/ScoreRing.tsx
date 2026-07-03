export default function ScoreRing({ score, size = 44 }: { score: number | null; size?: number }) {
  if (score === null || score === undefined) return null
  const r = (size - 6) / 2
  const c = 2 * Math.PI * r
  const pct = Math.max(0, Math.min(100, score))
  const hue = 8 + (pct / 100) * 130 // red → green
  const color = `hsl(${hue} 70% 42%)`
  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={4}
          className="stroke-stone-200 dark:stroke-stone-700"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={4}
          strokeLinecap="round"
          strokeDasharray={`${(pct / 100) * c} ${c}`}
        />
      </svg>
      <span
        className="absolute inset-0 flex items-center justify-center text-xs font-bold"
        style={{ color }}
      >
        {Math.round(pct)}
      </span>
    </div>
  )
}
