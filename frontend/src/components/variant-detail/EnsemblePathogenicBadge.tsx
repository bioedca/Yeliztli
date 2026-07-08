import { formatEnsemblePathogenicBadgeLabel } from "@/lib/ensemblePathogenicLabel"
import { cn } from "@/lib/utils"

interface EnsemblePathogenicBadgeProps {
  deleteriousCount: number | null
  deleteriousTotalAssessed: number | null
  className?: string
}

export default function EnsemblePathogenicBadge({
  deleteriousCount,
  deleteriousTotalAssessed,
  className,
}: EnsemblePathogenicBadgeProps) {
  return (
    <div className={cn(
      "rounded font-medium bg-red-50 dark:bg-red-950/20 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800/30",
      className,
    )}>
      {formatEnsemblePathogenicBadgeLabel(deleteriousCount, deleteriousTotalAssessed)}
    </div>
  )
}
