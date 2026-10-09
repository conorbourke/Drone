# Phase 5 contract: Files

Read `docs/BRIEF.md` ("Files"), `docs/phases/PHASE2.md` section 1 (coordinate system) and the settings (printer envelope). This file adds to them.

**Acceptance (from the brief):** every printed part fits the 240 mm envelope and opens in Bambu Studio, and the STEP files open in a free CAD viewer.

## 1. Geometry kernel

- CadQuery 2.8 (OpenCascade via `cadquery-ocp` wheels for CPython 3.12) in `backend/app/cad/`. The parametric model is built from the same design parameters and the Python `geometry.py` of Phase 3, so CAD, analysis and the browser views agree (test: wing area, span, MAC and positions match the geometry module to 0.5 mm).
- Solids: wing panels (left/right) lofted through airfoil sections from the library with twist, dihedral and sweep, a spar channel sized to the selected spar tube (outer diameter + 0.3 mm clearance) at 25 % chord (stated), and the aileron hinge line marked; fuselage shell (ellipse or rounded rectangle loft) with the swappable nose bay as a separate module (camera window in its lower edge, four M3 mounting bosses and a mating flange); booms are carbon tubes (not printed) with printed motor mounts (mount pattern from the selected motor, M3/M4), tilt hinge blocks (servo pocket sized to the selected servo, hinge pin, stop at `tilt.max_angle_deg`) for tilt layouts, pusher mount for quad + pusher; tail surfaces with spar channel; wing-to-fuselage and boom-to-wing clamps; landing gear (skids or legs).
- Shells for the prototype: wall thickness 0.8 mm for lightweight foaming PLA (two perimeters, stated), solid printed mounts in PETG/ASA/PA-CF (filament per part in the notes).

## 2. Splitting for the printer

- `backend/app/cad/split.py`: every printed part whose bounding box exceeds the usable envelope (settings `printer.usable_envelope_mm`, default 240 × 240 × 240) is split into pieces that fit in some orientation (allow rotation about all axes; check the oriented bounding box). Wings are split spanwise at planes chosen to avoid high-stress zones: never within 15 % of span of the root (the highest bending moment), never through a motor/boom attachment or the aileron hinge ends, and with joints staggered on long panels. Fuselage splits are transverse, away from the wing attachment and the nose-bay flange.
- Each joint gets alignment keys (two tapered pins and matching sockets, 0.2 mm clearance), a bonding surface (a 10 mm overlap lip or a flat face with glue grooves) and, where a spar or boom passes, the tube channel runs continuously through the joint so the tube carries the load across it.
- Output per piece: the solid, its piece number and label (for example "Wing L 2/4"), the printing orientation chosen (largest flat face down; wing pieces leading edge down for strength; state why), filament, estimated print mass (volume × density × infill/wall model) and an estimated print time band.
- A check verifies every exported piece fits the envelope in its chosen orientation, fails the export otherwise, and is unit-tested at the default design and at a large prototype (3 m span).

## 3. Exports

- STL (binary) and 3MF (one part per file, plus a combined 3MF with every printed piece arranged on plates of the printer bed size with 5 mm spacing; include the per-object name and colour; 3MF must be valid per the 3MF core spec so Bambu Studio opens it — validate the package structure and the model XML in tests).
- STEP (AP214) of each part and of the full assembly (with tubes, motors as simple cylinders, props as discs, battery and payload volumes) — validate by re-importing with OpenCascade in tests.
- Drawings: dimensioned 2D drawings as PDF (three-view general arrangement with overall dimensions, wing planform with chords, span, MAC and stations, tail, boom and motor stations, CG and neutral point marks from the latest analysis; title block with project, version, date, scale, units mm) using CadQuery projections or the Python geometry and ReportLab; DXF (ezdxf) for flat parts that can be CNC-cut from carbon plate (motor mount plates, servo plates, nose-bay base) with hole positions.
- Bill of materials CSV: every part from the Phase 4 selection plus printed pieces (filament mass and cost estimate), tubes with cut lengths, fasteners and consumables; columns: item, category, role, manufacturer, model, quantity, unit mass g, line mass g, unit price €, line price €, best supplier, country, URL, last checked, notes.
- Printing notes: a Markdown/PDF sheet per part (filament, nozzle, layer height, walls, infill, orientation, supports, temperatures guidance for LW-PLA foaming, post-processing and joint bonding instructions).
- Exports run on the analysis worker as a job (`exports` table: project, version, status, progress, files list, sizes, errors), stored under `{APP_DATA_DIR}/files/exports/{export_id}/`, downloadable individually or as one ZIP, deleted when the project is deleted. Large jobs report progress. Memory stays within 2 GB (measure; split work per part).

## 4. User interface

Files tab: "Generate files" for the draft or a version, progress, then a grouped list (print files by part with piece count and a fit-check tick, CAD, drawings, BOM, notes) with downloads and "Download all (ZIP)"; a 3D preview of each printed piece in its print orientation on the bed outline; the envelope from settings shown; plain explanations of each file type and which free program opens it (Bambu Studio for 3MF/STL; FreeCAD or an online STEP viewer for STEP; any PDF reader; LibreCAD for DXF).

## 5. Tests

- pytest: CAD builds for all three layouts and four tail types; split pieces all fit the envelope; joints have keys and channels; no split within the root exclusion zone; STL watertight (trimesh); 3MF package valid; STEP re-imports; DXF opens with ezdxf; PDF has the expected pages; BOM totals equal the parts list totals.
- Playwright `e2e/tests/phase5.spec.ts`: generate files for the default design, see every piece ticked as fitting, download the ZIP and the BOM.
