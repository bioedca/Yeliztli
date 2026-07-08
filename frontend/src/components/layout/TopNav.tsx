import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Sun, Moon, Monitor, Search } from 'lucide-react'
import Logo from './Logo'
import IndividualSelector from './IndividualSelector'
import CommandPalette from '@/components/CommandPalette'
import { useThemeContext } from '@/lib/ThemeContext'

export default function TopNav() {
  const { theme, cycleTheme } = useThemeContext()
  const [paletteOpen, setPaletteOpen] = useState(false)

  // Global Cmd+K / Ctrl+K shortcut
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setPaletteOpen((prev) => !prev)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  const openPalette = useCallback(() => setPaletteOpen(true), [])

  const ThemeIcon = theme === 'light' ? Sun : theme === 'dark' ? Moon : Monitor

  return (
    <header className="h-12 border-b border-border bg-background flex items-center px-4 gap-4 shrink-0">
      <Link to="/" className="flex items-center gap-2 font-semibold text-foreground">
        <Logo decorative className="h-5 w-5 text-primary" />
        <span>Yeliztli</span>
      </Link>

      <div className="flex-1" />

      {/* Two-level individual / sample selector (Step 49 / IND-05) */}
      <IndividualSelector />

      {/* Command palette trigger (P2-18) */}
      <button
        type="button"
        onClick={openPalette}
        className="hidden sm:flex items-center gap-2 text-sm text-muted-foreground border border-input rounded-md px-3 py-1.5 hover:bg-accent hover:text-accent-foreground transition-colors"
        aria-label="Open command palette"
        data-testid="command-palette-trigger"
      >
        <Search className="h-3.5 w-3.5" />
        <span>Search...</span>
        <kbd className="ml-2 text-xs bg-muted text-secondary-foreground px-1.5 py-0.5 rounded">{/Mac|iPhone|iPad/.test(navigator.userAgent) ? '⌘' : 'Ctrl+'}K</kbd>
      </button>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />

      {/* Dark mode toggle (P4-26a) */}
      <button
        type="button"
        onClick={cycleTheme}
        className="p-2 rounded-md hover:bg-accent text-muted-foreground hover:text-accent-foreground transition-colors"
        aria-label={`Theme: ${theme}`}
        title={`Theme: ${theme}`}
        data-testid="theme-toggle"
      >
        <ThemeIcon className="h-4 w-4" />
      </button>
    </header>
  )
}
