/**
 * Login page — PIN/password authentication (P4-21a).
 *
 * Full-screen page shown when auth is enabled and the user
 * does not have a valid session. Redirects to "/" on success.
 */
import { useState, useRef, useEffect, type FormEvent } from "react"
import { Navigate, useNavigate } from "react-router-dom"
import { useLogin, useAuthStatus } from "@/api/auth"
import { Shield, AlertCircle } from "lucide-react"
import Logo from "@/components/layout/Logo"

export default function Login() {
  const navigate = useNavigate()
  const { data: authStatus } = useAuthStatus()
  const loginMutation = useLogin()
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const passwordRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    passwordRef.current?.focus()
  }, [])

  // If auth is not enabled, redirect to dashboard
  if (authStatus && !authStatus.auth_enabled) {
    return <Navigate to="/" replace />
  }

  // If already authenticated, redirect to dashboard
  if (authStatus?.authenticated) {
    return <Navigate to="/" replace />
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError("")

    if (!password.trim()) {
      setError("Please enter your password")
      return
    }

    try {
      await loginMutation.mutateAsync(password)
      navigate("/", { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed")
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        {/* Logo / branding */}
        <div className="mb-8 text-center">
          <Logo
            variant="lockup"
            decorative
            className="mx-auto mb-4 text-primary"
          />
          <h1 className="sr-only">Yeliztli</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Enter your password to continue
          </p>
        </div>

        {/* Login form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="rounded-lg border border-border bg-card p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-2 text-sm font-medium">
              <Shield className="h-4 w-4 text-muted-foreground" />
              Authentication Required
            </div>

            <div className="space-y-3">
              <div>
                <label
                  htmlFor="password"
                  className="mb-1.5 block text-sm font-medium"
                >
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  placeholder="Enter your password"
                  ref={passwordRef}
                  autoComplete="current-password"
                />
              </div>

              {error && (
                <div className="flex items-center gap-2 text-sm text-destructive" role="alert">
                  <AlertCircle className="h-4 w-4 shrink-0" />
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={loginMutation.isPending}
                className="w-full rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-50"
              >
                {loginMutation.isPending ? "Signing in..." : "Sign in"}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
