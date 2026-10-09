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

## 6. As built

Built in commits `ec26663` (CAD library), `f746fc9` (export jobs and downloads) and `7684fff` (Files tab). These notes record the choices the contract left open and what does not yet match it.

**CAD library** `backend/app/cad/` (entry point `generate_files` in `__init__.py`). The model agrees with the engine's geometry module to 0.2 mm. Everything is built part by part (one part's solids are meshed, written and released before the next) so memory stays bounded. Output layout under the export directory:

```
print/stl/<part>_<n>of<m>.stl     one binary STL per piece, in print orientation
print/3mf/<part>.3mf              one 3MF per part (its pieces x copies, on plates)
print/all_pieces.3mf              every printed piece on bed-sized plates
cad/<part>.step, cad/assembly.step  STEP AP214, aircraft coordinates, mm
drawings/drawings.pdf             dimensioned drawings, 4 A3 sheets
drawings/dxf/*.dxf                flat plates for CNC-cut carbon
bom.csv                           bill of materials with totals
notes/<part>.md, notes/printing_notes.pdf
manifest.json                     what was made: parts, pieces, checks, files, BOM totals
```

Measured locally: the default design makes 27 pieces (all fit 240 mm) in about 40 s with a peak of about 680 MB; a 3 m span prototype makes 36 pieces at about 710 MB. One export takes 20 to 35 MB of disk.

**Export job** (`backend/app/exports.py`, `backend/app/export_child.py`).

- Table `exports` (migration `0005`; `kind` added by `0007`, default `"files"`; mould sets of Phase 7 are rows with `kind = "moulds"`): owner, project (cascade), `version_id` (nullable; `ON DELETE CASCADE`, deliberately not the Phase 1 RESTRICT policy, because files can be made again from the version and must never block deleting it), `source`, `inputs` (snapshot), `inputs_hash`, `status` (queued, running, done, error), `progress`, `stage`, `error`, `manifest`, `files_dir`, `total_size_bytes`, `duration_s`, `peak_rss_mb`, `started_at`, `finished_at`, `reused_from_id`. Ids are never reused (they name directories).
- Inputs are snapshotted when the export is queued: the source's parameters and mission, the owner's settings, the newest finished full analysis of the same source with the same parameters and mission (only its balance and structure blocks are used, for the CG and neutral point marks and the spar), the Phase 4 parts list (the BOM lists exactly those parts at their best listing; generic sizes with an empty catalogue) and the title-block text. `inputs_hash` covers everything except the title-block date.
- Reuse: a queued or running export with the same hash for the same source is returned as is; a finished one whose files are all still on disk is returned, or hard-linked into a new export for another source, instead of running again.
- The job runs on the single analysis worker, but the CAD work happens in a child process (`python -m app.export_child`): OpenCascade holds about 470 MB once loaded and only gives it back when the process ends. The child reports progress as JSON lines. The worker kills it after `EXPORT_TIMEOUT_S` (default 600 s) or when its resident memory passes `EXPORT_MEMORY_LIMIT_MB` (default 1500 MB on the 2 GB machine), and records its peak memory. CAD and envelope errors become a plain message on the row.
- Files live in `{APP_DATA_DIR}/files/exports/{id}/` and are removed with the row, its version or its project; directories without a row are swept at startup, and exports left running by a restart become errors. Downloads are looked up in the stored manifest; no path is taken from the URL.

**Endpoints** (router-level auth; `X-Requested-With: fetch` on state-changing calls):

- `POST /api/projects/{id}/exports` `{source: "draft" | {version_id}}` → 202 list item (503 while the worker starts).
- `GET /api/projects/{id}/exports` → list; `GET /api/exports/{eid}` → detail with manifest; `DELETE /api/exports/{eid}` → 204 (cancels a running one).
- `GET /api/exports/{eid}/files/{path}` → one file; `GET /api/exports/{eid}/zip` → every file as one ZIP, streamed while it is written (3MF stored, the rest deflated); 410 when files have gone missing from disk.
- `GET /api/exports/{eid}/pieces/{piece_id}/mesh` → indexed mesh of one piece in print orientation for the preview (decimated above 60,000 triangles).

**Files tab** (`frontend/src/tabs/FilesTab.tsx`, helpers `frontend/src/lib/files.ts`, API `frontend/src/api/exports.ts`, preview `frontend/src/components/PiecePreview3D.tsx`, lazy-loaded three.js). Choose the draft or a version, *Generate files*, progress polled every 1.5 s (queue position while waiting), then: printed parts with piece count, a fit tick per piece against the envelope from settings, filament, mass and print time, and a 3D preview of the selected piece standing on the bed outline inside the envelope box; every file grouped (print, CAD, drawings, bill of materials, notes) with what it is and which free program opens it; *Download all (ZIP)*. A banner says when the draft has changed since the files were made. Earlier exports are listed underneath and can be shown again or deleted.

**Tests**: `backend/tests/cad/` (CAD, split, joints, watertight STL, 3MF package, STEP re-import, DXF, PDF, BOM totals), `backend/tests/test_phase5_exports.py` (job, reuse, memory guard, downloads, ZIP, deletes), `e2e/tests/phase5.spec.ts`.

**Known limits.**

- Exports are kept until deleted. Each one uses 20 to 35 MB of the 3 GB volume; delete old ones on the Files tab if space runs short.
- The Phase 1 RESTRICT policy does not apply to exports (see above): deleting a version deletes its files.
- Only one job runs at a time on the worker, so an export waits behind any running analysis, flight log or mould set.
