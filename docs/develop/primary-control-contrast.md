# Proposed colors for primary controls

Use a dedicated foreground, fill and focus color for primary actions in #2525. On the three measured controls the lowest enabled text contrast was **4.86:1 in light mode** and **6.77:1 in dark mode**. The lowest keyboard-focus contrast was **4.24:1** and **7.41:1**, respectively. This proposal is ready for a maintainer palette decision. [Design issue #2708](https://github.com/bioedca/Yeliztli/issues/2708)

| Proposed role | Light | Dark |
| --- | --- | --- |
| Primary action fill | `hsl(168 85% 24%)` · `#09715c` | `hsl(168 75% 42%)` · `#1bbb9b` |
| Primary action text and current-color icons | `#ffffff` | `hsl(222.2 84% 4.9%)` · `#020817` |
| Primary action keyboard ring | `#09715c` | `#1bbb9b` |

Use semantic action tokens in the existing Tailwind theme and its dark override. Keep the existing 90%-opacity fill on hover for controls that already use it. A 2 CSS-pixel outline with a 2 CSS-pixel offset was visible and unobscured on the three keyboard witnesses. The offset allows the surrounding surface to separate the ring from a matching fill. The skip-navigation link has its own box-shadow ring utility and needs its own keyboard check.

## Palette comparison

The values below are the lowest measured ratios across “Open full detail,” “+ Rule” and enabled “Run Query” during rest, hover, pointer press and keyboard focus. Focus-ring contrast is measured against the adjacent background. In Query Builder that background is the tinted rule group. Text and focus ratios use separate criteria.

| Choice | Light text | Dark text | Light ring | Dark ring | Consequence |
| --- | ---: | ---: | ---: | ---: | --- |
| Current white on brand teal | 3.22 | 2.43 | 2.61 | 7.41 | Normal and hovered text fail in both themes. |
| Darken the global fill to `#09715c` / `#117864`, keep white text | 4.86 | 5.39 | 2.61 | 7.41 | Changes every global primary fill and foreground-color consumer, including the header logo's teal. |
| Keep the fill, change the global on-primary text to `#021c17` / `#020817` | 4.85 | 6.77 | 2.61 | 7.41 | Keeps the bright fill and changes the Setup Wizard logo's on-primary color. |
| **Dedicated action pair and focus ring** | **4.86** | **6.77** | **4.24** | **7.41** | Targets actionable controls and protects the existing brand and sidebar palette. |

Changing only the action fill/text reproduced the 2.61:1 light ring on “+ Rule.” A stronger ring is a separate requirement and can accompany any palette. The decision favors white text on the darker light-theme action and dark text on the existing bright dark-theme action. The sidebar defines its own tokens, and its active navigation links currently use `sidebar-accent` and `sidebar-accent-foreground`. Preserve that selection treatment.

## Browser measurements

Each cell reports the effective foreground against the painted fill, including alpha compositing. Hover and pointer-down had the same computed color on all three enabled witnesses. Keyboard focus retained the resting text pair. Ratios are rounded here for reading. Pass/fail used the unrounded values.

| Control and theme | Current rest / hover | Proposed rest or focus | Proposed hover or press | Proposed ring against adjacent surface |
| --- | --- | --- | --- | --- |
| Detail link and Run Query, light | 3.67 / 3.22 | white on `#09715c`, 5.95 | white on `#217f6c`, 4.86 | `#09715c` on white, 5.95 |
| + Rule, light | 3.67 / 3.33 | white on `#09715c`, 5.95 | white on `#1c7b6b`, 5.13 | `#09715c` on `#ccdbf1`, 4.24 |
| Detail link and Run Query, dark | 2.43 / 2.95 | `#020817` on `#1bbb9b`, 8.22 | `#020817` on `#19a98e`, 6.77 | `#1bbb9b` on `#020817`, 8.22 |
| + Rule, dark | 2.43 / 2.89 | `#020817` on `#1bbb9b`, 8.22 | `#020817` on `#19ab91`, 6.93 | `#1bbb9b` on the dark rule group, 7.41 |

The current resting text is white on `#0c977b` in light mode and `#1bbb9b` in dark mode. Its hovered fill over the page or detail panel is `#24a188` / `#19a98e`. Over the rule group it is `#1f9e87` / `#19ab91`. Native disabled “Run Query” has 50% element opacity. Its proposed effective pairs were white on `#84b8ae` (2.22:1, light) and `#020817` on `#0f6259` (2.77:1, dark). It refused focus and a click sent zero query requests. After adding a rule, the same button sent one request. The detail link and “+ Rule” have no disabled state in these flows.

