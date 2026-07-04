import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { PanelLeftClose, PanelLeft } from 'lucide-react'
import { cn } from '@/lib/utils'
import { navRoutes } from '@/lib/nav-routes'

type SidebarProps = {
  className?: string
}

export default function Sidebar({ className }: SidebarProps) {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside
      className={cn(
        'flex h-16 w-full max-w-full shrink-0 flex-col overflow-x-auto overflow-y-hidden border-t border-sidebar-border bg-sidebar-background transition-all duration-200 md:h-auto md:overflow-x-visible md:overflow-y-visible md:border-t-0 md:border-r',
        collapsed ? 'md:w-12' : 'md:w-56',
        className
      )}
    >
      <div className="flex-1 py-1 md:py-2 md:overflow-y-auto">
        <nav
          aria-label="Main navigation"
          className="flex h-full flex-row items-stretch gap-1 px-2 md:h-auto md:flex-col md:gap-0.5"
        >
          {navRoutes.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex min-w-0 shrink-0 flex-col items-center justify-center gap-1 rounded-md px-3 py-2 text-sm font-medium transition-colors md:flex-row md:justify-start md:gap-3 md:px-2',
                  isActive
                    ? 'bg-sidebar-accent text-sidebar-accent-foreground'
                    : 'text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground',
                  collapsed && 'md:justify-center md:px-0'
                )
              }
              title={label}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className={cn('max-w-20 truncate text-xs md:max-w-full md:min-w-0 md:text-sm', collapsed && 'md:sr-only')}>
                {label}
              </span>
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="hidden border-t border-sidebar-border p-2 md:block">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center justify-center w-full rounded-md p-2 text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground transition-colors"
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? (
            <PanelLeft className="h-4 w-4" />
          ) : (
            <PanelLeftClose className="h-4 w-4" />
          )}
        </button>
      </div>
    </aside>
  )
}
