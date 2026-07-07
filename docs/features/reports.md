# Reports

Generate a PDF report from your analysis results:

1. Open **Reports** from the sidebar.
2. Select which modules to include.
3. Preview the layout.
4. Click **Generate PDF** (rendered with Playwright for high fidelity).
5. Download the report.

Reports use clinical typography with evidence stars rendered for print. The Reports UI currently
generates full analysis PDFs; it does not expose single-variant evidence-card export controls from
[variant detail](variant-detail.md) pages. The backend variant-card rendering endpoints are keyed
to reportable findings and also require the Playwright Chromium browser, which the native installer
and Docker image install automatically. Manual environments can run:

```bash
python -m playwright install chromium
```

!!! warning "Reports are not clinical documents"
    A generated report summarises Yeliztli's research-use findings. It is **not** a clinical
    or diagnostic report — see [Intended use & disclaimers](../intended-use.md).
