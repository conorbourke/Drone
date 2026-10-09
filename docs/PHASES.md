# Phases

Build one phase at a time. A phase is done when its acceptance criteria pass in the browser.

| # | Phase | Status | Done when |
|---|---|---|---|
| 1 | Foundations: hosting, login, project and version storage, parts database structure, deployment pipeline | **Done.** Deployed at https://conor-vtol-designer.fly.dev and accepted by the owner (9 Oct 2026) | The owner opens a URL, logs in, creates a project and saves two versions. |
| 2 | Inputs and design: image upload, mission form, Claude image reading, parametric model for all three layouts, 3D view and 2D drawings with handles, Tier 1 instant estimates, version comparison | **Built and deployed** to https://conor-vtol-designer.fly.dev. Contract in `docs/phases/PHASE2.md` | Uploaded images produce a sensible starting model, edits update the model and estimates instantly, two versions compare side by side. |
| 3 | Full analysis and assistant: AVL, XFOIL, drag, propulsion, battery and transition models, checks, ranked recommendations, scale-to-weight, validation suite, Claude assistant | **Built and deployed.** As-built notes in `docs/phases/PHASE3.md` | Validation suite passes with a report in the app; Analyse returns results, checks and ranked recommendations; the assistant explains them correctly. |
| 4 | Parts and suppliers: real components, engine-driven recommendations, Irish/UK supplier lookup, cost and weight totals | **Built and deployed.** As-built notes in `docs/phases/PHASE4.md` | A complete prototype parts list with working supplier links and a total against the €5,000 budget. |
| 5 | Files: split STL/3MF, STEP, PDF/DXF drawings, bill of materials | **Built and deployed** (9 Oct 2026). Awaiting the owner's check. As-built notes and known limits in `docs/phases/PHASE5.md` | Every printed part fits the 240 mm envelope and opens in Bambu Studio; STEP files open in a free CAD viewer. |
| 6 | Flight data: log upload, phase detection, predicted-vs-actual, calibration factors, built weights | **Built and deployed** (9 Oct 2026). Awaiting the owner's check. As-built notes and known limits in `docs/phases/PHASE6.md` | A sample ArduPilot log is parsed and compared against its design's predictions. |
| 7 | Moulds and full scale | **Built and deployed** (9 Oct 2026). Awaiting the owner's check. As-built notes and known limits in `docs/phases/PHASE7.md` | Mould files are generated for the nose, fuselage and wing-root fairings of a test design. |

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

## Phase 5 checklist (Files)

What was built:

- [x] A CadQuery model of the aircraft from the design parameters, agreeing with the engine's geometry to within 0.2 mm.
- [x] Every printed part split to fit the printer envelope (240 mm by default), with no cut near the wing root, alignment pins and sockets, glue grooves, and spar and boom channels that run straight through each joint.
- [x] Files per design: STL per piece, 3MF per part plus one 3MF with every piece on bed-sized plates, STEP per part and for the assembly, a 4-sheet A3 PDF of dimensioned drawings, DXF outlines of the flat carbon plates, a bill of materials (CSV) and printing notes.
- [x] Files are made on the server in the background, can be downloaded one by one or as one ZIP, and identical requests reuse earlier files.
- [x] The *Files* tab: generate for the draft or a version, progress, a fit tick per piece, a 3D preview of each piece on the printer bed, and which free program opens each file type.

How the owner checks it:

1. Open a project, go to *Files*, press *Generate files* (a minute or two for the default design).
2. Every piece shows a green fit tick against the 240 mm envelope.
3. Download *all_pieces.3mf* and open it in Bambu Studio: the pieces sit on plates.
4. Download a STEP file and open it in FreeCAD or an online STEP viewer.

## Phase 6 checklist (Flight data)

What was built:

- [x] A "Set up logging" card with the ArduPilot parameters to set and how to get the log off the aircraft.
- [x] Upload of ArduPilot DataFlash logs (`.bin`, or `.log` text logs) up to 200 MB, read on the server in the background.
- [x] Flight phases found automatically (take-off hover, transition, cruise, back-transition, landing hover) with per-phase power, energy, speeds, attitude and vibration, and charts.
- [x] Predicted against measured per phase, with the error in percent and likely causes when a measurement is outside the predicted range.
- [x] Calibration factors (hover power, cruise drag, battery usable energy, structural mass) combined over every log, with a preview of what they change; *Apply* and *Undo*. Full analyses run after applying say "Calibrated with N flights".
- [x] Built weights: enter the weighed mass of each component; the structure items give the structural mass factor.
- [x] A real ArduPilot simulator flight (ArduPlane SITL QuadPlane) bundled as a sample: *Load sample flight*.

How the owner checks it:

1. Open a project, go to *Flight data*, press *Load sample flight*.
2. When it has been read, the timeline shows the phases and the *Predicted against measured* table fills in.
3. In *Calibration*, look at the preview and press *Apply*; run *Analyse* on the Design tab and see "Calibrated with 1 flight". The sample is a different, simulated 4.5 kg aircraft, so press *Undo* in Calibration before relying on the numbers (deleting the log alone does not remove factors already applied).

## Phase 7 checklist (Moulds and full scale)

What was built:

- [x] Two-part female moulds for the nose bay shell, the fuselage shell and the wing-root fairing: computed parting line, draft check (2° minimum by default) that reports faces and never changes the part, 6 mm walls, a 25 mm flange with M5 bolt holes at 60 mm pitch, registration cones, a trim line 5 mm outside the part edge, optional vent channel.
- [x] Moulds cut into tiles that fit the printer, with bolting ribs and alignment keys at each joint, print orientation and notes per tile; STL and 3MF per tile, STEP per mould half and an A3 PDF sheet per part.
- [x] Full-scale (24 kg) checks: the Phase 3 checks plus the 23/24/25 kg mass thresholds, motor-out hover with an octocopter (coaxial X8) recommendation when a quad cannot hold attitude, spar and boom tubes from the catalogue, landing gear load, battery current at 24 kg hover, a composite layup per part with its mass, and the A3 operating note beside range and endurance.
- [x] The *Full scale* tab with the checks and a *Moulds* section (generate, progress, tiles with a bed preview, draft report, downloads and ZIP).

How the owner checks it:

1. Scale a design to 24 kg with *Scale to weight* on the Design tab and set the mission scale to final.
2. Open *Full scale*: the checks list shows the mass thresholds, motor-out and the other 24 kg checks.
3. In *Moulds for the carbon parts* keep all three parts ticked and press *Generate moulds*. On the server this takes several minutes.
4. Every tile shows a fit tick; the draft report lists any flagged faces; the ZIP downloads.

Known limits of Phases 5 to 7 are listed in each phase file under "As built".

Open decisions for the owner are in `docs/DECISIONS.md`.
