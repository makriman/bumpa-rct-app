# Bumpa Bestie brand design QA

## Source truth

- Reference: `/var/folders/02/mtc26_qx5rv99vydlmj80tfr0000gn/T/codex-clipboard-68c75fcb-77b3-470b-8593-71d200f8025a.png`
- Intent: use the supplied Bumpa mark as a cue for simplicity, compact legibility, green brand recognition, and a sturdy wordmark—not as an exact logo target.
- Product system: existing forest, lime, paper, Inter, and Newsreader tokens in `apps/web/app/globals.css`.

## Implementation evidence

- Desktop landing page, 1280 × 720: `artifacts/design-qa/brand-home-1280x720.png`
- Mobile landing page, 412 × 915: `artifacts/design-qa/brand-home-mobile-412x915.png`
- Desktop sign-in page, 1280 × 720: `artifacts/design-qa/brand-login-1280x720.png`
- Social card, 1200 × 630: `apps/web/public/brand/social-card.png`
- Browser state: local development build, public landing and temporary sign-in routes.

## Comparison and QA history

1. Compared the supplied reference and generated social card together. The implementation preserves the reference's small-size clarity and green/neutral balance while introducing an original two-conversation partnership mark and a warmer Bestie wordmark.
2. Checked desktop landing hierarchy, spacing, icon alignment, typography, chat preview, focus naming, and absence of horizontal overflow.
3. Checked the 412 px mobile landing page. Header, display type, actions, trust points, and chat preview remain ordered and unclipped; document scroll width equals viewport width.
4. Checked the sign-in flow and searchable country-code picker. Flags, calling codes, selected state, accessible labels, keyboard dismissal, and focus styling work.
5. Found the first dark-panel wordmark used light-surface colours and failed visual contrast. Added a scoped dark-surface mark treatment and re-captured the sign-in evidence.
6. Found a CSP nonce hydration warning on the structured-data script. Added the React hydration exception for the browser-hidden nonce and confirmed fresh landing and sign-in tabs have no console errors.
7. Verified the SVG, 16/32/48 px ICO frames, Apple icon, PWA icons, maskable icon, and social card are generated reproducibly from the canonical vector source.

## Final result

Passed. The identity is distinct, coherent with the existing product, legible across favicon through social-card sizes, responsive on desktop/mobile, and consistent on light and forest surfaces. Automated accessibility and browser regression results are recorded with the release verification.
