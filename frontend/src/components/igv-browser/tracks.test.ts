/**
 * Tests for IGV.js track configurations (P2-17).
 *
 * Validates track factory functions produce correct IGV.js configs
 * pointing to the right API endpoints with proper settings.
 */
import { describe, it, expect } from "vitest"
import {
  buildDefaultTracks,
  createClinVarTrack,
  createSampleVariantsTrack,
  createGnomadTrack,
  createEncodeCcresTrack,
} from "./tracks"

describe("createClinVarTrack", () => {
  it("returns variant track with VCF format", () => {
    const track = createClinVarTrack()
    expect(track.type).toBe("variant")
    expect(track.format).toBe("vcf")
    expect(track.sourceType).toBe("service")
  })

  it("points to ClinVar API with URL template variables", () => {
    const track = createClinVarTrack()
    expect(track.url).toContain("/api/igv-tracks/clinvar")
    expect(track.url).toContain("$CHR")
    expect(track.url).toContain("$START")
    expect(track.url).toContain("$END")
  })

  it("includes headerURL for VCF header", () => {
    const track = createClinVarTrack()
    expect(track.headerURL).toBe("/api/igv-tracks/clinvar/header")
  })

  it("has color function for ClinVar significance", () => {
    const track = createClinVarTrack()
    expect(typeof track.color).toBe("function")
    const colorFn = track.color as (v: { info?: Record<string, string> }) => string
    // Pathogenic → red
    expect(colorFn({ info: { CLNSIG: "Pathogenic" } })).toBe("#DC2626")
    // Multi-word backend-emitted values keep severity colors.
    expect(colorFn({ info: { CLNSIG: "Likely pathogenic" } })).toBe("#EF4444")
    expect(colorFn({ info: { CLNSIG: "Uncertain significance" } })).toBe("#F59E0B")
    expect(colorFn({ info: { CLNSIG: "Likely benign" } })).toBe("#22C55E")
    expect(colorFn({ info: { CLNSIG: "Conflicting classifications of pathogenicity" } })).toBe(
      "#F97316",
    )
    // Raw underscore forms are normalized before lookup.
    expect(colorFn({ info: { CLNSIG: "Uncertain_significance" } })).toBe("#F59E0B")
    // Benign → green
    expect(colorFn({ info: { CLNSIG: "Benign" } })).toBe("#16A34A")
    // Unknown → gray
    expect(colorFn({ info: { CLNSIG: "UnknownType" } })).toBe("#6B7280")
    // Missing info
    expect(colorFn({})).toBe("#6B7280")
  })

  it("sets visibility window", () => {
    const track = createClinVarTrack()
    expect(track.visibilityWindow).toBe(1_000_000)
  })
})

describe("createSampleVariantsTrack", () => {
  it("returns variant track for a specific sample", () => {
    const track = createSampleVariantsTrack(42)
    expect(track.type).toBe("variant")
    expect(track.format).toBe("vcf")
    expect(track.sourceType).toBe("service")
    expect(track.name).toBe("Your Variants")
  })

  it("includes sample ID in URLs", () => {
    const track = createSampleVariantsTrack(42)
    expect(track.url).toContain("/api/igv-tracks/sample/42/variants")
    expect(track.headerURL).toContain("/api/igv-tracks/sample/42/header")
  })

  it("uses $CHR/$START/$END template variables", () => {
    const track = createSampleVariantsTrack(1)
    expect(track.url).toContain("$CHR")
    expect(track.url).toContain("$START")
    expect(track.url).toContain("$END")
  })

  it("uses teal color matching project theme", () => {
    const track = createSampleVariantsTrack(1)
    expect(track.color).toBe("#0D9488")
  })
})

describe("createGnomadTrack", () => {
  it("returns annotation track with custom source", () => {
    const track = createGnomadTrack()
    expect(track.type).toBe("annotation")
    expect(track.sourceType).toBe("custom")
    expect(track.name).toBe("gnomAD AF")
  })

  it("has source with URL template and JSON content type", () => {
    const track = createGnomadTrack()
    const source = track.source as { url: string; contentType: string }
    expect(source.url).toContain("/api/igv-tracks/gnomad")
    expect(source.url).toContain("$CHR")
    expect(source.contentType).toBe("application/json")
  })

  it("sets compact display and fixed height", () => {
    const track = createGnomadTrack()
    expect(track.displayMode).toBe("collapsed")
    expect(track.height).toBe(40)
  })
})

describe("createEncodeCcresTrack", () => {
  it("returns annotation track with custom source", () => {
    const track = createEncodeCcresTrack()
    expect(track.type).toBe("annotation")
    expect(track.sourceType).toBe("custom")
    expect(track.name).toBe("ENCODE cCREs")
  })

  it("has source with URL template and JSON content type", () => {
    const track = createEncodeCcresTrack()
    const source = track.source as { url: string; contentType: string }
    expect(source.url).toContain("/api/igv-tracks/encode-ccres")
    expect(source.url).toContain("$CHR")
    expect(source.contentType).toBe("application/json")
  })

  it("uses expanded display mode", () => {
    const track = createEncodeCcresTrack()
    expect(track.displayMode).toBe("expanded")
  })
})

describe("buildDefaultTracks", () => {
  it("returns 3 tracks without sampleId (ClinVar, gnomAD, ENCODE)", () => {
    const tracks = buildDefaultTracks()
    expect(tracks).toHaveLength(3)
    expect(tracks[0].name).toBe("ClinVar Variants")
    expect(tracks[1].name).toBe("gnomAD AF")
    expect(tracks[2].name).toBe("ENCODE cCREs")
  })

  it("returns 4 tracks with sampleId (user VCF first)", () => {
    const tracks = buildDefaultTracks(5)
    expect(tracks).toHaveLength(4)
    expect(tracks[0].name).toBe("Your Variants")
    expect(tracks[0].url).toContain("/sample/5/")
    expect(tracks[1].name).toBe("ClinVar Variants")
    expect(tracks[2].name).toBe("gnomAD AF")
    expect(tracks[3].name).toBe("ENCODE cCREs")
  })

  it("places user variants track first in array", () => {
    const tracks = buildDefaultTracks(1)
    expect(tracks[0].name).toBe("Your Variants")
  })
})
