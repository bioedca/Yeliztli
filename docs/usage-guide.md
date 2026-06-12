# Usage Guide

This guide walks through every major feature of Yeliztli. After completing the [Setup Guide](setup-guide.md), open your browser to [http://localhost:8000](http://localhost:8000) (native install) or [http://localhost:5173](http://localhost:5173) (development mode).

---

## Dashboard

The dashboard is your home screen after uploading a sample. It displays:

- **Status bar** (top) — Current sample name, annotation status, database versions
- **Module cards** — Quick-access grid linking to each analysis module with finding counts
- **High-confidence findings** — Top findings across all modules (3-4 star evidence)
- **QC summary** — Collapsible section with sample quality metrics (heterozygosity, call rate, per-chromosome counts)

### Sample Selector

Use the sample selector in the top navigation bar to switch between uploaded samples. Each sample has its own isolated database with independent annotation results.

---

## Uploading Data

### Supported formats

Yeliztli accepts 23andMe raw data files:

- **23andMe v3, v4, v5** — Auto-detected from file header
- File types: `.txt` (plain text) or `.zip` (compressed)

### Upload process

1. Click **Upload** from the dashboard or sidebar
2. Drag and drop your file (or click to browse)
3. Yeliztli parses the file and displays progress
4. Annotation runs automatically in the background via the Huey task worker
5. You are redirected to the dashboard when annotation completes

Annotation typically takes under 2 minutes for a standard 23andMe file (~600k variants).

---

## Variant Explorer

The variant table is the central hub for browsing your genomic data.

### Navigation

- **Chromosome anchors** — Jump to any chromosome using the navigation bar
- **Infinite scroll** — Variants load progressively as you scroll
- **Total count** — Displayed asynchronously (first page loads immediately)

### Filtering

- Toggle between **All variants** and **Annotated only**
- Use the advanced filter panel for specific criteria (gene, consequence, allele frequency, ClinVar significance, etc.)
- Filter by **variant tags** you have applied

### Column Presets

Switch between predefined column layouts:

| Preset | Columns shown |
|--------|---------------|
| Clinical | Gene, consequence, ClinVar, gnomAD AF, HGVS |
| Research | All annotation sources, in-silico scores |
| Frequency | Population frequencies (gnomAD subpopulations) |
| Scores | CADD, REVEL, SIFT, PolyPhen, MutationTaster |

Create custom presets from **Column Settings** (select columns, name the preset, save).

### Variant Detail

Click any variant row to open the **side panel** with a summary. Click **Open full detail** for the full 6-tab detail page:

1. **Overview** — Key annotations, ClinVar interpretation, consequence
2. **Frequencies** — Population frequencies across gnomAD subpopulations
3. **Scores** — In-silico pathogenicity predictions
4. **Literature** — PubMed citations (cached, fetched via Entrez API)
5. **Protein** — Nightingale protein domain diagram with variant position
6. **Gene** — Gene information, associated phenotypes (MONDO/HPO, optional OMIM)

### Variant Tagging

Apply tags to variants for personal tracking:

- **Predefined tags**: Pathogenic interest, Benign confirmed, Follow-up, Research
- **Custom tags**: Create your own from the tag menu
- Filter the variant table by tag to see tagged subsets

### VUS Watching

For Variants of Uncertain Significance (VUS), click **Watch** to monitor for reclassification. When reference databases are updated, watched variants are re-checked and you receive a banner notification if any are reclassified.

---

## Genome Browser

The built-in IGV.js genome browser provides visual variant inspection:

- Navigate by gene name, rsID, or coordinates
- Variant track shows your genotypes with color-coded annotations
- Reference genome track for sequence context
- Click variants in the browser to open the variant detail panel

---

## Analysis Modules

Each module provides findings with evidence-based scoring:

### Evidence Stars

| Rating | Criteria |
|--------|----------|
| ★★★★ | ClinVar Pathogenic/Likely Pathogenic (2+ star review) OR CPIC Level A OR GWAS OR > 5, p < 5e-8 |
| ★★★ | ClinVar Likely Pathogenic (1-star) OR CPIC Level B OR replicated GWAS |
| ★★ | VUS with functional evidence OR single large GWAS OR PharmGKB 2A/2B |
| ★ | Single study OR candidate gene OR PharmGKB 3/4 |

### Pharmacogenomics

Star-allele calling for 8 key pharmacogenes (CYP2D6, CYP2C19, CYP2C9, CYP3A5, DPYD, TPMT, SLCO1B1, UGT1A1). Based on CPIC guidelines with three-state data quality indicators (Complete / Partial / Insufficient). Includes prescribing alerts for common drug interactions.

### Nutrigenomics

Categorical pathway scoring across 6 pathways: Folate Metabolism, Vitamin D, Vitamin B12, Omega-3, Iron Metabolism, and Lactose Tolerance. Outputs are categorical (Elevated Need / Moderate Need / Standard) — never numeric risk scores. Evidence rating hard-caps pathways at Moderate when evidence is 1-star.

### Methylation

Deep-dive into MTHFR and methylation pathways with 5 sub-pathways: Folate & MTHFR, Methionine Cycle, Transsulfuration, BH4 Pathway, and Choline Metabolism. Covers ~35 SNPs with pathway levels determined by the highest called SNP category; multiple Moderate findings are reported as context rather than promoted to Elevated.

### Cancer

28-gene hereditary cancer panel including BRCA1/2, TP53, PALB2, MLH1, and MSH2. Reports ClinVar Pathogenic/Likely Pathogenic findings grouped by cancer syndrome. Includes Polygenic Risk Score (PRS) computation with bootstrap 95% confidence intervals.

### Cardiovascular

16-gene panel covering familial hypercholesterolemia (LDLR, PCSK9, APOB), channelopathies, and cardiomyopathies. Reports FH variant status and cardiovascular risk findings.

### APOE

Determines APOE diplotype from rs429358 and rs7412. Generates three findings: cardiovascular risk implications, Alzheimer's disease association, and lipid/dietary considerations. Includes an opt-in gate with links to educational resources (NIA, Alzheimer's Association, NSGC). The APOE section cannot be dismissed without acknowledgment.

