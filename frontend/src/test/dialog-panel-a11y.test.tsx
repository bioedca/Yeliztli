/** A11y coverage for the slide-in detail panels (#703/#846):
 *  - the shared `useDialogFocus` hook (focus-in / Tab-trap / focus-restore);
 *  - modal hardening (background inert + scroll lock);
 *  - the four previously-bare panels now expose role="dialog" + aria-modal and
 *    move focus into themselves on open.
 */

import { readdirSync, readFileSync } from "node:fs"
import { join, relative, sep } from "node:path"
import { useRef, type ComponentType } from "react"
import { describe, expect, it, vi } from "vitest"
import { render, screen, fireEvent } from "./test-utils"
import { useDialogFocus } from "@/hooks/useDialogFocus"

import CarrierPanel from "@/components/carrier/VariantDetailPanel"
import CancerPanel from "@/components/cancer/VariantDetailPanel"
import CardiovascularPanel from "@/components/cardiovascular/VariantDetailPanel"
import RareVariantPanel from "@/components/rare-variants/VariantDetailPanel"
import NutrigenomicsPathwayPanel from "@/components/nutrigenomics/PathwayDetailPanel"
import TraitsPathwayPanel from "@/components/traits/PathwayDetailPanel"
import MethylationPathwayPanel from "@/components/methylation/PathwayDetailPanel"
import SkinPathwayPanel from "@/components/skin/PathwayDetailPanel"
import AllergyPathwayPanel from "@/components/allergy/PathwayDetailPanel"
import SleepPathwayPanel from "@/components/sleep/PathwayDetailPanel"
import FitnessPathwayPanel from "@/components/fitness/PathwayDetailPanel"
import type { CarrierVariant } from "@/types/carrier"
import type { CancerVariant } from "@/types/cancer"
import type { CardiovascularVariant } from "@/types/cardiovascular"
import type { RareVariant } from "@/types/rare-variants"

// ── Self-discovering production dialog guard (#1251) ────────────────────────

const SOURCE_ROOT = join(process.cwd(), "src")
const ROLE_DIALOG_RE = /\brole\s*=\s*["']dialog["']/
const ARIA_MODAL_TRUE_RE = /\baria-modal\s*=\s*(?:"true"|'true'|\{\s*true\s*\})/
const DIALOG_OPENING_TAG_RE =
  /<[A-Za-z][\w:.]*(?=[^>]*\brole\s*=\s*["']dialog["'])(?=[^>]*\baria-modal\s*=\s*(?:"true"|'true'|\{\s*true\s*\}))[^>]*>/gs
const USE_DIALOG_FOCUS_IMPORT_RE =
  /import\s*\{[^}]*\buseDialogFocus\b[^}]*\}\s*from\s*["']@\/hooks\/useDialogFocus["']/
const TAB_INDEX_MINUS_ONE_RE = /\btabIndex\s*=\s*\{\s*-1\s*\}/
const REF_ATTRIBUTE_RE = /\bref\s*=\s*\{\s*([A-Za-z_$][\w$]*)\s*\}/

const KNOWN_MODAL_DIALOG_SOURCES = [
  "components/allergy/PathwayDetailPanel.tsx",
  "components/cancer/VariantDetailPanel.tsx",
  "components/cardiovascular/VariantDetailPanel.tsx",
  "components/carrier/VariantDetailPanel.tsx",
  "components/fitness/PathwayDetailPanel.tsx",
  "components/gene-health/PathwayDetailPanel.tsx",
  "components/individuals/MergeWizard.tsx",
  "components/individuals/PostMergeRewatchModal.tsx",
  "components/methylation/PathwayDetailPanel.tsx",
  "components/nutrigenomics/PathwayDetailPanel.tsx",
  "components/pharmacogenomics/DrugDetailPanel.tsx",
  "components/rare-variants/VariantDetailPanel.tsx",
  "components/sleep/PathwayDetailPanel.tsx",
  "components/skin/PathwayDetailPanel.tsx",
  "components/traits/PathwayDetailPanel.tsx",
  "components/variant-detail/VariantDetailSidePanel.tsx",
  "pages/ReportBuilder.tsx",
] as const

type DialogSource = {
  path: string
  source: string
  dialogOpeningTags: string[]
}

function collectProductionTsxFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const absolutePath = join(dir, entry.name)
    if (entry.isDirectory()) {
      return entry.name === "test" ? [] : collectProductionTsxFiles(absolutePath)
    }
    return entry.isFile() && entry.name.endsWith(".tsx") ? [absolutePath] : []
  })
}

