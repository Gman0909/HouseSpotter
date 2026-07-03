import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { Home, Heart, Map, MessageCircle, SlidersHorizontal, Activity, Settings } from 'lucide-react'
import clsx from 'clsx'

const NAV = [
  { to: '/', label: 'Homes', short: 'Homes', icon: Home },
  { to: '/lists', label: 'Lists', short: 'Lists', icon: Heart },
  { to: '/areas', label: 'Areas', short: 'Areas', icon: Map },
  { to: '/chat', label: 'Agent', short: 'Agent', icon: MessageCircle },
  { to: '/settings', label: 'Search Profiles', short: 'Profiles', icon: SlidersHorizontal },
  { to: '/system', label: 'Status', short: 'Status', icon: Activity },
  { to: '/config', label: 'Settings', short: 'Settings', icon: Settings },
]

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Sidebar (desktop) */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-stone-200 bg-white p-4 md:flex dark:border-stone-800 dark:bg-stone-900">
        <div className="mb-8 flex items-center gap-2 px-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-600 text-lg font-bold text-white">
            H
          </div>
          <span className="text-lg font-semibold tracking-tight">HouseSpotter</span>
        </div>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                clsx(
                  'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                  isActive
                    ? 'bg-brand-50 text-brand-700 dark:bg-brand-950 dark:text-brand-300'
                    : 'text-stone-600 hover:bg-stone-100 dark:text-stone-400 dark:hover:bg-stone-800',
                )
              }
            >
              <Icon size={18} />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main */}
      <main id="hs-main" className="min-h-0 flex-1 overflow-y-auto pb-20 md:pb-0">{children}</main>

      {/* Bottom nav (mobile) */}
      <nav className="fixed inset-x-0 bottom-0 z-40 flex justify-around border-t border-stone-200 bg-white/95 py-1.5 backdrop-blur md:hidden dark:border-stone-800 dark:bg-stone-900/95">
        {NAV.map(({ to, short, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              clsx(
                'flex flex-col items-center gap-0.5 rounded-md px-3 py-1 text-[11px] font-medium',
                isActive ? 'text-brand-600 dark:text-brand-300' : 'text-stone-500',
              )
            }
          >
            <Icon size={20} />
            {short}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
