import { cn } from '@/lib/utils'
import {
  EMBLEM_PATH,
  EMBLEM_VIEWBOX,
  LOCKUP_PATH,
  LOCKUP_VIEWBOX,
} from './logo-paths'

type LogoProps = {
  /** `emblem` = the chakana + DNA mark; `lockup` = mark over the YELIZTLI wordmark. */
  variant?: 'emblem' | 'lockup'
  className?: string
  /**
   * Hide from the accessibility tree. Use when an adjacent text node (a heading
   * or label) already names the app, so screen readers don't announce it twice.
   */
  decorative?: boolean
  title?: string
}

/**
 * The Yeliztli logo, inlined as an SVG so it inherits colour via `currentColor`
 * (`fill-current`) — set `text-primary` (teal), `text-primary-foreground`
 * (white on teal), etc. Paths are generated from brand/logo-source.svg; see
 * ./logo-paths.ts.
 */
export default function Logo({
  variant = 'emblem',
  className,
  decorative = false,
  title = 'Yeliztli',
}: LogoProps) {
  const lockup = variant === 'lockup'
  return (
    <svg
      viewBox={lockup ? LOCKUP_VIEWBOX : EMBLEM_VIEWBOX}
      className={cn('fill-current', lockup ? 'h-auto w-40' : 'h-6 w-6', className)}
      role={decorative ? undefined : 'img'}
      aria-label={decorative ? undefined : title}
      aria-hidden={decorative || undefined}
      focusable="false"
    >
      {!decorative && <title>{title}</title>}
      <path fillRule="evenodd" d={lockup ? LOCKUP_PATH : EMBLEM_PATH} />
    </svg>
  )
}
