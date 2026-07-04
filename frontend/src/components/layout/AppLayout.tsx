import { Outlet } from 'react-router-dom'
import TopNav from './TopNav'
import Sidebar from './Sidebar'
import SkipNav from './SkipNav'

export default function AppLayout() {
  return (
    <div className="h-screen flex flex-col">
      <SkipNav />
      <TopNav />
      <div className="flex min-h-0 flex-1 flex-col-reverse overflow-hidden md:flex-row">
        <Sidebar />
        {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- main scrollable region must be keyboard-accessible (axe: scrollable-region-focusable) */}
        <main id="main-content" className="min-w-0 flex-1 overflow-y-auto" tabIndex={0}>
          <Outlet />
        </main>
      </div>
    </div>
  )
}
