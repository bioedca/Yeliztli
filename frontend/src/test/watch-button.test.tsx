import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, waitFor } from "./test-utils"
import userEvent from "@testing-library/user-event"
import WatchButton from "@/components/variant-detail/WatchButton"
import VariantDetailSidePanel from "@/components/variant-detail/VariantDetailSidePanel"
import type { VariantDetail } from "@/types/variant-detail"

const mockFetch = vi.fn()

const mockVariantDetail: VariantDetail = {
  rsid: "rs100",
  chrom: "17",
  pos: 43000000,
  ref: "A",
  alt: "G",
  genotype: "AG",
  zygosity: "het",
  gene_symbol: "BRCA1",
  transcript_id: "NM_007294",
  consequence: "missense_variant",
  hgvs_coding: "c.5382insC",
  hgvs_protein: "p.Tyr1853Ter",
  strand: "+",
  exon_number: 11,
  intron_number: null,
  mane_select: true,
  clinvar_significance: "Pathogenic",
  clinvar_review_stars: 3,
  clinvar_accession: "VCV000017694",
  clinvar_conditions: "Hereditary breast and ovarian cancer syndrome",
  gnomad_af_global: 0.0003,
  gnomad_af_afr: 0.0001,
  gnomad_af_amr: 0.0002,
  gnomad_af_eas: null,
  gnomad_af_eur: 0.0004,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  gnomad_homozygous_count: 0,
  rare_flag: true,
  ultra_rare_flag: false,
  cadd_phred: 28.4,
  sift_score: 0.001,
  sift_pred: "D",
  polyphen2_hsvar_score: 0.99,
  polyphen2_hsvar_pred: "probably_damaging",
  revel: 0.85,
  mutpred2: null,
  vest4: null,
  metasvm: null,
  metalr: null,
  gerp_rs: null,
  phylop: null,
  mpc: null,
  primateai: null,
  dbsnp_build: null,
  dbsnp_rsid_current: null,
  dbsnp_validation: null,
  disease_name: "Hereditary breast cancer",
  disease_id: "MONDO:0003582",
  phenotype_source: "mondo_hpo",
  hpo_terms: null,
  inheritance_pattern: "Autosomal dominant",
  deleterious_count: 4,
  deleterious_total_assessed: 4,
  evidence_conflict: true,
  ensemble_pathogenic: true,
  annotation_coverage: 0b111111,
  transcripts: [],
  gene_phenotypes: [],
  evidence_conflict_detail: {
    has_conflict: false,
    clinvar_significance: null,
    clinvar_review_stars: null,
    clinvar_accession: null,
    deleterious_count: null,
    total_tools_assessed: 0,
    deleterious_tools: [],
    cadd_phred: null,
    summary: null,
  },
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch)
  mockFetch.mockReset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("WatchButton (P4-21j)", () => {
  it("renders 'Watch this variant' when not watched", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/watches")) {
        return { ok: true, json: async () => [] }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    render(<WatchButton rsid="rs100" sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /watch this variant/i })).toBeInTheDocument()
    })
    expect(screen.getByText("Watch this variant")).toBeInTheDocument()
  })

  it("renders 'Unwatch' when variant is watched", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/watches")) {
        return {
          ok: true,
          json: async () => [
            {
              rsid: "rs100",
              watched_at: "2026-01-01T00:00:00",
              clinvar_significance_at_watch: "Pathogenic",
              notes: "",
            },
          ],
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    render(<WatchButton rsid="rs100" sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /unwatch/i })).toBeInTheDocument()
    })
    expect(screen.getByText("Unwatch")).toBeInTheDocument()
  })

  it("calls watch API when clicking 'Watch this variant'", async () => {
    mockFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      if (url.includes("/api/watches") && (!opts || opts.method !== "POST")) {
        return { ok: true, json: async () => [] }
      }
      if (url.includes("/api/watches") && opts?.method === "POST") {
        return {
          ok: true,
          json: async () => ({
            rsid: "rs100",
            watched_at: "2026-01-01T00:00:00",
            clinvar_significance_at_watch: "Pathogenic",
            notes: "",
          }),
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    const user = userEvent.setup()
    render(<WatchButton rsid="rs100" sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("Watch this variant")).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: /watch this variant/i }))

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/watches",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ sample_id: 1, rsid: "rs100" }),
        }),
      )
    })
  })

  it("calls unwatch API when clicking 'Unwatch'", async () => {
    mockFetch.mockImplementation(async (url: string, opts?: RequestInit) => {
      if (url.includes("/api/watches/rs100") && opts?.method === "DELETE") {
        return { ok: true }
      }
      if (url.includes("/api/watches")) {
        return {
          ok: true,
          json: async () => [
            {
              rsid: "rs100",
              watched_at: "2026-01-01T00:00:00",
              clinvar_significance_at_watch: "Pathogenic",
              notes: "",
            },
          ],
        }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    const user = userEvent.setup()
    render(<WatchButton rsid="rs100" sampleId={1} />)

    await waitFor(() => {
      expect(screen.getByText("Unwatch")).toBeInTheDocument()
    })

    await user.click(screen.getByRole("button", { name: /unwatch/i }))

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/watches/rs100?sample_id=1",
        expect.objectContaining({ method: "DELETE" }),
      )
    })
  })

  it("renders in compact mode", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/watches")) {
        return { ok: true, json: async () => [] }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    render(<WatchButton rsid="rs100" sampleId={1} compact />)

    await waitFor(() => {
      const btn = screen.getByRole("button", { name: /watch this variant/i })
      expect(btn).toBeInTheDocument()
      expect(btn).toHaveClass("text-xs")
    })
  })
})

describe("Side panel watch button integration (P4-21j)", () => {
  it("shows watch button below 'Open full detail' link", async () => {
    mockFetch.mockImplementation(async (url: string) => {
      if (url.includes("/api/watches")) {
        return { ok: true, json: async () => [] }
      }
      if (url.match(/\/api\/variants\/rs\d+/)) {
        return { ok: true, json: async () => mockVariantDetail }
      }
      return { ok: false, status: 404, text: async () => "Not found" }
    })

    render(
      <VariantDetailSidePanel rsid="rs100" sampleId={1} onClose={() => {}} />,
    )

    await waitFor(() => {
      expect(screen.getByText("rs100")).toBeInTheDocument()
    })

    // Both the link and watch button should be present
    expect(screen.getByRole("link", { name: /open full detail/i })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /watch this variant/i })).toBeInTheDocument()
  })
})