### Carrier Status

Screens for carrier status across 7 genes: CFTR, HBB, GBA, HEXA, BRCA1, BRCA2, and SMN1. Reports heterozygous Pathogenic/Likely Pathogenic variants only, framed in reproductive context. BRCA1/2 findings appear in both Cancer and Carrier Status modules.

### Ancestry

PCA-based ancestry inference with admixture fractions. Haplogroup assignment via:

- **Mitochondrial (mtDNA)** — PhyloTree reference
- **Y-chromosome** — ISOGG reference (skipped for XX samples)

Results displayed as admixture bar chart and PCA scatter plot with reference populations.

### Fitness

Athletic trait analysis covering 4 pathways: Endurance Capacity, Power & Strength, Recovery, and Training Response. Features ACTN3 three-state calling (RR / RX / XX) and ACE proxy genotyping.

### Sleep

Sleep phenotype analysis with 4 pathways: Caffeine Sensitivity (CYP1A2 metabolizer status), Chronotype, Sleep Quality, and Sleep Disorders. The Sleep Disorders pathway includes the HLA-DQ region marker rs2858884 for informational context only — narcolepsy type 1 risk is driven by HLA-DQB1*06:02, which is not directly typed by SNP-array panels, so no narcolepsy risk is inferred.

### Skin

Dermatological trait analysis with MC1R multi-allele calling and FLG filaggrin proxy. Covers 4 pathways: Pigmentation & UV Response, Skin Barrier Function, Oxidative Stress, and Skin Micronutrients.

### Allergy & Immune Sensitivities

HLA proxy calling for immune-mediated conditions. Covers 4 pathways: Atopic Conditions, Drug Hypersensitivities, Food Sensitivities (including celiac DQ2/DQ8 typing), and Histamine Metabolism.

### Traits & Personality

PRS-based trait analysis including cognitive traits, Big Five personality dimensions, and behavioral traits. All findings are capped at 2-star evidence with a module-level "Research Use Only" disclaimer. Displayed with radar chart for Big Five scores.

### Gene Health

17 disease conditions from ClinVar and GWAS data, grouped by body system:

- **Neurological**: Alzheimer's, Parkinson's, Multiple Sclerosis, Epilepsy
- **Metabolic**: Type 2 Diabetes, Obesity, Gout, Thyroid conditions
- **Autoimmune**: Rheumatoid Arthritis, IBD, Celiac Disease, Lupus, Psoriasis
- **Sensory**: Age-related Macular Degeneration, Glaucoma, Hearing Loss

Cross-links to APOE and Allergy modules where relevant.

---

## Query Builder

For advanced users, the query builder lets you construct complex variant filters:

- **Visual builder** — Drag-and-drop filter rules with AND/OR grouping
- **SQL console** — Write raw SQL queries against your variant database (read-only)
- **Saved queries** — Save, name, and re-run your favorite queries
- **Export results** — Download query results as VCF, TSV, JSON, or CSV

---

## Reports

Generate PDF reports from your analysis results:

