/** Tests for the APOE UI (P3-22d). */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "./test-utils"
import userEvent from "@testing-library/user-event"
import APOEGate from "@/components/apoe-gate/APOEGate"
import APOEGenotypeCard from "@/components/apoe-gate/APOEGenotypeCard"
import APOEFindingCard from "@/components/apoe-gate/APOEFindingCard"
import APOECaveats from "@/components/apoe-gate/APOECaveats"
import type {
  APOEGateDisclaimerResponse,
  APOEGenotypeResponse,
  APOEFinding,
} from "@/types/apoe"

// ── Fixtures ──────────────────────────────────────────────────────────

const DISCLAIMER: APOEGateDisclaimerResponse = {
  title: "APOE Genetic Information Disclosure",
  text: "You are about to view information about your APOE genotype.\n\n**Important considerations before viewing:**\n\n- Having an APOE e4 allele does NOT mean you will develop Alzheimer's disease.\n\n**Resources:**\n- National Institute on Aging: https://www.nia.nih.gov/health/alzheimers",
  accept_label: "I Understand — Show My APOE Results",
  decline_label: "Not Now — Skip APOE Results",
}

const GENOTYPE_E3E4: APOEGenotypeResponse = {
  status: "determined",
  diplotype: "e3/e4",
  has_e4: true,
  e4_count: 1,
  has_e2: false,
  e2_count: 0,
  rs429358_genotype: "CT",
  rs7412_genotype: "CC",
}

const GENOTYPE_E3E3: APOEGenotypeResponse = {
  status: "determined",
  diplotype: "e3/e3",
  has_e4: false,
  e4_count: 0,
  has_e2: false,
  e2_count: 0,
  rs429358_genotype: "TT",
  rs7412_genotype: "CC",
}

const GENOTYPE_E2E4: APOEGenotypeResponse = {
  status: "determined",
  diplotype: "e2/e4",
  has_e4: true,
  e4_count: 1,
  has_e2: true,
  e2_count: 1,
  rs429358_genotype: "CT",
  rs7412_genotype: "CT",
}

const GENOTYPE_NOT_RUN: APOEGenotypeResponse = {
  status: "not_run",
  diplotype: null,
  has_e4: null,
  e4_count: null,
  has_e2: null,
  e2_count: null,
  rs429358_genotype: null,
  rs7412_genotype: null,
}

const GENOTYPE_MISSING: APOEGenotypeResponse = {
  status: "missing_snps",
  diplotype: null,
  has_e4: null,
  e4_count: null,
  has_e2: null,
  e2_count: null,
  rs429358_genotype: null,
  rs7412_genotype: null,
}

const CV_FINDING: APOEFinding = {
  category: "cardiovascular_risk",
  evidence_level: 4,
  finding_text: "APOE ε3/ε4 genotype is associated with modestly elevated LDL cholesterol and cardiovascular risk.",
  phenotype: "Elevated LDL cholesterol",
  conditions: "Cardiovascular disease, Type III hyperlipoproteinemia",
  diplotype: "e3/e4",
  pmid_citations: ["21460841", "9343467"],
  detail_json: { risk_level: "modestly elevated" },
}

const ALZHEIMERS_FINDING: APOEFinding = {
  category: "alzheimers_risk",
  evidence_level: 4,
  finding_text: "APOE ε3/ε4 genotype confers approximately 3.2× relative risk for late-onset Alzheimer's disease.",
  phenotype: "Late-onset Alzheimer's disease",
  conditions: "Alzheimer's disease",
  diplotype: "e3/e4",
  pmid_citations: ["21460841", "17309940"],
  detail_json: { non_actionable: true, risk_level: "elevated", relative_risk: 3.2 },
}

const LIPID_FINDING: APOEFinding = {
  category: "lipid_dietary",
  evidence_level: 3,
  finding_text: "Carriers of ε4 may show enhanced LDL response to dietary saturated fat intake.",
  phenotype: "Enhanced saturated fat sensitivity",
  conditions: null,
  diplotype: "e3/e4",
  pmid_citations: ["19124690"],
  detail_json: { risk_level: "enhanced response" },
}

const CAVEAT_FINDING: APOEFinding = {
  ...CV_FINDING,
  detail_json: {
    ...CV_FINDING.detail_json,
    array_reliability: {
      caveat:
        "APOE ε-status here is derived from consumer genotyping-array calls at rs429358 and rs7412.",
      confirm_in_clia_recommended: true,
      concordance_with_direct_genotyping: "~90% ε genotype / ~93% ε4 status",
      pmids: ["24448547", "22972946"],
    },
    source_discrepancies: [
      {
        rsid: "rs429358",
        gene: "APOE",
        kept_genotype: "CT",
        affects_e4_status: true,
        note: "Your source files disagree at rs429358 (APOE).",
        calls: [
          {
            source: "S1",
            genotype: "TT",
            implied_diplotype: "ε3/ε3",
            e4_present: false,
          },
          {
            source: "S2",
            genotype: "CT",
            implied_diplotype: "ε3/ε4",
            e4_present: true,
          },
        ],
      },
    ],
  },
}

