/**
 * Test-only utilities for IgvBrowser.
 *
 * Separated from IgvBrowser.tsx to satisfy react-refresh/only-export-components
 * (files with components must only export components).
 */
import type { IgvBrowserInstance, IgvReference } from "./IgvBrowser"

interface IgvBrowserOptions {
  genome?: string
  reference?: IgvReference
  locus: string
  showNavigation: boolean
  showRuler: boolean
  showCenterGuide: boolean
  showCursorTrackingGuide: boolean
  tracks: unknown[]
}

export type IgvModule = {
  createBrowser: (div: HTMLElement, options: IgvBrowserOptions) => Promise<IgvBrowserInstance>
  removeBrowser: (browser: IgvBrowserInstance) => void
}

// Allow tests to inject a mock without loading the real 3MB module
let igvOverride: IgvModule | null = null

/** @internal Test-only: inject a mock igv module */
export function __setIgvForTesting(mock: IgvModule | null): void {
  igvOverride = mock
}

/** @internal Get the current igv override (used by IgvBrowser component) */
export function __getIgvOverride(): IgvModule | null {
  return igvOverride
}
