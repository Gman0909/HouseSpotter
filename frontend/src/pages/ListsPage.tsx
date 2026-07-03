import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Trash2, Plus } from 'lucide-react'
import { api } from '../lib/api'
import type { SavedListInfo } from '../lib/types'

interface ListItemRow {
  item: { id: number; note: string; status: string }
  address: string
  image: string | null
  price: number | null
  beds: number | null
  property_id: number
}

export default function ListsPage() {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<number | null>(null)
  const [newName, setNewName] = useState('')

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
  const saveNote = useMutation({
    mutationFn: ({ itemId, note }: { itemId: number; note: string }) =>
      api.patch(`/api/lists/${activeId}/items/${itemId}`, { note }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['list-items', activeId] }),
  })

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
          <div className="space-y-3">
            {items.data?.map((row) => (
              <div
                key={row.item.id}
                className="flex gap-3 rounded-2xl border border-stone-200 bg-white p-3 dark:border-stone-800 dark:bg-stone-900"
              >
                <Link to={`/property/${row.property_id}`} className="shrink-0">
                  {row.image ? (
                    <img src={row.image} alt="" className="h-20 w-28 rounded-xl object-cover" />
                  ) : (
                    <div className="flex h-20 w-28 items-center justify-center rounded-xl bg-stone-200 text-xs text-stone-400 dark:bg-stone-800">
                      No photo
                    </div>
                  )}
                </Link>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <Link to={`/property/${row.property_id}`} className="truncate text-sm font-semibold hover:underline">
                      {row.address}
                    </Link>
                    <button
                      onClick={() => removeItem.mutate(row.item.id)}
                      className="text-stone-400 hover:text-red-500"
                    >
                      <Trash2 size={15} />
                    </button>
                  </div>
                  <p className="text-sm text-stone-500">
                    {row.price != null && `£${row.price.toLocaleString('en-GB')}`}
                    {row.beds != null && ` · ${row.beds} bed`}
                  </p>
                  <input
                    className="mt-1.5 w-full rounded-lg border border-transparent bg-stone-50 px-2 py-1 text-xs text-stone-600 focus:border-stone-300 dark:bg-stone-800 dark:text-stone-300"
                    placeholder="Add a note…"
                    defaultValue={row.item.note}
                    onBlur={(e) => {
                      if (e.target.value !== row.item.note)
                        saveNote.mutate({ itemId: row.item.id, note: e.target.value })
                    }}
                  />
                </div>
              </div>
            ))}
            {items.data?.length === 0 && (
              <p className="rounded-2xl border border-dashed border-stone-300 p-8 text-center text-sm text-stone-500 dark:border-stone-700">
                Nothing saved here yet. Tap Save on any property.
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
