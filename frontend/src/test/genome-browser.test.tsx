/**
 * #621: GenomeBrowser must read the selected sample from ?sample_id (snake_case)
 * — the param every page writes via IndividualSelector — not ?sampleId
 * (camelCase). When it read camelCase, picking a sample and then opening the
 * Genome Browser never loaded the user's "Your Variants" track (only the
 * variant-detail link happened to emit camelCase). These assert the snake param
 * loads the user track and the legacy camelCase param does not.
 */

import { forwardRef } from "react"

import { render } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it, vi } from "vitest"

import type { IgvTrack } from "@/components/igv-browser"

// Capture the tracks GenomeBrowser feeds to IGV without booting IGV.js. The page
// imports buildDefaultTracks from the real "@/components/igv-browser/tracks"
// subpath, so the actual track-building logic runs; only the heavy IGV component
// (imported from the package index) is stubbed.
const captured = vi.hoisted(() => ({ tracks: [] as IgvTrack[] }))

vi.mock("@/components/igv-browser", () => ({
  IgvBrowser: forwardRef(function MockIgvBrowser(props: { tracks?: IgvTrack[] }) {
    captured.tracks = props.tracks ?? []
    return null
  }),
}))

// Imported after the mock so GenomeBrowser picks up the stubbed IgvBrowser.
import GenomeBrowser from "@/pages/GenomeBrowser"

function renderAt(url: string): string[] {
  captured.tracks = []
  render(
    <MemoryRouter initialEntries={[url]}>
      <GenomeBrowser />
    </MemoryRouter>,
  )
  return captured.tracks.map((t) => t.name ?? "")
}

describe("GenomeBrowser sample_id param (#621)", () => {
  it("loads the user's variant track from ?sample_id (snake_case)", () => {
    const names = renderAt("/genome-browser?sample_id=1")
    expect(names).toContain("Your Variants")
    expect(names).not.toContain("ENCODE cCREs")
    // user track + the two GRCh37-compatible reference tracks (ClinVar / gnomAD)
    expect(captured.tracks).toHaveLength(3)
  })

  it("shows reference tracks only when no sample is selected", () => {
    const names = renderAt("/genome-browser")
    expect(names).not.toContain("Your Variants")
    expect(names).not.toContain("ENCODE cCREs")
    expect(captured.tracks).toHaveLength(2)
  })

  it("ignores the legacy ?sampleId (camelCase) param — the #621 regression", () => {
    const names = renderAt("/genome-browser?sampleId=1")
    expect(names).not.toContain("Your Variants")
  })

  it("ignores a non-numeric / invalid sample_id", () => {
    expect(renderAt("/genome-browser?sample_id=abc")).not.toContain("Your Variants")
    expect(renderAt("/genome-browser?sample_id=0")).not.toContain("Your Variants")
  })
})
