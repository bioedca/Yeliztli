import { Outlet } from 'react-router-dom'
import TopNav from './TopNav'
import Sidebar from './Sidebar'
import SkipNav from './SkipNav'

export default function AppLayout() {
  return (
    <div className="h-screen flex flex-col">
      <SkipNav />
      <TopNav />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden md:flex-row">
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- main scrollable region must be keyboard-accessible (axe: scrollable-region-focusable) */}
        <main id="main-content" className="order-1 min-w-0 flex-1 overflow-y-auto md:order-2" tabIndex={0}>
          <Outlet />
        </main>
        <Sidebar className="order-2 md:order-1" />
      </div>
    </div>
  )
}