function toSourceRelativePath(filePath: string): string {
  return relative(SOURCE_ROOT, filePath).split(sep).join("/")
}

function stripTsComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1")
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function useDialogFocusCallForRef(refName: string): RegExp {
  return new RegExp(
    `\\buseDialogFocus\\s*\\(\\s*${escapeRegExp(refName)}(?:\\s*[,\\)])`,
  )
}

function discoverModalDialogSources(): DialogSource[] {
  return collectProductionTsxFiles(SOURCE_ROOT)
    .map((filePath) => ({
      path: toSourceRelativePath(filePath),
      source: stripTsComments(readFileSync(filePath, "utf8")),
    }))
    .filter(
      ({ source }) => ROLE_DIALOG_RE.test(source) && ARIA_MODAL_TRUE_RE.test(source),
    )
    .map(({ path, source }) => ({
      path,
      source,
      dialogOpeningTags: source.match(DIALOG_OPENING_TAG_RE) ?? [],
    }))
    .sort((a, b) => a.path.localeCompare(b.path))
}

describe("production modal dialogs wire focus management (#1251)", () => {
  it("discovers every modal dialog source and requires useDialogFocus", () => {
    const dialogSources = discoverModalDialogSources()
    const discoveredPaths = dialogSources.map(({ path }) => path)
    const missingExpectedSources = KNOWN_MODAL_DIALOG_SOURCES.filter(
      (path) => !discoveredPaths.includes(path),
    )
    const violations: string[] = []

    for (const { path, source, dialogOpeningTags } of dialogSources) {
      if (!USE_DIALOG_FOCUS_IMPORT_RE.test(source)) {
        violations.push(`${path}: import useDialogFocus from @/hooks/useDialogFocus`)
      }
      if (dialogOpeningTags.length === 0) {
        violations.push(`${path}: expose role="dialog" and aria-modal="true" on a JSX tag`)
      }
      for (const tag of dialogOpeningTags) {
        const refName = tag.match(REF_ATTRIBUTE_RE)?.[1]
        if (!refName) {
          violations.push(`${path}: add a ref={...} to ${tag}`)
        } else if (!useDialogFocusCallForRef(refName).test(source)) {
          violations.push(`${path}: call useDialogFocus(${refName}, ...) for ${tag}`)
        }
        if (!TAB_INDEX_MINUS_ONE_RE.test(tag)) {
          violations.push(`${path}: add tabIndex={-1} to ${tag}`)
        }
      }
    }

    expect(discoveredPaths.length).toBeGreaterThan(0)
    expect(missingExpectedSources).toEqual([])
    expect(violations).toEqual([])
  })
})

// ── useDialogFocus ──────────────────────────────────────────────────────────

function FocusHarness() {
  const ref = useRef<HTMLDivElement>(null)
  useDialogFocus(ref)
  return (
    <div ref={ref} role="dialog" aria-modal="true" aria-label="harness" tabIndex={-1}>
      <button>first</button>
      <button>second</button>
      <button>last</button>
    </div>
  )
}

/** A trigger button + a conditionally-mounted dialog (the pattern-A panels). */
function ControlledDialog({ open }: { open: boolean }) {
  return (
    <>
      <button data-testid="trigger">open</button>
      {open && <FocusHarness />}
    </>
  )
}

function ScrollContainerDialog({ open }: { open: boolean }) {
  return (
    <div id="main-content" data-testid="scroll-container" style={{ overflow: "auto" }}>
      <button data-testid="background-action">background action</button>
      {open && <FocusHarness />}
    </div>
  )
}

