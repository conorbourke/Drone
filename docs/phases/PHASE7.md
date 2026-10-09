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
