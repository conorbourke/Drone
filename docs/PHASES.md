# Phases

Build one phase at a time. A phase is done when its acceptance criteria pass in the browser.

| # | Phase | Status | Done when |
|---|---|---|---|
| 1 | Foundations: hosting, login, project and version storage, parts database structure, deployment pipeline | **Built, awaiting the owner's first deploy** | The owner opens a URL, logs in, creates a project and saves two versions. |
| 2 | Inputs and design: image upload, mission form, Claude image reading, parametric model for all three layouts, 3D view and 2D drawings with handles, Tier 1 instant estimates, version comparison | Not started | Uploaded images produce a sensible starting model, edits update the model and estimates instantly, two versions compare side by side. |
| 3 | Full analysis and assistant: AVL, XFOIL, drag, propulsion, battery and transition models, checks, ranked recommendations, scale-to-weight, validation suite, Claude assistant | Not started | Validation suite passes with a report in the app; Analyse returns results, checks and ranked recommendations; the assistant explains them correctly. |
| 4 | Parts and suppliers: real components, engine-driven recommendations, Irish/UK supplier lookup, cost and weight totals | Not started | A complete prototype parts list with working supplier links and a total against the €5,000 budget. |
| 5 | Files: split STL/3MF, STEP, PDF/DXF drawings, bill of materials | Not started | Every printed part fits the 240 mm envelope and opens in Bambu Studio; STEP files open in a free CAD viewer. |
| 6 | Flight data: log upload, phase detection, predicted-vs-actual, calibration factors, built weights | Not started | A sample ArduPilot log is parsed and compared against its design's predictions. |
| 7 | Moulds and full scale | Not started | Mould files are generated for the nose, fuselage and wing-root fairings of a test design. |

## Phase 1 checklist

What was built:

- [x] Fly.io hosting definition and a GitHub Actions deploy that needs no terminal (`fly.toml`, `.github/workflows/deploy.yml`, `docs/DEPLOYMENT.md`).
- [x] Single-password login with signed, HTTP-only session cookies, rate limiting and a logout that works everywhere when the password changes.
- [x] Projects with a live draft, and versions you can save, name, duplicate, restore and delete. Version numbers are never reused.
- [x] Design parameters and mission inputs stored as versioned documents with plain-language explanations served from the API, all metric.
- [x] Parts database structure: eleven categories with the specification fields the engine will need, supplier listings with "last checked" dates, and a seed of clearly labelled example rows.
- [x] Settings: printer envelope (default Bambu Lab P2S, 240 mm usable), take-off weight limits (23 kg warning, 24 kg design limit, 25 kg legal limit) and the proposed check thresholds, each with its source.
- [x] Daily in-app backups downloadable in the browser, plus Fly volume snapshots.
- [x] Automated checks: backend unit tests, frontend type and lint checks, a Playwright test that performs the acceptance criterion end to end, and a container build with a smoke test.

How the owner checks it (after the first deploy):

1. Open the app address. You are sent to the login page.
2. Log in with `APP_PASSWORD`.
3. Press *New project*, give it a name, open it.
4. On the *Inputs* tab change the target take-off weight; the status shows "Saved".
5. In the *Versions* panel press *Save version*, name it "v1".
6. On the *Design* tab change the wingspan; save a version named "v2".
7. Both versions are listed as v1 and v2. *Restore* v1 and the wingspan goes back.

Open decisions for the owner are in `docs/DECISIONS.md`.