1. Go to **Reports** from the sidebar
2. Select which modules to include
3. Preview the report layout
4. Click **Generate PDF** — uses Playwright for high-fidelity rendering
5. Download the generated report

Reports use clinical typography with evidence stars rendered in print CSS. You can also generate single-variant evidence cards (PDF or PNG) from any variant detail page.

---

## Export

Export your data in multiple formats from the variant table or query results:

| Format | Description |
|--------|-------------|
| VCF 4.2 | Standard variant call format |
| TSV | Tab-separated with all annotation columns |
| JSON | Structured JSON with nested annotations |
| CSV | Comma-separated for spreadsheet import |
| FHIR R4 | DiagnosticReport Bundle (JSON) for clinical interoperability |

---

## Settings

Access settings from the sidebar gear icon.

### Database Management

- View installed database versions and sizes
- Trigger manual updates for individual databases
- Configure auto-update schedule (startup / daily / weekly)
- Set bandwidth windows for large downloads (> 100 MB)
- View update history log

### System Health

Admin panel showing:

- **Log explorer** — Search and filter structured application logs
- **Database stats** — Row counts, file sizes, last-modified dates for all databases
- **Disk usage** — Storage breakdown by database and sample
- **Database health** — Per-database state (Ready / Downloading / Building / Partial /
  Corrupt / Failed / Not installed) with an integrity check that confirms each database
  is actually readable by the annotation engine. For a database that needs attention you can:
  - **Resume** — continue an interrupted download from where it stopped (no restart)
  - **Verify** — run a deep integrity check (`PRAGMA quick_check`) on demand
  - **Clean** — remove a partial/corrupt artifact so it can be re-downloaded cleanly

  Health is also surfaced during the setup wizard: an interrupted database download shows a
  **Resume** button so a crash or disconnect mid-setup never forces you to start over.

### Authentication

Enable optional authentication to protect your instance:

1. Go to **Settings > Authentication**
2. Enable authentication and set a PIN or password
3. Sessions expire after 4 hours of inactivity
4. All routes are protected when auth is enabled

### Theme

Toggle between Light, Dark, and System (follows OS preference) from the top navigation bar or Settings.

### Backup & Restore

- **Export**: Create a `.tar.gz` backup of all samples, configuration, and optionally reference databases
- **Import**: Restore from a backup archive

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Cmd/Ctrl + K` | Open command palette |
| `Escape` | Close panel / dialog |

### Command Palette

Press `Cmd+K` (macOS) or `Ctrl+K` (Linux/Windows) to open the command palette. Search for:

- **Variants** by rsID or gene name
- **Pages** by name (Dashboard, Settings, any module)
- **Actions** like switching samples or toggling dark mode

The command palette is navigation-only — no destructive or state-changing actions.

---

## Data Privacy

Yeliztli is designed with privacy as a core principle:

- **Localhost only** — The server binds to 127.0.0.1 by default
- **No telemetry** — Zero analytics, tracking, or usage reporting
- **No outbound variant data** — Your genomic data never leaves your machine
- **No cloud processing** — All annotation runs against local SQLite databases
- **Optional auth** — Protect your instance with a PIN/password if others share your computer
- **Nuclear delete** — Wipe all data with a single action from Settings

---

## FAQ

**Q: How long does annotation take?**
A standard 23andMe file (~600,000 variants) annotates in under 2 minutes.

**Q: Can I analyze multiple samples?**
Yes. Upload additional files from the Upload page. Each sample gets its own isolated database. Use the sample selector in the top nav to switch between them. If two samples come from the same person — for example, a 23andMe export and an AncestryDNA export — you can group them under an **individual** and optionally **merge** them into a single union sample with a concordance report. See [Multi-Source Sample Merging](multi-source-merging.md) for the end-to-end walkthrough.

**Q: What if annotation is interrupted?**
Yeliztli uses crash recovery: the partial annotation is deleted and re-run from scratch. With under 2-minute runtime, checkpointing is unnecessary.

**Q: Can I use files from other services (AncestryDNA, MyHeritage)?**
Not yet in v1. 23andMe (v3/v4/v5) is the only supported format. AncestryDNA support is the top priority for post-v1.

**Q: Do I need an internet connection?**
Only for initial database downloads and optional PubMed literature lookups. All analysis runs locally once databases are installed.

**Q: How do I add OMIM data?**
Obtain an API key from [omim.org/api](https://omim.org/api) and enter it in Settings > External Services (or during setup wizard Step 4). OMIM enriches gene-disease associations but is not required — MONDO/HPO provides baseline phenotype data.
