import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Heart, Plus } from 'lucide-react'
import { api } from '../lib/api'
import type { SavedListInfo } from '../lib/types'

interface Membership {
  list_id: number
  item_id: number
  name: string
}

export default function AddToListButton({ propertyId }: { propertyId: number }) {
  const [open, setOpen] = useState(false)
  const [newName, setNewName] = useState('')
  const qc = useQueryClient()
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  const memberships = useQuery({
    queryKey: ['saved', propertyId],
    queryFn: () => api.get<Membership[]>(`/api/lists/membership?property_id=${propertyId}`),
  })
  const saved = (memberships.data?.length ?? 0) > 0
  const inList = (listId: number) => memberships.data?.find((m) => m.list_id === listId)

  const lists = useQuery({
    queryKey: ['lists'],
    queryFn: () => api.get<SavedListInfo[]>('/api/lists'),
    enabled: open,
  })

  function refresh() {
    qc.invalidateQueries({ queryKey: ['lists'] })
    qc.invalidateQueries({ queryKey: ['saved', propertyId] })
    qc.invalidateQueries({ queryKey: ['saved-ids'] })
  }

  const toggle = useMutation({
    mutationFn: async (listId: number) => {
      const member = inList(listId)
      if (member) {
        await api.delete(`/api/lists/${listId}/items/${member.item_id}`)
      } else {
        await api.post(`/api/lists/${listId}/items`, { property_id: propertyId })
      }
    },
    onSuccess: refresh,
  })

  const createAndAdd = useMutation({
    mutationFn: async (name: string) => {
      const list = await api.post<{ id: number }>('/api/lists', { name })
      return api.post(`/api/lists/${list.id}/items`, { property_id: propertyId })
    },
    onSuccess: () => {
      setNewName('')
      refresh()
    },
  })

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 rounded-lg border px-3.5 py-2 text-sm font-semibold transition ${
          saved
            ? 'border-brand-600 bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-300'
            : 'border-stone-300 hover:bg-stone-100 dark:border-stone-700 dark:hover:bg-stone-800'
        }`}
      >
        <Heart size={15} fill={saved ? 'currentColor' : 'none'} />
        {saved ? 'Saved' : 'Save'}
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-2 w-60 rounded-xl border border-stone-200 bg-white p-2 shadow-xl dark:border-stone-700 dark:bg-stone-900">
          {lists.data?.map((list) => {
            const member = !!inList(list.id)
            return (
              <button
                key={list.id}
                onClick={() => toggle.mutate(list.id)}
                className="flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm hover:bg-stone-100 dark:hover:bg-stone-800"
              >
                <span className={member ? 'font-semibold text-brand-700 dark:text-brand-300' : ''}>
                  {list.name}
                </span>
                {member ? (
                  <Check size={15} className="text-brand-600" />
                ) : (
                  <span className="text-xs text-stone-400">{list.count}</span>
                )}
              </button>
            )
          })}
          {lists.data?.length === 0 && (
            <p className="px-3 py-1.5 text-xs text-stone-400">No lists yet — create one:</p>
          )}
          <div className="mt-1 flex gap-1 border-t border-stone-200 pt-2 dark:border-stone-700">
            <input
              className="min-w-0 flex-1 rounded-lg border border-stone-300 bg-transparent px-2 py-1.5 text-sm dark:border-stone-700"
              placeholder="New list…"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && newName.trim() && createAndAdd.mutate(newName.trim())}
            />
            <button
              onClick={() => newName.trim() && createAndAdd.mutate(newName.trim())}
              className="rounded-lg bg-brand-600 px-2 text-white hover:bg-brand-700"
            >
              <Plus size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