describe("useDialogFocus", () => {
  it("moves focus into the dialog on open (first focusable)", () => {
    render(<FocusHarness />)
    expect(document.activeElement).toBe(screen.getByText("first"))
  })

  it("traps Tab from the last element back to the first", () => {
    render(<FocusHarness />)
    const last = screen.getByText("last")
    last.focus()
    fireEvent.keyDown(last, { key: "Tab" })
    expect(document.activeElement).toBe(screen.getByText("first"))
  })

  it("traps Shift+Tab from the first element back to the last", () => {
    render(<FocusHarness />)
    const first = screen.getByText("first")
    first.focus()
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(screen.getByText("last"))
  })

  it("keeps Tab trapped when the container itself holds focus", () => {
    render(<FocusHarness />)
    const dialog = screen.getByRole("dialog")
    dialog.focus()
    expect(document.activeElement).toBe(dialog)
    fireEvent.keyDown(dialog, { key: "Tab" })
    expect(document.activeElement).toBe(screen.getByText("first"))
  })

  it("restores focus to the trigger when the dialog closes", () => {
    const { rerender } = render(<ControlledDialog open={false} />)
    const trigger = screen.getByTestId("trigger")
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    rerender(<ControlledDialog open={true} />) // open → focus moves inside
    expect(document.activeElement).toBe(screen.getByText("first"))

    rerender(<ControlledDialog open={false} />) // close → focus restored
    expect(document.activeElement).toBe(trigger)
  })

  it("marks background siblings inert while the dialog is open", () => {
    const { rerender } = render(<ControlledDialog open={false} />)
    const trigger = screen.getByTestId("trigger")

    rerender(<ControlledDialog open={true} />)
    const dialog = screen.getByRole("dialog")

    expect(trigger).toHaveAttribute("inert")
    expect(dialog).not.toHaveAttribute("inert")

    rerender(<ControlledDialog open={false} />)
    expect(trigger).not.toHaveAttribute("inert")
  })

  it("locks and restores body plus app scroll-container overflow", () => {
    document.body.style.overflow = "auto"
    let closeDialog: (() => void) | undefined

    try {
      const { rerender } = render(<ScrollContainerDialog open={false} />)
      closeDialog = () => rerender(<ScrollContainerDialog open={false} />)
      const scrollContainer = screen.getByTestId("scroll-container")

      rerender(<ScrollContainerDialog open={true} />)

      expect(document.body.style.overflow).toBe("hidden")
      expect(scrollContainer).toHaveStyle({ overflow: "hidden" })

      rerender(<ScrollContainerDialog open={false} />)

      expect(document.body.style.overflow).toBe("auto")
      expect(scrollContainer).toHaveStyle({ overflow: "auto" })
    } finally {
      closeDialog?.()
      document.body.style.overflow = ""
    }
  })
})

// ── Panels expose dialog semantics + focus-in ───────────────────────────────

const CARRIER_VARIANT: CarrierVariant = {
  rsid: "rs113993960",
  gene_symbol: "CFTR",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000007105",
  clinvar_review_stars: 3,
  clinvar_conditions: "Cystic fibrosis",
  conditions: ["Cystic Fibrosis"],
  inheritance: "AR",
  evidence_level: 4,
  cross_links: [],
  pmids: ["20301428"],
  notes: "Carrier-panel example.",
}

const CANCER_VARIANT: CancerVariant = {
  rsid: "rs80357906",
  gene_symbol: "BRCA1",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000017661",
  clinvar_review_stars: 3,
  clinvar_conditions: "Hereditary breast and ovarian cancer syndrome",
  syndromes: ["Hereditary Breast and Ovarian Cancer"],
  cancer_types: ["Breast", "Ovarian"],
  inheritance: "AD",
  evidence_level: 4,
  cross_links: [],
  pmids: ["20301425"],
}

const CARDIO_VARIANT: CardiovascularVariant = {
  rsid: "rs28942082",
  gene_symbol: "LDLR",
  genotype: "C/T",
  zygosity: "het",
  clinvar_significance: "Pathogenic",
  clinvar_accession: "VCV000003657",
  clinvar_review_stars: 3,
  clinvar_conditions: "Familial hypercholesterolemia",
  conditions: ["Familial hypercholesterolemia"],
  cardiovascular_category: "familial_hypercholesterolemia",
  inheritance: "AD",
  evidence_level: 4,
  cross_links: [],
  pmids: ["19657116"],
}

