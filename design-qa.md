# Login country-code control design QA

- Source visual truth: `/var/folders/02/mtc26_qx5rv99vydlmj80tfr0000gn/T/codex-clipboard-c7aac920-9040-400d-91fc-7055faa1108d.png`
- Implementation screenshot: `/tmp/bumpabestie-design-qa/implementation-login.png`
- Open-menu screenshot: `docs/design-evidence/login-country-picker-open.png`
- Full-view comparison: `/tmp/bumpabestie-design-qa/source-vs-implementation.png`
- Focused phone-control comparison:
  `docs/design-evidence/login-phone-control-comparison.png`
- Desktop comparison viewport: 1025 × 799 after removing the source browser chrome
- Mobile verification viewport: 390 × 844
- State: temporary web-access login, default UK country code; closed and open picker states

## Comparison history

### Initial finding

- **P1 — The country selector dominated and duplicated the phone field.** The
  source used a wide country-name select followed by a second visible `+44`,
  producing two competing controls and consuming most of the available width.
  On smaller screens it also forced the phone entry into a two-row interaction.

### Fix applied

- Replaced the separate region select and repeated dial-code prefix with one
  50px-high phone control. Its compact leading trigger contains a real flag
  asset, the selected calling code, and a library-provided caret; the national
  number remains the dominant input.
- Added a searchable, scroll-contained country list with real flag assets,
  selected and active states, click-outside dismissal, and keyboard support for
  Arrow Up/Down, Home/End, Enter, and Escape.
- Kept the existing typography, palette, radii, validation behavior, phone
  normalization, and approved-number boundary unchanged.

### Post-fix evidence

- The full-view comparison shows the same screen hierarchy and brand system
  with a materially simpler phone-entry region.
- The focused comparison confirms that the country name and repeated dial code
  are gone; flag and code now occupy 106px on desktop and 100px on mobile.
- The 390 × 844 open-menu capture stays within the viewport (`19px` left,
  `339px` right) with no horizontal overflow.
- Browser-rendered desktop and mobile interactions passed with zero console
  warnings or errors.
- All 32 Playwright journeys passed in desktop and mobile Chromium, including
  Axe WCAG A/AA checks, country normalization, login flow, CSP, and visual
  baselines. All 162 component/unit tests also passed.

### Accessibility iteration

- **P1 — The first open-menu Axe pass found the 10.56px “Countries” label at a
  4.14:1 contrast ratio.** The label was moved from the lighter secondary gray
  to the established `--ink-soft` token, taking it safely above the WCAG AA
  threshold without changing the visual hierarchy. The desktop and mobile
  open-menu Axe checks then passed.

## Required fidelity surfaces

- **Fonts and typography:** Existing Inter/Newsreader hierarchy, weights,
  wrapping, line heights, and antialiasing are unchanged. The calling code uses
  the existing compact UI weight and tabular numerals.
- **Spacing and layout rhythm:** The two-column control was consolidated into a
  single aligned field. Existing 12px field radius and 50px height are retained;
  the trigger, divider, input, help copy, and submit button share one clear
  vertical rhythm.
- **Colors and visual tokens:** The implementation uses the established paper,
  line, forest, mint, ink, and focus tokens. Hover, expanded, active, selected,
  focus-visible, and error states remain semantically distinct.
- **Image quality and asset fidelity:** Country flags come from the pinned MIT
  `flag-icons` asset set and render at their native 3:2 ratio. Caret and search
  icons come from the pinned Phosphor icon library. No emoji, CSS-drawn flags,
  placeholder art, or handwritten SVG was introduced.
- **Copy and content:** The help text now matches the combined interaction:
  “Choose the country code, then enter the mobile number. A leading zero is
  fine.” All security and temporary-access copy remains unchanged.

## Findings

- No actionable P0, P1, or P2 findings remain.
- No P3 follow-up is required for this scoped control.

## Primary interactions verified

- Open and close the picker with pointer input.
- Search by country name or calling code.
- Select India with the keyboard and update the flag, `+91`, and example number.
- Preserve UK and India E.164 normalization through the real login journey.
- Render the closed and open states without desktop or mobile overflow.
- Complete automated WCAG A/AA checks with no reported violations.

## Final result

final result: passed