// ── APOEGate tests ────────────────────────────────────────────────────

describe("APOEGate", () => {
  const onAccept = vi.fn()
  const onDecline = vi.fn()

  beforeEach(() => {
    onAccept.mockClear()
    onDecline.mockClear()
  })

  it("renders gate title", () => {
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={false}
      />,
    )
    expect(screen.getByText("APOE Genetic Information Disclosure")).toBeInTheDocument()
  })

  it("renders accept and decline buttons with labels", () => {
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={false}
      />,
    )
    expect(screen.getByTestId("apoe-gate-accept")).toHaveTextContent(
      "I Understand — Show My APOE Results",
    )
    expect(screen.getByTestId("apoe-gate-decline")).toHaveTextContent(
      "Not Now — Skip APOE Results",
    )
  })

  it("calls onAccept when accept button clicked", async () => {
    const user = userEvent.setup()
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={false}
      />,
    )
    await user.click(screen.getByTestId("apoe-gate-accept"))
    expect(onAccept).toHaveBeenCalledTimes(1)
  })

  it("calls onDecline when decline button clicked", async () => {
    const user = userEvent.setup()
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={false}
      />,
    )
    await user.click(screen.getByTestId("apoe-gate-decline"))
    expect(onDecline).toHaveBeenCalledTimes(1)
  })

  it("disables buttons while acknowledging", () => {
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={true}
      />,
    )
    expect(screen.getByTestId("apoe-gate-accept")).toBeDisabled()
    expect(screen.getByTestId("apoe-gate-decline")).toBeDisabled()
  })

  it("shows processing text while acknowledging", () => {
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={true}
      />,
    )
    expect(screen.getByTestId("apoe-gate-accept")).toHaveTextContent("Processing...")
  })

  it("has accessible role and labelling", () => {
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={false}
      />,
    )
    expect(screen.getByRole("alertdialog")).toBeInTheDocument()
    expect(screen.getByRole("alertdialog")).toHaveAttribute("aria-labelledby", "apoe-gate-title")
  })

  it("has test id for gate container", () => {
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={false}
      />,
    )
    expect(screen.getByTestId("apoe-gate")).toBeInTheDocument()
  })

  it("renders gate text content", () => {
    render(
      <APOEGate
        disclaimer={DISCLAIMER}
        onAccept={onAccept}
        onDecline={onDecline}
        isAcknowledging={false}
      />,
    )
    expect(
      screen.getByText(/APOE genotype/),
    ).toBeInTheDocument()
  })
})

// ── APOEGenotypeCard tests ────────────────────────────────────────────

describe("APOEGenotypeCard", () => {
  it("renders diplotype badge for determined genotype", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E4} />)
    expect(screen.getByTestId("apoe-diplotype-badge")).toHaveTextContent("ε3/ε4")
  })

  it("renders ε3/ε3 diplotype", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E3} />)
    expect(screen.getByTestId("apoe-diplotype-badge")).toHaveTextContent("ε3/ε3")
  })

  it("renders SNP genotypes", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E4} />)
    expect(screen.getByText("CT")).toBeInTheDocument()
    expect(screen.getByText("CC")).toBeInTheDocument()
  })

  it("renders rs429358 codon label", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E4} />)
    expect(screen.getByText("rs429358 (codon 112)")).toBeInTheDocument()
  })

  it("renders rs7412 codon label", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E4} />)
    expect(screen.getByText("rs7412 (codon 158)")).toBeInTheDocument()
  })

  it("shows ε4 present indicator for e3/e4", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E4} />)
    expect(screen.getByTestId("apoe-e4-indicator")).toHaveTextContent("ε4 present (1 copy)")
  })

  it("shows no ε4 indicator for e3/e3", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E3} />)
    expect(screen.getByTestId("apoe-e4-indicator")).toHaveTextContent("No ε4 alleles")
  })

  it("shows ε2 indicator when present", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E2E4} />)
    expect(screen.getByTestId("apoe-e2-indicator")).toHaveTextContent("ε2 present (1 copy)")
  })

  it("does not show ε2 indicator when absent", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E4} />)
    expect(screen.queryByTestId("apoe-e2-indicator")).not.toBeInTheDocument()
  })

  it("shows not-run status message", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_NOT_RUN} />)
    expect(screen.getByTestId("apoe-genotype-status")).toHaveTextContent(
      "APOE analysis has not been run yet.",
    )
  })

  it("shows missing-snps status message", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_MISSING} />)
    expect(screen.getByTestId("apoe-genotype-status")).toHaveTextContent(
      /rs429358.*rs7412.*missing/,
    )
  })

  it("does not render diplotype badge for not-run status", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_NOT_RUN} />)
    expect(screen.queryByTestId("apoe-diplotype-badge")).not.toBeInTheDocument()
  })

  it("has accessible test id", () => {
    render(<APOEGenotypeCard genotype={GENOTYPE_E3E4} />)
    expect(screen.getByTestId("apoe-genotype-card")).toBeInTheDocument()
  })
})

