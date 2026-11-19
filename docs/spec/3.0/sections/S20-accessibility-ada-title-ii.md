# S20 — Accessibility / ADA Title II (WCAG 2.1 AA)

**Status:** Build spec for CivicCast 3.0 · Authored 2026-06-14 · **Added from pre-build validation** (this was entirely absent from the spec — a critical, legally-deadlined gap)
**Scope:** Public portal (web) + OTT apps accessibility to **WCAG 2.1 Level AA**
**Legal basis:** DOJ 2024 final rule under **ADA Title II (28 CFR Part 35)** requiring state/local government web + mobile to meet **WCAG 2.1 AA**. Compliance deadlines fall in **2026–2027 depending on the governing entity's population size** (larger entities first). A PEG / government-access operator is in scope.
**Key claim boundary:** CivicCast provides accessible software + audit proof; it does **not** certify the operating *entity's* overall ADA compliance (content + process are the station's responsibility). Never claim "ADA compliant" as a guarantee — claim "ships WCAG 2.1 AA-tested surfaces."

---

## 1. Why this exists / parity + legal rationale

The DOJ's 2024 ADA Title II rule makes **WCAG 2.1 AA the technical standard** for state/local government websites and mobile apps. PEG/government-access stations (and the municipalities they serve) are squarely in scope, and the public portal is exactly the kind of surface the rule targets (video, search, meeting archives). This is **not** a PEG automation item — it's a hard legal requirement that the spec previously ignored. Shipping a portal that fails WCAG 2.1 AA in 2026 exposes the operating municipality to complaints; we must ship accessible by default.

It also reinforces two existing gaps: **captions** (S11) and **descriptive/secondary audio** (S24/S11) are accessibility requirements, not just coverage features.

---

## 2. Scope & deadlines

| Surface | Standard | Deadline (entity-size dependent) |
|---|---|---|
| **Public portal** (VOD player, search, show/series pages, meeting + **agenda** pages [S25], login/recovery) | WCAG 2.1 AA | 2026 (large) / 2027 (small) |
| **OTT apps** (Roku/Apple TV/Fire TV/Android TV/mobile — S12) | WCAG 2.1 AA *as applicable to the platform*, plus each platform's native a11y API (VoiceOver, TalkBack, Roku audio guide) | 2027 |
| **Operator console** (staff UI) | Best-effort AA (not strictly Title-II-scoped, but adopt the same components) | — |

---

## 3. Requirements (WCAG 2.1 AA, the load-bearing subset)

- **Captions** on all VOD and live video (delivered by S11; the player must render them and expose a visible toggle). *WCAG 1.2.2 / 1.2.4.*
- **Audio description / secondary audio** available where provided (S24/S11 SAP/descriptive tracks); player exposes track selection. *WCAG 1.2.3/1.2.5 (AA).*
- **Keyboard operability:** every interactive element (player controls, search, facets, agenda jump-points, pagination, login) fully operable by keyboard; no traps; logical tab order. *WCAG 2.1.1/2.1.2.*
- **Visible focus** indicator on all focusable elements. *WCAG 2.4.7.*
- **Screen-reader semantics:** semantic HTML + ARIA labels/roles on all controls, images (alt text), form fields, and the video player; meaningful page titles + heading structure. *WCAG 1.1.1/1.3.1/4.1.2.*
- **Color contrast** ≥ 4.5:1 (normal text), ≥ 3:1 (large text + UI components/graphics). *WCAG 1.4.3/1.4.11.* (Note: this constrains the **operator-uploaded branding/theme colors** in S12 — the portal must validate/auto-adjust contrast or warn the operator.)
- **Reflow / resize:** usable at 320 px width and 200% zoom without loss of content. *WCAG 1.4.10/1.4.4.*
- **No keyboard/seizure hazards:** no flashing > 3×/s (relevant to CG/bulletin overlays surfaced in the portal). *WCAG 2.3.1.*

---

## 4. Entities / data model

No new persistent entities required. One addition to the S12 branding flow: when an operator sets theme colors, the portal **validates contrast** and either auto-adjusts or blocks-with-warning (a derived check, not stored state). Accessibility is a property of the rendered surfaces, enforced by component choices + tests, not a table.

---

## 5. Behavior / implementation approach

- Build the portal on **accessible component primitives** (semantic HTML first; ARIA only where needed); the video player must be a known-accessible player (captions, keyboard, ARIA) — if the current portal player isn't AA-capable, that's a remediation task.
- Branding/theme contrast is validated at config time (S12 "drop your logo / pick a color" → contrast gate).
- OTT apps implement each platform's native accessibility API (VoiceOver/TalkBack/Roku audio guide) — tracked under S12's per-codebase work.

---

## 6. Proof tier + testable DONE-criteria

| # | Done-criterion (testable) | Proof rung |
|---|---|---|
| DC-1 | **axe-core** automated scan of every public-portal page type returns **0 WCAG 2.1 AA violations**; Lighthouse a11y score ≥ 95. | rung 1 (lab) |
| DC-2 | **Keyboard-only** walkthrough completes every portal task (browse, search, play w/ captions, open agenda item, log in) with no trap and visible focus throughout. | rung 1 (lab) |
| DC-3 | **Screen-reader** walkthrough (NVDA or VoiceOver) reaches and correctly labels every control + the video player; captions toggle is announced. | rung 2 (machine — in the soak/walkthrough) |
| DC-4 | **Contrast gate**: an operator-chosen theme color below 4.5:1 is rejected or auto-adjusted with a warning (test both paths). | rung 1 |
| DC-5 | Player renders S11 captions + exposes caption + audio-track toggles; captions present on both VOD and live. | rung 1→2 |
| DC-6 | The release audit (S3/CI) **fails** if axe reports any AA violation on the portal (a11y is a release gate, not a checkbox). | rung 1 |
| DC-7 | Honesty guard: no surface/string claims "ADA compliant" as a guarantee; copy says "WCAG 2.1 AA-tested." | contract |

Proof tier: **contract → lab → machine** (the screen-reader/keyboard walkthrough rides in the stage `/walkthrough`).

---

## 7. Commissioning + ongoing (S3 integration)

- Commissioning wizard (S3) runs a **portal accessibility audit** at setup and shows the result; flags any operator theme that fails contrast.
- Every release from here forward includes the axe + keyboard + screen-reader checks in the audit pass (this is why it's a release gate, DC-6).

## 8. Test plan
- Automated: axe-core + Lighthouse CI on all portal page types (gating).
- Manual (in `/walkthrough` at stage close): NVDA/VoiceOver pass + keyboard-only pass, logged.
- Unit: contrast-gate logic on branding colors.
- Coverage + audit: 0/0/0/0/0.

## 9. Dependencies & cross-references
- **S11** (captions/loudness) + **S24** (SAP/descriptive audio): the media-track requirements a11y depends on.
- **S12** (OTT + branding): per-platform native a11y APIs; the branding-color contrast gate lives here.
- **S26** (agenda): agenda jump-points must be keyboard/screen-reader accessible.
- **S3** (commissioning): the accessibility audit step.
- **master §5** (proof ladder): add the a11y rung-1/rung-2 criteria above.

## 10. DONE when
DC-1…DC-7 pass; a11y is a CI release gate; commissioning audit step live; master §5 + §11 index reference S20; audit 0/0/0/0/0.

Estimated implementation effort: **~1.5–2 engineer-weeks** (portal remediation + accessible player + contrast gate + axe/keyboard/SR test harness); OTT-app a11y folds into the per-codebase S12 work.
