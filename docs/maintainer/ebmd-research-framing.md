# Proposed placement of the eBMD research disclaimer

For #2419, display the existing response’s `context.disclaimer` as the **first paragraph of the shared eBMD framing panel**, before the explanation of the score and before the results branch. Keep the supported API fields. In the current response the exact sentence is “Research Use Only. Discuss bone health, DXA, and FRAX with a clinician.” The same paragraph belongs on a finished bring-your-own-score page and on a page with a score. This presentation decision is pending in [#2712](https://github.com/bioedca/Yeliztli/issues/2712).

## Source and existing screens

`GET /api/analysis/ebmd/prs` returns `context`, `research_use_only`, availability, scoring state and a possible score. The API reuses `EBMD_CONTEXT` from `backend/analysis/ebmd_prs.py`. At `7be123f382be44cc3c0936d2787038886b6488f7`, the existing amber `ebmd-context` panel displays the substitute, direction and utility fields. It drops the disclaimer. The default bring-your-own branch shows setup guidance and has no research-use badge. A presentable score uses `PRSGaugeCard`, whose badge is rendered by the shared card even when an artificial response sets the page flag false.

The FH page provides a precedent for placement. Its shared `fh-criteria` block renders the API disclaimer ahead of its result branches. FH has its own wording. The eBMD change should consume its own response and preserve its established wording, direction text and source links.

## Display contract

- Render a nonempty disclaimer from the current response verbatim, once in the common panel. Place the existing information icon alongside the framing and hide that decorative icon from assistive technology. Use ordinary readable text and a named region. Static framing needs no alert or live announcement.
- When `research_use_only` is true, give the region the accessible name “Research use information.” If the disclaimer is missing or empty, show the existing short label “Research Use Only” there as a fallback. The current server supplies both fields. The fallback covers an incomplete response without inventing clinician advice.
- When the flag is false or absent, use a neutral region name such as “About this score.” Continue to display any supplied disclaimer. With an empty disclaimer, omit that paragraph and keep the other supplied framing. Preserve the meaning of a contradictory response by retaining its authored text. A missing or false flag supplies no clinical clearance.
- Keep the shared score card’s existing local badge and score provenance. That badge identifies the card. The common panel carries the page-level explanation even when the card is absent. No new visible page-level badge is needed beside the full sentence. Do not change other modules’ shared cards for this decision.

| Page state | What the user should see |
| --- | --- |
| No selected sample | Select-a-sample prompt. No sample-bound response or disclaimer. |
| First GET pending, or failed with no cached response | Existing loading status or error and retry behavior. Show response-supplied wording when a response arrives. |
| Current response with no installed score (unavailable, no PRS, and not withheld) | Common disclaimer followed by the correct bring-your-own guidance. |
| Never scored, or rescore in progress | Common disclaimer with the pending state or scoring status. A missing score alone does not establish that the package is absent. |
| Withheld | Common disclaimer and the existing withheld message. Do not show score values, a reassuring negative result or setup guidance. |
| Presentable installed score | Common disclaimer ahead of the score section, plus the card’s local badge and provenance. |
| Stale score refreshing, or a cached response with a failed recheck | Retain the framing with the cached result from that sample. Preserve scoring/warning status. A sample switch must not show the previous sample’s disclosure or result. |

Rendering the sentence only on the score card leaves the default and withheld states uncovered. Removing both fields would change the public response and remove the backend-owned source for this wording. Keeping an unused flag while duplicating the sentence in frontend code would let the two sources diverge. The common panel already exists in every successful response branch.

## Browser and regression evidence

The comparison used the running React routes and synthetic HTTP responses. A browser-only preview added the proposed paragraph and accessible region name. The current eBMD page omitted the canonical sentence in the BYO, installed, withheld, never-scored and stale states. The existing FH route displayed its own exact API disclaimer. On a forced failed eBMD recheck the stored score and its framing remained visible. With no initial response, only the loading or error state was present.

Nine loaded-response variants exercised normal, false-flag and missing-copy states. The preview exposed supplied text in Chromium's accessibility tree. Tab from the Re-score control skipped the static panel, which added no focusable element. Scoped axe checks in the BYO, installed and withheld panels found zero violations in light and dark themes, and the added paragraph appeared in each contrast rule’s passing nodes. At a 375 CSS-pixel viewport the panel's text wrapped within its measured width. The existing direction regression passed in Chromium, Firefox and WebKit. The preview and axe observations were made in Chromium. These are presentation observations, with no new scientific wording or changes to patient results.

For #2419, assert the returned field is nonempty on the production GET and passes unchanged to the common panel. In the page and browser fixtures, give the response a distinctive disclaimer and assert its exact text and position in both BYO and installed views. Assert absent score content in the withheld and never-scored branches, and check the pending, cold-error and cached-error paths. Exercise true/false flags, nonempty/empty text, keyboard traversal, both color themes and a narrow viewport. Preserve the existing direction assertions and local badge. Test that a changed response string reaches the screen without a frontend copy change.
