import { useMutation, useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import type { ScrapeRunInfo } from '../lib/types'

export default function SystemPage() {
  const runs = useQuery({
    queryKey: ['scrape-runs'],
    queryFn: () => api.get<ScrapeRunInfo[]>('/api/system/scrape-runs'),
    refetchInterval: 15_000,
  })

  const poll = useMutation({
    mutationFn: () => api.post('/api/system/poll-now'),
  })

  return (
    <div className="mx-auto max-w-4xl p-4 md:p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">System status</h1>
        <button
          onClick={() => poll.mutate()}
          disabled={poll.isPending}
          className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3.5 py-2 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-50"
        >
          <RefreshCw size={15} className={poll.isPending ? 'animate-spin' : ''} />
          Scan now
        </button>
      </div>
      {poll.isSuccess && (
        <p className="mb-4 rounded-lg bg-brand-50 px-3 py-2 text-sm text-brand-700 dark:bg-brand-950 dark:text-brand-300">
          Scan started — results appear below as portals finish.
        </p>
      )}

      <div className="overflow-x-auto rounded-2xl border border-stone-200 bg-white dark:border-stone-800 dark:bg-stone-900">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-stone-200 text-left text-xs uppercase tracking-wide text-stone-400 dark:border-stone-800">
              <th className="px-4 py-2.5">Portal</th>
              <th className="px-4 py-2.5">Started</th>
              <th className="px-4 py-2.5">Found</th>
              <th className="px-4 py-2.5">New</th>
              <th className="px-4 py-2.5">Updated</th>
              <th className="px-4 py-2.5">Status</th>
            </tr>
          </thead>
          <tbody>
            {runs.data?.map((run) => (
              <tr key={run.id} className="border-b border-stone-100 last:border-0 dark:border-stone-800/50">
                <td className="px-4 py-2 font-medium capitalize">{run.portal}</td>
                <td className="px-4 py-2 text-stone-500">
                  {new Date(run.started_at).toLocaleString('en-GB')}
                </td>
                <td className="px-4 py-2">{run.found}</td>
                <td className="px-4 py-2">{run.new}</td>
                <td className="px-4 py-2">{run.updated}</td>
                <td className="px-4 py-2">
                  {run.blocked ? (
                    <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700 dark:bg-red-950 dark:text-red-300">
                      Blocked
                    </span>
                  ) : run.error ? (
                    <span title={run.error} className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300">
                      Error
                    </span>
                  ) : run.finished_at ? (
                    <span className="rounded-full bg-brand-100 px-2 py-0.5 text-xs font-medium text-brand-700 dark:bg-brand-950 dark:text-brand-300">
                      OK
                    </span>
                  ) : (
                    <span className="text-xs text-stone-400">Running…</span>
                  )}
                </td>
              </tr>
            ))}
            {runs.data?.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-stone-400">
                  No scans yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
