"""Module-specific disclaimers and display names for PDF reports (P4-07).

Each analysis module has its own disclaimer text that appears in the
report below the module header.  Disclaimer text is drawn from the
canonical ``backend.disclaimers`` module where possible, with additional
report-specific condensed versions for modules that lack a dedicated
disclaimer.
"""

from __future__ import annotations

from backend.disclaimers import (
    CANCER_DISCLAIMER_TEXT,
    CANCER_DISCLAIMER_TITLE,
    CARDIOVASCULAR_DISCLAIMER_TEXT,
    CARDIOVASCULAR_DISCLAIMER_TITLE,
    CARRIER_STATUS_DISCLAIMER_TEXT,
    CARRIER_STATUS_DISCLAIMER_TITLE,
)

# ── Module display names ──────────────────────────────────────────────

MODULE_DISPLAY_NAMES: dict[str, str] = {
    "cancer": "Cancer Predisposition",
    "cardiovascular": "Cardiovascular Genetics",
    "apoe": "APOE Genotype",
    "pharmacogenomics": "Pharmacogenomics",
    "nutrigenomics": "Nutrigenomics",
    "metabolic": "Metabolic (T2D & Obesity)",
    "fh": "Familial Hypercholesterolemia",
    "ebmd": "Bone Density (eBMD)",
    "carrier": "Carrier Status",
    "ancestry": "Ancestry & Haplogroups",
    "gene_health": "Gene Health",
    "fitness": "Gene Fitness",
    "sleep": "Gene Sleep",
    "methylation": "MTHFR & Methylation",
    "skin": "Gene Skin",
    "allergy": "Gene Allergy",
    "traits": "Traits & Personality",
    "rare_variants": "Rare Variant Finder",
}

# ── Module disclaimers ────────────────────────────────────────────────
# Each entry: {"title": str, "text": str}

MODULE_DISCLAIMERS: dict[str, dict[str, str]] = {
    "cancer": {
        "title": CANCER_DISCLAIMER_TITLE,
        "text": CANCER_DISCLAIMER_TEXT,
    },
    "cardiovascular": {
        "title": CARDIOVASCULAR_DISCLAIMER_TITLE,
        "text": CARDIOVASCULAR_DISCLAIMER_TEXT,
    },
    "carrier": {
        "title": CARRIER_STATUS_DISCLAIMER_TITLE,
        "text": CARRIER_STATUS_DISCLAIMER_TEXT,
    },
    "apoe": {
        "title": "About APOE Genotype Results",
        "text": (
            "APOE genotype information is associated with risk for "
            "late-onset Alzheimer's disease and cardiovascular conditions. "
            "Having an APOE \u03b54 allele does NOT mean you will develop "
            "Alzheimer's disease. Many people with \u03b54 never develop the "
            "condition. This result is based on a consumer genotyping chip "
            "and is NOT a clinical diagnostic test. Consult a genetic "
            "counselor for personalized risk assessment."
        ),
    },
    "pharmacogenomics": {
        "title": "About Pharmacogenomics Results",
        "text": (
            "Pharmacogenomic results indicate how your genetic variants "
            "may affect drug metabolism. These results are based on CPIC "
            "guidelines and should be discussed with your prescribing "
            "physician or pharmacist before making any changes to "
            "medication regimens. Consumer genotyping chips test only a "
            "subset of known pharmacogenomic variants."
        ),
    },
    "nutrigenomics": {
        "title": "About Nutrigenomics Results",
        "text": (
            "Nutrigenomic findings reflect statistical associations between "
            "genetic variants and nutritional metabolism pathways. Results "
            "are presented as categorical assessments (Elevated/Moderate/"
            "Standard) and should not be used as the basis for dietary "
            "supplementation without consulting a healthcare provider."
        ),
    },
    "ancestry": {
        "title": "About Ancestry Results",
        "text": (
            "Ancestry inference is based on principal component analysis "
            "of genotype data and reference population panels. Results are "
            "statistical estimates and may not fully reflect your family "
            "history or self-identified ancestry. Haplogroup assignments "
            "trace specific maternal (mtDNA) or paternal (Y-chromosome) "
            "lineages only."
        ),
    },
    "traits": {
        "title": "About Traits & Personality Results",
        "text": (
            "Trait associations are derived from genome-wide association "
            "studies (GWAS) and represent statistical correlations at the "
            "population level. Individual trait expression is influenced by "
            "many non-genetic factors. All trait findings are capped at "
            "\u2605\u2605\u2606\u2606 evidence level. These results are for "
            "research and educational purposes only."
        ),
    },
    "gene_health": {
        "title": "About Gene Health Results",
        "text": (
            "Gene Health findings are based on known genetic associations "
            "with disease conditions from ClinVar and GWAS data. "
            "Predisposition is not diagnosis. Consult a healthcare "
            "provider for clinical interpretation and management."
        ),
    },
    "fitness": {
        "title": "About Gene Fitness Results",
        "text": (
            "Fitness-related genetic findings reflect associations from "
            "published research. Individual athletic performance is "
            "influenced by training, nutrition, and many non-genetic "
            "factors. These results should not replace professional "
            "sports medicine or fitness advice."
        ),
    },
    "sleep": {
        "title": "About Gene Sleep Results",
        "text": (
            "Sleep-related genetic findings reflect associations between "
            "variants and circadian rhythm, sleep quality, and related "
            "traits. Sleep patterns are strongly influenced by lifestyle, "
            "environment, and health conditions. Consult a sleep specialist "
            "for clinical concerns."
        ),
    },
    "methylation": {
        "title": "About MTHFR & Methylation Results",
        "text": (
            "Methylation pathway findings are based on known variant "
            "effects on folate metabolism and related biochemical pathways. "
            "Clinical significance of common MTHFR variants is debated. "
            "Supplementation decisions should be made with a healthcare "
            "provider based on clinical lab work, not genotype alone."
        ),
    },
    "skin": {
        "title": "About Gene Skin Results",
        "text": (
            "Skin-related genetic findings reflect associations with "
            "pigmentation, skin conditions, and dermatological traits. "
            "These are statistical associations and do not constitute "
            "dermatological diagnosis. Consult a dermatologist for "
            "clinical skin concerns."
        ),
    },
    "allergy": {
        "title": "About Gene Allergy Results",
        "text": (
            "Allergy-related findings include HLA proxy genotyping with "
            "limited accuracy. Drug hypersensitivity alerts based on HLA "
            "proxies require clinical confirmation before affecting "
            "prescribing decisions. Consult an allergist or immunologist "
            "for clinical allergy assessment."
        ),
    },
}
