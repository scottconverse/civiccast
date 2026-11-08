# CivicCast project site (GitHub Pages)

The site is plain static HTML and CSS served by GitHub Pages from `main` `/docs`:
<https://scottconverse.github.io/civiccast/>. There is no build step and no framework.

| File | What it is |
| --- | --- |
| `docs/index.html` | The landing page (single page, anchors: product, how, screens, architecture, evidence, start, docs) |
| `docs/install-windows.html` | Release status and install notes |
| `docs/assets/web/site.css` | The design system: brand tokens, type scale, layout, components |
| `docs/assets/web/fonts/` | Barlow and Barlow Condensed as WOFF2 (SIL OFL; originals in `docs/assets/fonts/barlow/`) |
| `docs/assets/web/img/` | Screenshots (WebP, from `docs/manual/images/`) and the architecture figure (from the manual) |
| `docs/brand/` | Logo, favicon and color tokens (see `docs/brand/BRAND.md`) |

Preview locally: `python -m http.server 8123 --bind 127.0.0.1` from the `docs` folder, then open <http://127.0.0.1:8123/>.

Keep it honest when you edit: every figure on the page is taken from `docs/releases/v1.0.0-beta.10-verification.md`
and the User Manual (Appendix H). When a release changes, update the release tag links, the download button, the
evidence table and the "Known limits" list together. Screenshots must show sample content only.
