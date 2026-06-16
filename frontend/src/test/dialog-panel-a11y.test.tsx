/** A11y coverage for the slide-in detail panels (#703/#846):
 *  - the shared `useDialogFocus` hook (focus-in / Tab-trap / focus-restore);
 *  - modal hardening (background inert + scroll lock);
 *  - the four previously-bare panels now expose role="dialog" + aria-modal and
 *    move focus into themselves on open.
 */

import { useRef } from "react"
import { describe, expect, it, vi } from "vitest"
import { render, screen, fireEvent } from "./test-utils"
import { useDialogFocus } from "@/hooks/useDialogFocus"

import CarrierPanel from "@/components/carrier/VariantDetailPanel"
import CancerPanel from "@/components/cancer/VariantDetailPanel"
import CardiovascularPanel from "@/components/cardiovascular/VariantDetailPanel"
import RareVariantPanel from "@/components/rare-variants/VariantDetailPanel"
import type { CarrierVariant } from "@/types/carrier"
import type { CancerVariant } from "@/types/cancer"
import type { CardiovascularVariant } from "@/types/cardiovascular"
import type { RareVariant } from "@/types/rare-variants"

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
