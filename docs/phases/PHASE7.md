# Phase 7 contract: Moulds and full scale

Read `docs/BRIEF.md` ("Files" moulds line, "Scale", "Rules"), `docs/phases/PHASE5.md` (CAD kernel, splitting, exports) and `docs/phases/PHASE3.md` (scale-to-weight, checks). This file adds to them.

**Acceptance (from the brief):** mould files are generated for the nose, fuselage and wing-root fairings of a test design.

## 1. Moulds

- `backend/app/cad/moulds.py`: for curved carbon parts (nose bay shell, fuselage shell halves, wing-root fairings, optionally wing skins and tail surfaces), generate two-part (or multi-part where the shape needs it) female moulds from the part's outer surface: parting line chosen at the maximum silhouette in the demoulding direction (computed, shown), draft angle check (default minimum 2°, configurable; faces below it are reported and coloured, and the part surface is not silently altered), mould wall thickness (default 6 mm printed), a flange around the parting line (default 25 mm wide) with registration keys (cones and sockets) and bolt holes (M5 at 60 mm pitch), resin/vent channels optional, a trim line 5 mm outside the net edge, and the laminate allowance stated (moulds are for the outer mould line; laminate grows inward).
- Tiling: moulds larger than the printer envelope are split into tiles that fit, with tile-to-tile alignment keys and bolting flanges on the back, joints placed away from sharp curvature; each tile labelled and given print orientation and notes (PETG/ASA, 100 % perimeters at the mould face, sanding and sealing/primer guidance, release agent).
- Exports as Phase 5: STL/3MF per tile, STEP per mould half, a PDF sheet with the parting line, draft report, tile layout and assembly order.

## 2. Full scale (24 kg)

- The "final" mission scale uses the carbon-composite mass model, Li-ion packs, 12S–14S propulsion and the Phase 4 large-motor candidates; scale-to-weight from the prototype to 24 kg produces a complete design that passes every check (or shows exactly which checks fail and why).
- Full 24 kg design checks: everything from Phase 3 plus MTOW warnings against the 23/24/25 kg thresholds from settings on every screen showing mass, motor-out hover capability check for the quad (with one motor failed, can the remaining three hold attitude and descend; report the yaw/roll authority margin and recommend an octo/coaxial layout if not), structural checks for the larger spar and booms with the carbon tube candidates, landing gear loads, battery current at 24 kg hover, and the A3 operating note next to every range/endurance figure.
- Composite layup suggestion per part (plies of carbon fabric by weight and orientation, core material where used) feeding the structural mass of the final-scale mass model, with sources.

## 3. Tests

- pytest: moulds generated for nose, fuselage and wing-root fairings of the default design scaled to final; every tile fits the envelope; the parting line splits each part into demouldable halves (no undercut along the pull direction beyond tolerance, sampled by ray casting); draft report flags a deliberately zero-draft test surface; keys and flanges present; exports valid. Full-scale: a 24 kg design runs the full analysis and checks; motor-out check gives a sensible result.
- Playwright `e2e/tests/phase7.spec.ts`: generate moulds for a test design and see the tiles and draft report; open a 24 kg design and see the full-scale checks.

## 4. As built

Built in commits `413fda2` (mould and full-scale libraries) and `9a21f85` (jobs, endpoints and the Full scale tab; app version 0.7.0). These notes record the choices the contract left open and what does not yet match it.

### 4.1 Moulds

**Library** `backend/app/cad/moulds.py` (entry point `generate_moulds`), with `moulds_geometry.py` (outer surfaces and offsets), `moulds_analysis.py` (draft, silhouette, parting plane, ray-cast demould check, wall thickness) and `moulds_sheet.py` (PDF).

- Parts: the nose bay shell, the fuselage shell and the right wing-root fairing. Wing skins and tail surfaces (optional in the contract) are not done.
- Outer mould line: the fuselage and nose bay use the same cross-sections as the printed shell, lofted smoothly (the printed shell uses flat facets between stations). The wing-root fairing is a new part: a concave fillet between the fuselage side and the wing root. Moulds are for the outer surface; the laminate grows inward (stated in the manifest with the layup thickness).
- Pull direction and parting line: each candidate direction (fuselage and nose: left/right or upper/lower halves; fairing: normal to the wing chord plane) is scored by the area below the minimum draft and by ray-cast undercuts; the parting plane sits at the largest silhouette in that direction. For the 24 kg test design the nose and fuselage split left/right and the fairing upper/lower.
- Draft check: every face is sampled per half; faces with area below the minimum draft (default 2°, 0.5° to 10° allowed) outside the parting band are flagged and drawn on the draft map. The part surface is never changed.
- Mould halves: 6 mm wall (a true outward offset of the surface), a 25 mm wide, 10 mm thick flange, M5 clearance holes at 60 mm pitch, tapered registration cones and sockets (0.2 mm clearance), a scribed trim line 5 mm outside the part edge, optional vent/resin channel along the flange.
- Tiling: halves larger than the printer envelope are cut across their long axis where the surface curvature is lowest. Each joint gets a bolting rib on the back (15 mm high, 8 mm thick) with M5 holes, lugs over the flange and tapered alignment keys. The fit is checked on the exported mesh in the print orientation.
- Print orientation per tile from four candidates, chosen so the mould face does not overhang more than 45°; notes cover PETG/ASA, 100 % perimeters at the mould face, sanding, sealing or primer, and release agent.
- Output (`moulds_manifest.json`, schema `vtol-moulds/1`): binary STL and 3MF per tile, STEP per mould half (aircraft coordinates) and an A3 PDF per part (parting line, draft map, tile layout, draft report, tile table, assembly order, laminate allowance).
- Measured locally for the 24 kg test design (all three parts): 26 tiles (nose 2, fuselage 16, fairing 8), all fitting 240 mm, 61 files, about 60 MB on disk, about 3 minutes, peak memory about 880 MB.