// ── APOEFindingCard tests ─────────────────────────────────────────────

describe("APOEFindingCard", () => {
  it("renders cardiovascular risk finding", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.getByTestId("apoe-finding-cardiovascular_risk")).toBeInTheDocument()
    expect(screen.getByText("Cardiovascular Risk")).toBeInTheDocument()
  })

  it("renders Alzheimer's risk finding", () => {
    render(<APOEFindingCard finding={ALZHEIMERS_FINDING} />)
    expect(screen.getByTestId("apoe-finding-alzheimers_risk")).toBeInTheDocument()
    expect(screen.getByText("Alzheimer's Risk")).toBeInTheDocument()
  })

  it("renders lipid/dietary finding", () => {
    render(<APOEFindingCard finding={LIPID_FINDING} />)
    expect(screen.getByTestId("apoe-finding-lipid_dietary")).toBeInTheDocument()
    expect(screen.getByText("Lipid & Dietary Response")).toBeInTheDocument()
  })

  it("renders finding text", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.getByText(/modestly elevated LDL cholesterol/)).toBeInTheDocument()
  })

  it("renders evidence stars", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.getByLabelText("4 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders 3-star evidence for lipid finding", () => {
    render(<APOEFindingCard finding={LIPID_FINDING} />)
    expect(screen.getByLabelText("3 of 4 stars evidence")).toBeInTheDocument()
  })

  it("renders Non-Actionable badge for Alzheimer's finding", () => {
    render(<APOEFindingCard finding={ALZHEIMERS_FINDING} />)
    expect(screen.getByText("Non-Actionable")).toBeInTheDocument()
  })

  it("does not render Non-Actionable badge for CV finding", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.queryByText("Non-Actionable")).not.toBeInTheDocument()
  })

  it("renders phenotype when present", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.getByText("Elevated LDL cholesterol")).toBeInTheDocument()
  })

  it("renders conditions when present", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(
      screen.getByText("Cardiovascular disease, Type III hyperlipoproteinemia"),
    ).toBeInTheDocument()
  })

  it("renders PubMed citations", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.getByText("PMID:21460841")).toBeInTheDocument()
    expect(screen.getByText("PMID:9343467")).toBeInTheDocument()
  })

  it("renders PubMed links with correct URLs", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    const link = screen.getByText("PMID:21460841").closest("a")
    expect(link).toHaveAttribute("href", "https://pubmed.ncbi.nlm.nih.gov/21460841/")
    expect(link).toHaveAttribute("target", "_blank")
    expect(link).toHaveAttribute("rel", "noopener noreferrer")
  })

  it("renders diplotype context", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.getByText(/Based on ε3\/ε4 genotype/)).toBeInTheDocument()
  })

  it("renders risk level from detail_json", () => {
    render(<APOEFindingCard finding={CV_FINDING} />)
    expect(screen.getByText("modestly elevated")).toBeInTheDocument()
  })
})

// ── APOECaveats tests ─────────────────────────────────────────────────

describe("APOECaveats", () => {
  it("does not render without source or reliability caveats", () => {
    const { container } = render(<APOECaveats findings={[CV_FINDING]} />)
    expect(container.firstChild).toBeNull()
  })

  it("renders source-discrepancy calls and ε4 status", () => {
    render(<APOECaveats findings={[CAVEAT_FINDING]} />)

    const discrepancy = screen.getByTestId("apoe-source-discrepancy-rs429358")
    expect(discrepancy).toHaveTextContent("Changes ε4 status")
    expect(discrepancy).toHaveTextContent("S1")
    expect(discrepancy).toHaveTextContent("TT")
    expect(discrepancy).toHaveTextContent(/ε3\/ε3/)
    expect(discrepancy).toHaveTextContent(/ε4 absent/)
    expect(discrepancy).toHaveTextContent("S2")
    expect(discrepancy).toHaveTextContent("CT")
    expect(discrepancy).toHaveTextContent(/ε3\/ε4/)
    expect(discrepancy).toHaveTextContent(/ε4 present/)
  })

  it("renders the array-reliability caveat and citations", () => {
    render(<APOECaveats findings={[CAVEAT_FINDING]} />)

    expect(screen.getByTestId("apoe-array-reliability")).toHaveTextContent(
      "CLIA confirmation recommended",
    )
    expect(screen.getByText(/consumer genotyping-array calls/)).toBeInTheDocument()
    expect(screen.getByText(/~90% ε genotype \/ ~93% ε4 status/)).toBeInTheDocument()
    expect(screen.getByText("PMID:24448547")).toHaveAttribute(
      "href",
      "https://pubmed.ncbi.nlm.nih.gov/24448547/",
    )
  })
})
