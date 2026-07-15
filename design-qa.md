# Bumpa Bestie brand design QA

## Source truth

- Reference: user-supplied Bumpa logo attachment (not committed to preserve source
  ownership and avoid a non-portable workstation path).
- Intent: use the supplied Bumpa mark as a cue for simplicity, compact legibility, green brand recognition, and a sturdy wordmark—not as an exact logo target.
- Product system: existing forest, lime, paper, Inter, and Newsreader tokens in `apps/web/app/globals.css`.

## Implementation evidence

- Desktop landing page, 1280 × 720: `artifacts/design-qa/brand-home-1280x720.png`
- Mobile landing page, 412 × 915: `artifacts/design-qa/brand-home-mobile-412x915.png`
- Desktop sign-in page, 1280 × 720: `artifacts/design-qa/brand-login-1280x720.png`
- Social card, 1200 × 630: `apps/web/public/brand/social-card.png`
- Sanitized live DOM/console/hash transcript:
  `artifacts/design-qa/live-browser-c0c1544.json`
- Browser state: exact-release local captures plus the live public landing and
  temporary sign-in routes at `bumpabestie.com`.

## Comparison and QA history

1. Compared the supplied reference and generated social card together. The implementation preserves the reference's small-size clarity and green/neutral balance while introducing an original two-conversation partnership mark and a warmer Bestie wordmark.
2. Checked desktop landing hierarchy, spacing, icon alignment, typography, chat preview, focus naming, and absence of horizontal overflow.
3. Checked the 412 px mobile landing page. Header, display type, actions, trust points, and chat preview remain ordered and unclipped; document scroll width equals viewport width.
4. Checked the sign-in flow and searchable country-code picker. Flags, calling codes, selected state, accessible labels, keyboard dismissal, and focus styling work.
5. Found the first dark-panel wordmark used light-surface colours and failed visual contrast. Added a scoped dark-surface mark treatment and re-captured the sign-in evidence.
6. Found a CSP nonce hydration warning on the structured-data script. Added the React hydration exception for the browser-hidden nonce and confirmed fresh landing and sign-in tabs have no console errors.
7. Verified the SVG, 16/32/48 px ICO frames, Apple icon, PWA icons, maskable icon, and social card are generated reproducibly from the canonical vector source.
8. Rechecked the live release in the selected in-app browser at 1280 × 720 and
   390 × 844. The homepage and sign-in page had no horizontal overflow, the
   brand link and primary actions remained present, and the browser console had no
   warnings or errors.
9. Opened the live country-code control and verified the searchable accessible
   listbox contains 245 rendered options. The India entry uses the real
   `flag-icons` asset class with `+91`; the selected United Kingdom control uses
   `+44`. The mobile selector, telephone field and submit action remain visible and
   usable without widening the viewport. Live screenshot capture was not retained;
   the exact-release local visual captures and the sanitized live observation
   transcript form the reviewable evidence pair.

## Final result

Passed. The identity is distinct, coherent with the existing product, legible
across favicon through social-card sizes, responsive on desktop/mobile, and
consistent on light and forest surfaces. Exact live assets match the reviewed
source hashes; automated accessibility, browser regression and live DOM/console
results are recorded with the release verification.
