import { ArrowLeft, Home, SearchX } from 'lucide-react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

export default function NotFound() {
  const location = useLocation()
  const navigate = useNavigate()
  const requestedPath = `${location.pathname}${location.search}${location.hash}`

  const handleBack = () => {
    const historyState = window.history.state as { idx?: unknown } | null
    if (typeof historyState?.idx === 'number' && historyState.idx > 0) {
      navigate(-1)
      return
    }
    navigate('/', { replace: true })
  }

  return (
    <div className="min-h-full px-6 py-8">
      <div className="max-w-3xl">
        <div className="flex items-center gap-3 text-sm font-medium text-muted-foreground">
          <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <SearchX className="h-5 w-5" aria-hidden="true" />
          </span>
          <span>404</span>
        </div>

        <h1 className="mt-4 text-2xl font-bold text-foreground">
          Page not found
        </h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
          No route matches{' '}
          <code className="break-all rounded bg-muted px-1.5 py-0.5 text-foreground">
            {requestedPath}
          </code>
          .
        </p>

        <div className="mt-6 flex flex-wrap gap-3">
          <Link
            to="/"
            className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
          >
            <Home className="h-4 w-4" aria-hidden="true" />
            Dashboard
          </Link>
          <button
            type="button"
            onClick={handleBack}
            className="inline-flex h-9 items-center gap-2 rounded-md border border-input bg-background px-3 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            <ArrowLeft className="h-4 w-4" aria-hidden="true" />
            Back
          </button>
        </div>
      </div>
    </div>
  )
}