The sampled enabled labels are 12–14 CSS pixels at weight 500. WCAG 2.1 AA requires 4.5:1 for normal text. A visible, author-styled focus cue must convey focus and have sufficient contrast against adjacent colors. The non-text criterion is 3:1. Inactive controls are exempt from these contrast thresholds. [Text contrast](https://www.w3.org/WAI/WCAG21/Understanding/contrast-minimum.html) · [Non-text contrast](https://www.w3.org/WAI/WCAG21/Understanding/non-text-contrast.html) · [Visible keyboard focus](https://www.w3.org/WAI/WCAG21/Understanding/focus-visible.html)

## Migration inventory

At `7be123f382be44cc3c0936d2787038886b6488f7`, a TSX search for `text-primary-foreground` found 50 source lines in 33 files. This includes focus-prefixed classes and opacity variants. After setting aside a logo comment, the Setup Wizard logo invocation and two noninteractive step-indicator clauses, 46 class or conditional lines in 30 files describe action and selection surfaces. Several lines can style one control.

| Group | Files to review during implementation |
| --- | --- |
| Setup actions | CredentialsStep, DatabasesStep, DisclaimerStep, ImportBackupStep, RestoreStep, StorageStep, UploadStep |
| Query actions | QueryBuilderPanel, QueryBuilderView, SavedQueriesPanel, SqlConsole |
| Variant, filter and genome-browser controls | VariantDetailSidePanel, ChromosomeNav, ColumnPresets, FilterPanel, GenomeBrowser, IgvBrowser |
| Settings and individual workflows | ExportBackup, SampleMetadataEditor, UpdateManager, IndividualDetail, MergeWizard, PostMergeRewatchModal |
| Other actions and keyboard navigation | AbsoluteRiskOverlay, AncestryView, AnnotationPanel, Login, NotFound, ReportBuilder, SkipNav |

Migrate by role. Chromosome and consequence/ClinVar choices use selected styles. Preserve their selected state and accessible names. ReportBuilder independently changes fill and text opacity when disabled. SkipNav applies the pair only on focus. Test these three patterns alongside the normal actions. Keep decorative logos on the brand palette. Audit the small numbered/current step and completed icon in WizardStepper as informational states with their own contrast requirements. Search standalone primary text, icons, muted fills, switches and sidebar consumers before replacing any global token. The literal search above measures the on-primary class, and the browser study covers the named witnesses.

## Reproduction and removal of test allowances

1. Use a local app and synthetic fixtures on the recorded revision. The seeded Variant Explorer fixture in `tests/e2e/wcag-audit.spec.ts` opens the `rs2000` detail dialog. The Query Builder fixture in `tests/e2e/query-builder-multi-value.spec.ts` adds a “between” rule and fills both bounds. Also test the empty rule set for native disabled “Run Query.”
2. In a reduced-motion browser at 1440 × 1000 CSS pixels, let the detail dialog finish its slide and settle each theme. For a prototype, override the fill, foreground and ring only on the action host. Move the pointer away for rest, hover, hold the primary mouse button for press, and Tab onto the control for keyboard focus. Read the computed foreground, fill, opacity and ancestor backgrounds. Composite each layer to sRGB and apply WCAG relative luminance. Check the actual ring, offset and backdrop in a cropped screenshot. The dark rule-group screenshot differed from the computed backdrop by one red-channel unit. Both give 7.41:1 to two decimal places.
3. In Chromium 156, axe-core 4.11.1 reported the named contrast failure in each theme on each unmodified witness (the detail link and “+ Rule”). With the proposed action styles those same nodes appeared in the **passes** collection, with zero violations in the dialog or builder panel. Existing baseline tests passed in Chromium, Firefox and WebKit with Playwright 1.64.1. The candidate measurements and injected-style axe scans used Chromium. WebKit used the repository-pinned browser on Ubuntu Noble libraries.
4. In #2525, remove the `isKnownPrimaryContrast` exception **and** its positive failure expectation from the seeded detail audit. Remove `KNOWN_PRIMARY_TOKEN_CONTRAST` and its expected target in the Query Builder audit. Assert the named nodes are visible, included in the contrast rule's passes and absent from its incomplete/violation nodes, in both themes. Keep the “between” labels, watch-list assertions and unrelated component exclusions. Exercise keyboard focus and hover and pointer press separately. A resting axe scan can miss them.
5. Run the two affected specs in all three browsers against the implemented styles. Include the remaining inventory patterns in focused checks and verify logo and sidebar appearance during that change. The implementation and removal of the two exceptions belong to [#2525](https://github.com/bioedca/Yeliztli/issues/2525) after the palette decision.