**Job.** A mould set is a row of `exports` with `kind = "moulds"` (migration `0007`), so it shares the Phase 5 job: child process, progress, reuse of identical sets, storage under `{APP_DATA_DIR}/files/exports/{id}/` and cascade deletes. It has its own time limit, `MOULD_TIMEOUT_S` (default 1800 s); the memory limit is the shared `EXPORT_MEMORY_LIMIT_MB` (1500 MB).

**Endpoints** (router-level auth; `X-Requested-With: fetch` on state-changing calls):

- `POST /api/projects/{id}/moulds` `{source, parts?: ["nose", "fuselage", "wing_root_fairing"], min_draft_deg?: 2, vent_channels?: false}` → 202 (503 while the worker starts).
- `GET /api/projects/{id}/moulds`, `GET /api/moulds/{mid}`, `DELETE /api/moulds/{mid}`.
- `GET /api/moulds/{mid}/files/{path}`, `GET /api/moulds/{mid}/zip`, `GET /api/moulds/{mid}/tiles/{tile_id}/mesh` (bed preview).
- Phase 5's `/api/exports/...` routes do not serve mould sets, and the mould routes do not serve file exports.

### 4.2 Full-scale checks

**Engine** `backend/app/engine/fullscale.py` (`run_fullscale_checks`) keeps every Phase 3 check and adds:

- Take-off mass against the 23 / 24 / 25 kg thresholds from settings (warning, design limit, legal limit), for the analysis mass and for the mass with the layup structure, with a banner on every screen that shows mass.
- Motor-out hover: with each motor failed in turn, linear programmes check whether the remaining rotors can hold vertical force, roll, pitch and yaw within their full-throttle thrust, and report the remaining roll and yaw authority. Tilt layouts may use up to ±10° of differential tilt for yaw. A plain quad cannot hold heading with a motor out (Mueller and D'Andrea, ICRA 2014), so it fails and a coaxial X8 is sized as the recommendation.
- Wing spar and booms at the final mass against every carbon tube in the catalogue (the Phase 4 margin rule), and the smallest standard tube that would do.
- Landing gear: 1.5 m/s sink rate, 75 mm stroke, 50 % spring efficiency (Raymer), with the Phase 3 boom landing case re-checked at that load factor.
- Battery current at 24 kg hover and in the worst workable motor-out case against the pack rating, with Li-ion alternatives of the same energy from catalogue cells.
- Composite layup per part (plies by fabric weight and orientation, core where used, with sources) and the structural mass it gives, compared with the mass model.
- The A3 operating note beside every range and endurance figure.

**Endpoint**: `POST /api/projects/{id}/fullscale` `{source}` → the checks (synchronous, about a second on top of an analysis). It reuses the newest finished full analysis of the same source with the same parameters, mission and settings (so that analysis' Phase 4 parts and Phase 6 calibration carry through); without one it runs a quick analysis with generic parts and no calibration. Results are cached in memory; one computation runs at a time.

### 4.3 Full scale tab

`frontend/src/tabs/FullScaleTab.tsx` with `frontend/src/tabs/fullscale/FullScaleChecks.tsx` and `MouldsSection.tsx` (helpers `frontend/src/lib/moulds.ts`, API `frontend/src/api/fullscale.ts` and `moulds.ts`). Choose the draft or a version. *Full-scale checks*: the checks list, then the mass thresholds, every motor-out case with the worst one and the octocopter recommendation, the tubes needed, the layup against the mass model, landing gear, battery current with the Li-ion alternatives, and range and endurance with the A3 note. *Moulds for the carbon parts*: tick the parts, *Generate moulds*, progress (polled every 2 s), then per part the halves and tiles (fit, filament, time, print orientation, a preview on the printer bed), the parting line, the draft report with flagged faces explained, assembly order and print notes; files grouped by type with what opens them; ZIP. A banner says when the draft has changed since the moulds were made. Earlier mould sets are listed underneath.

**Tests**: `backend/tests/cad/test_moulds.py` (draft flagging on a zero-draft box, demouldable sphere, tiles fit, keys and flanges, exports valid, memory under 1500 MB), `backend/tests/test_phase7_api.py`, `e2e/tests/phase7.spec.ts` with the 24 kg design in `e2e/fixtures/design_24kg.json`.

### 4.4 Known limits

- Mould generation is slow: about 3 minutes on a fast desktop and several minutes on the shared Fly CPU, with a peak of about 880 MB. It runs on the single worker, so analyses, file exports and flight logs wait behind it.
- Only the right wing-root fairing is moulded. The left one is its mirror image: mirror its tile files in the slicer (Bambu Studio can mirror an object) before printing.
- Tile labels (for example "FUS-L 3/8") are in the file names, the 3MF object names, the notes and the PDF, but are not embossed on the tiles. Mark each tile by hand as it comes off the printer.
- The layup mass is reported, and checked against the mass limits, but not fed back into the mass model: the analysis, Tier 1 estimate and scale-to-weight still use the mass model's areal densities for the final scale.
- Each mould set uses about 60 MB of the 3 GB volume until deleted.
