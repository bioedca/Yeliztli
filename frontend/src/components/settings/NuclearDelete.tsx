/** Nuclear Delete confirmation UI (P4-21).
 *
 * Two-step confirmation: user must type "DELETE ALL DATA" to enable the
 * delete button. On success, redirects to the setup wizard.
 */

import { useState } from "react"
import { AlertTriangle } from "lucide-react"
import { useNuclearDelete } from "@/api/nuclear"

const CONFIRMATION_PHRASE = "DELETE ALL DATA"

export default function NuclearDelete() {
  const [confirmText, setConfirmText] = useState("")
  const [showConfirm, setShowConfirm] = useState(false)
  const nuclearDelete = useNuclearDelete()

  const isConfirmed = confirmText === CONFIRMATION_PHRASE

  function handleDelete() {
    if (!isConfirmed) return
    nuclearDelete.mutate()
  }

  function handleCancel() {
    setShowConfirm(false)
    setConfirmText("")
  }

  return (
    <div className="space-y-4" data-testid="nuclear-delete">
      <div>
        <h2 className="text-lg font-semibold text-foreground">Delete All Data</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Permanently remove all samples, reference databases, cached downloads,
          logs, and configuration. The application will reset to its initial setup
          wizard state.
        </p>
      </div>

      {!showConfirm ? (
        <button
          type="button"
          data-testid="nuclear-delete-trigger"
          onClick={() => setShowConfirm(true)}
          className="inline-flex items-center gap-2 rounded-md border border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/40 px-4 py-2 text-sm font-medium text-red-700 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-950/60 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-500"
        >
          <AlertTriangle className="h-4 w-4" />
          Delete All Data
        </button>
      ) : (
        <div
          role="alertdialog"
          aria-labelledby="nuclear-delete-title"
          aria-describedby="nuclear-delete-desc"
          className="rounded-lg border-2 border-red-300 dark:border-red-800 bg-red-50 dark:bg-red-950/30 p-5 space-y-4"
        >
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-red-100 dark:bg-red-900/50 text-red-600 dark:text-red-400 shrink-0">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <div>
              <h3
                id="nuclear-delete-title"
                className="text-base font-semibold text-red-800 dark:text-red-300"
              >
                This action is irreversible
              </h3>
              <p
                id="nuclear-delete-desc"
                className="text-sm text-red-700 dark:text-red-400 mt-1"
              >
                All sample databases, reference databases (ClinVar, gnomAD, dbNSFP,
                VEP bundle, ENCODE), cached downloads, logs, and your configuration
                will be permanently deleted. You will need to re-run the setup wizard
                to use Yeliztli again.
              </p>
            </div>
          </div>

          <div>
            <label
              htmlFor="nuclear-confirm-input"
              className="block text-sm font-medium text-red-800 dark:text-red-300 mb-1.5"
            >
              Type <span className="font-mono font-bold">{CONFIRMATION_PHRASE}</span>{" "}
              to confirm:
            </label>
            <input
              id="nuclear-confirm-input"
              data-testid="nuclear-confirm-input"
              type="text"
              value={confirmText}
              onChange={(e) => setConfirmText(e.target.value)}
              className="w-full max-w-sm rounded-md border border-red-300 dark:border-red-700 bg-white dark:bg-red-950/20 px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-red-500"
              placeholder={CONFIRMATION_PHRASE}
              autoComplete="off"
              spellCheck={false}
            />
          </div>

          <div className="flex gap-3">
            <button
              type="button"
              data-testid="nuclear-delete-confirm"
              disabled={!isConfirmed || nuclearDelete.isPending}
              onClick={handleDelete}
              className="inline-flex items-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-500"
            >
              {nuclearDelete.isPending ? "Deleting..." : "Permanently Delete Everything"}
            </button>
            <button
              type="button"
              data-testid="nuclear-delete-cancel"
              onClick={handleCancel}
              disabled={nuclearDelete.isPending}
              className="inline-flex items-center rounded-md border border-border bg-background px-4 py-2 text-sm font-medium text-foreground hover:bg-muted transition-colors disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              Cancel
            </button>
          </div>

          {nuclearDelete.isError && (
            <p
              role="alert"
              className="text-sm text-red-700 dark:text-red-400"
              data-testid="nuclear-delete-error"
            >
              Delete failed: {nuclearDelete.error.message}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
