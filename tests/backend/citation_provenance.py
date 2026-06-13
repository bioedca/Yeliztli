"""Central registry for the repo-wide citation-provenance guard (gh #276 / #277).

There is a recurring class of "<panel> row cites an unrelated PMID" defects:
a curated ``pmids`` entry that resolves to a paper from an entirely different
field, which then rides verbatim into ``findings.pmid_citations`` and reaches
user-facing evidence links. Each has been fixed one at a time, but nothing
caught the *class* — and a PMID purged from one panel could silently reappear
in another.

This module is the single, offline, deterministic place that prevents
reintroduction of the **globally unrelated** PMIDs: papers whose topic is so far
outside the variant/disease-association domain of every curated panel that they
could never be a legitimate citation anywhere in the repo. ``test_citation_
provenance.py`` scans *all* ``backend/data/panels/*.json`` for these and fails
if any reappears.

Scope — what belongs here vs. what does not:
  • HERE: PMIDs topically unrelated to the entire domain (e.g. marine ecology,
    HIV virology, environmental chemistry) — safe to ban repo-wide, no panel
    could ever cite them legitimately.
  • NOT here: PMIDs that are real human-genetics references merely *transposed
    to the wrong gene* (e.g. a GeneReviews chapter, or a GWAS for a different
    locus). Those stay in their per-panel/per-row guards, because the same PMID
    can be the *correct* citation for another row — a repo-wide ban would block
    a legitimate future use (e.g. a Lynch GeneReviews PMID belongs on the right
    cancer row even if it was wrong on a cardiovascular one).

Provenance of the titles below: NCBI E-utilities ``esummary`` (db=pubmed),
accessed 2026-06-13. Titles are recorded so a reviewer can see, without a
network call, *why* each PMID is off-domain.

To register a new entry: confirm (a) the PMID is absent from every panel now and
(b) its title/abstract is unrelated to all panel topics, then add it below with
its title and the panel/gene it was mis-cited in. If a defect is a transposed
*relevant* PMID, add a per-panel guard instead (see e.g. test_methylation_panel.py).
"""

from __future__ import annotations

# PMID -> human-readable note: "<title> — <why off-domain> (mis-cited in <where>)".
GLOBALLY_UNRELATED_PMIDS: dict[str, str] = {
    "11735260": (
        "Bcl-2 regulation of Na/Ca exchange & mitochondrial energetics in the heart — "
        "apoptosis mechanism, not a TNNT2/variant association "
        "(mis-cited in cardiovascular TNNT2)"
    ),
    "12181445": (
        "CDK2 inhibition via the Chk1-Cdc25A S-phase checkpoint — cancer cell-cycle "
        "mechanism, unrelated to methylation nutrient genetics (mis-cited in methylation MTRR)"
    ),
    "15657627": (
        "Salmonella surveillance in New South Wales — infectious-disease epidemiology, "
        "off-domain (mis-cited in sleep ADORA2A)"
    ),
    "17343727": (
        "Automated array-CGH for FFPE tumor material — laboratory methods, off-domain "
        "(mis-cited in sleep PER3)"
    ),
    "18197166": (
        "microRNA post-transcriptional regulation review — not a variant association "
        "(mis-cited in traits)"
    ),
    "19289833": (
        "HIV-1 gp41 CCR5-inhibitor resistance — virology, off-domain (mis-cited in sleep PER3)"
    ),
    "20162554": (
        "Antigen strength & IL-10 Treg generation — immunology mechanism, off-domain "
        "(mis-cited in methylation DHFR)"
    ),
    "20689844": (
        "Biodiversity of the Mediterranean Sea — marine ecology, off-domain "
        "(mis-cited in gene_health)"
    ),
    "21149639": (
        "GPER1/GPR30 plasma-membrane localization — receptor cell biology, off-domain "
        "(mis-cited in hemochromatosis HFE)"
    ),
    "21248726": (
        "Electronic medical records in pharmacogenomics — informatics review, not a "
        "variant association (mis-cited in allergy)"
    ),
    "22177658": (
        "Treatment decision-making in prostate cancer patients — psycho-oncology, "
        "off-domain (mis-cited in allergy)"
    ),
    "22232607": (
        "DNA methylation & phenotypic plasticity in invertebrates — invertebrate "
        "epigenetics, off-domain (mis-cited in sleep ADORA2A)"
    ),
    "23430975": (
        "Arginine butyrate for Duchenne muscular dystrophy — DMD therapeutics, off-domain "
        "(mis-cited in sleep PER3)"
    ),
    "25904306": (
        "Dispersant-induced lysosome abnormality in macrophages — nanotoxicology, "
        "off-domain (mis-cited in gene_health)"
    ),
    "25979839": (
        "Transcranial direct-current stimulation against SUDEP — neurostimulation, "
        "off-domain (mis-cited in sleep ADORA2A)"
    ),
    "26092464": (
        "Ectomycorrhizal communities on beech roots — fungal ecology, off-domain "
        "(mis-cited in allergy)"
    ),
    "26547463": (
        "'Advancing Cardiovascular Science' editorial — not a variant association "
        "(mis-cited in cardiovascular)"
    ),
    "27095798": (
        "Early-career physicians' antibiotic prescribing for URTIs — health-services "
        "research, off-domain (mis-cited in cardiovascular)"
    ),
    "27914672": (
        "Superficial basal cell carcinoma — dermatology clinical review, off-domain "
        "(mis-cited in cardiovascular)"
    ),
    "28774630": (
        "Chlorination of benzophenone-4 in the presence of iodide — environmental "
        "chemistry, off-domain (mis-cited in cancer MUTYH)"
    ),
    "30580001": (
        "WITHDRAWN: 68Ga-PSMA-11 PET staging & radiotherapy plans — withdrawn radiology "
        "paper (mis-cited in cardiovascular LPA)"
    ),
}
