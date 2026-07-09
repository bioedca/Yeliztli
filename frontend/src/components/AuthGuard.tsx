/**
 * AuthGuard — redirects unauthenticated users to /login (P4-21a)
 * and fresh installs to /setup.
 *
 * Wraps protected routes. On fresh install (needs_setup), redirects
 * to the setup wizard. When auth is enabled and the user has no
 * valid session, redirects to the login page. Otherwise renders
 * children immediately.
 */
import { Navigate, Outlet, useLocation } from "react-router-dom"
import { useAuthStatus } from "@/api/auth"
import { useSetupStatus } from "@/api/setup"
import PageError from "@/components/ui/PageError"

export default function AuthGuard() {
  const {
    data: authStatus,
    isLoading: authLoading,
    isError: authError,
    refetch: refetchAuthStatus,
  } = useAuthStatus()
  const {
    data: setupStatus,
    isLoading: setupLoading,
    isError: setupError,
    refetch: refetchSetupStatus,
  } = useSetupStatus()
  const location = useLocation()

  function retryFailedStatusChecks() {
    if (authError) void refetchAuthStatus()
    if (setupError) void refetchSetupStatus()
  }

  // While loading, show nothing (avoids flash)
  if (authLoading || setupLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      </div>
    )
  }

  if (authError || setupError) {
    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <div className="w-full max-w-lg">
          <PageError
            message="Can't reach the Yeliztli backend. Check that the server is running, then retry."
            onRetry={retryFailedStatusChecks}
          />
        </div>
      </div>
    )
  }

  // Fresh install → redirect to setup wizard (unless already there)
  if (setupStatus?.needs_setup && !location.pathname.startsWith("/setup")) {
    return <Navigate to="/setup" replace />
  }

  // Auth is enabled but user is not authenticated — redirect to login
  if (authStatus?.auth_enabled && !authStatus.authenticated) {
    return <Navigate to="/login" replace />
  }

  return <Outlet />
}