const RARE_VARIANT: RareVariant = {
  rsid: "rs12345",
  chrom: "17",
  pos: 43071077,
  ref: "A",
  alt: "G",
  genotype: "AG",
  zygosity: "het",
  gene_symbol: "BRCA1",
  consequence: "missense_variant",
  hgvs_coding: "c.1234A>G",
  hgvs_protein: "p.Asp412Gly",
  gnomad_af_global: 0.00023,
  gnomad_af_afr: 0.0001,
  gnomad_af_amr: null,
  gnomad_af_eas: null,
  gnomad_af_eur: 0.0003,
  gnomad_af_fin: null,
  gnomad_af_sas: null,
  is_novel: false,
  clinvar_significance: "Pathogenic",
  clinvar_review_stars: 2,
  clinvar_accession: "VCV000012345",
  clinvar_conditions: "Breast-ovarian cancer, familial 1",
  cadd_phred: 28.5,
  revel: 0.892,
  deleterious_count: 2,
  deleterious_total_assessed: 2,
  ensemble_pathogenic: true,
  evidence_conflict: false,
  evidence_level: 4,
  disease_name: "Breast cancer",
  inheritance_pattern: "AD",
}

describe("slide-in detail panels expose dialog semantics (#703)", () => {
  it("carrier panel is a modal dialog and takes focus", () => {
    render(
      <CarrierPanel
        variant={CARRIER_VARIANT}
        sampleId={1}
        geneNote={undefined}
        onClose={vi.fn()}
      />,
    )
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it("cancer panel is a modal dialog and takes focus", () => {
    render(<CancerPanel variant={CANCER_VARIANT} sampleId={1} onClose={vi.fn()} />)
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it("cardiovascular panel is a modal dialog and takes focus", () => {
    render(<CardiovascularPanel variant={CARDIO_VARIANT} onClose={vi.fn()} />)
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it("rare-variants panel is a modal dialog and takes focus", () => {
    render(<RareVariantPanel variant={RARE_VARIANT} onClose={vi.fn()} />)
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog.contains(document.activeElement)).toBe(true)
  })
})

// ── Categorical pathway detail panels wire useDialogFocus (#1216) ────────────

/** The seven categorical-pathway slide-in panels. gene-health's was migrated in
 *  #703/#846; these siblings declared role="dialog" + aria-modal but never wired
 *  useDialogFocus, so they were modals in name only (no focus-in / trap / restore
 *  / inert). Each must now behave like the variant panels above. */
type PathwayPanelProps = {
  pathwayId: string
  pathwayName: string
  sampleId: number
  onClose: () => void
}

const PATHWAY_PANELS: ReadonlyArray<readonly [string, ComponentType<PathwayPanelProps>]> = [
  ["nutrigenomics", NutrigenomicsPathwayPanel],
  ["traits", TraitsPathwayPanel],
  ["methylation", MethylationPathwayPanel],
  ["skin", SkinPathwayPanel],
  ["allergy", AllergyPathwayPanel],
  ["sleep", SleepPathwayPanel],
  ["fitness", FitnessPathwayPanel],
]

describe("categorical pathway detail panels expose dialog semantics + focus-in (#1216)", () => {
  it.each(PATHWAY_PANELS)(
    "%s pathway panel is a focusable modal dialog and moves focus into itself",
    (_name, Panel) => {
      render(
        <Panel
          pathwayId="folate_metabolism"
          pathwayName="Folate Metabolism"
          sampleId={1}
          onClose={vi.fn()}
        />,
      )
      const dialog = screen.getByRole("dialog")
      expect(dialog).toHaveAttribute("aria-modal", "true")
      // tabIndex={-1} makes the container itself a programmatic focus target —
      // the focus-in fallback when the panel has no focusable child yet.
      expect(dialog).toHaveAttribute("tabindex", "-1")
      // useDialogFocus moved focus into the panel on open (the #1216 contract).
      expect(dialog.contains(document.activeElement)).toBe(true)
    },
  )
})
