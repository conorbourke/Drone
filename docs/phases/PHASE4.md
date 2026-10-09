# Phase 4 contract: Parts and suppliers

Read `docs/BRIEF.md` ("Parts and suppliers"), `docs/ARCHITECTURE.md` (parts catalogue), `docs/phases/PHASE3.md` (propulsion and battery models that consume parts). This file adds to them.

**Acceptance (from the brief):** a complete prototype parts list is produced with working supplier links and a total under or against the €5,000 budget.

## 1. Seed data

- `backend/seed/parts.json` (committed) holds real components researched on 9 Oct 2026: 53 parts in the eleven categories (motors with manufacturer thrust tables, propellers, ESCs, tilt servos, LiPo and Li-ion packs and cells, ArduPilot autopilots, GPS, radio and telemetry links legal in Ireland, carbon tubes) with 69 UK and Irish listings. `source` is the manufacturer page; `verified` is false until the owner (or a later check) confirms the numbers on that page, and the UI says so on every unverified part. Notes on radio legality in Ireland (868 MHz SRD limits, 2.4 GHz 100 mW EIRP with LBT, 433 MHz 10 mW only, 915 MHz not legal) live in `docs/PARTS_NOTES.md` and in each radio part's notes.
- A data migration (Alembic `0004`) or the existing idempotent loader runs at startup **only when the parts table is empty**, so the owner's edits are never overwritten. The example placeholder parts from Phase 1 are removed by the same migration if untouched.
- Wiring and small parts category additions are not needed in the schema: wiring, connectors, power module and fasteners are a "consumables" line in the parts list with an estimated cost and the Tier 1 wiring fraction for mass.

## 2. Engine-driven recommendations

`backend/app/engine/selection.py`: for a design (draft or version) and its latest analysis (or the fast path), choose a full parts set:
- Lift motors and propellers: candidates whose cell range includes the pack, whose thrust tables (or the motor model for parts without one) give hover at ≤ 1 / (thrust-to-weight minimum) of maximum thrust with the design's propeller diameter or the nearest available, and whose maximum current fits the ESC; score by hover efficiency (g/W at the hover point), then mass, then price. Cruise motor for quad + pusher: thrust at cruise drag near the best-efficiency throttle band. For tilt layouts the tilting pair must also meet cruise thrust at a sensible throttle.
- ESCs: continuous rating ≥ 1.25 × peak motor current, cell range, telemetry preferred.
- Tilt servos (tilt layouts only): torque ≥ 2 × the computed hinge moment (motor thrust offset from the hinge plus gyroscopic and inertia allowance, stated) and speed ≤ 0.15 s/60°.
- Battery: chemistry and pack (or cells for a custom pack) meeting usable energy for the endurance target and continuous discharge ≥ peak current / (current margin from settings); report pack vs custom Li-ion trade-off.
- Autopilot, GPS, radio, telemetry: a default ArduPilot-supported set legal in Ireland, with a cheaper and a premium alternative.
- Carbon tubes: spar and booms meeting the Phase 3 structure checks with margin, lightest first.
- Every choice carries `reasoning` (plain sentences quoting the numbers: "Hover at 2.6 kg needs 650 g per motor; at that thrust the MN4014 KV400 with a 15×5 prop draws 6.1 A at 66 % of its maximum thrust, giving 2.0 thrust-to-weight"), the runner-up alternatives with why they lost, and flags (unverified spec, missing thrust data, no Irish/UK listing).
- Selected parts feed back into the design: a `parts_selection` stored per project draft and per version (new table `part_selections`: project/version, category, role (`lift_motor`, `cruise_motor`, `lift_prop`, `pusher_prop`, `esc`, `tilt_servo`, `battery`, `autopilot`, `gps`, `radio`, `telemetry`, `spar_tube`, `boom_tube`), part_id, quantity, `locked` (owner choice wins over the engine)). The Tier 1 engine and the Phase 3 analysis use selected parts' real masses and motor/propeller data instead of statistical estimates when present (Tier 1 gets them through the draft/version payload).
- "Upgrades worth paying for": for each role, alternatives that cost more and improve endurance, mass or margins, with the trade-off in plain words and the € per minute of endurance gained.

## 3. Supplier lookup (Claude with web search)

`POST /api/parts/{id}/refresh-listings` runs Claude (`claude-opus-5-5`, adaptive thinking, effort `medium`, server-side fallbacks, refusal handling, typed errors — read the bundled Claude API skill first) with the server-side `web_search_20260209` tool (and `web_fetch_20260209`) restricted with `user_location` Ireland and prompted to find current price, stock and product-page URL at Irish and UK shops for exactly that manufacturer and model, returning structured JSON (validate it). Listings are upserted with `last_checked_at` = now; links not found are kept but marked stale after 30 days. Each URL is then checked server-side with a HEAD/GET (status 200–399) before it is stored as working. A missing API key returns the plain 503 message. Rate: one refresh per part per hour; a "refresh all in this parts list" button queues them on the worker.

## 4. Parts list, totals and budget

- Parts tab: the recommended list for the current draft (or a version), grouped by system (propulsion, energy, flight control, structure, consumables), each line with part, quantity, unit and line mass, unit and line price (best listing), supplier links with country flag and "last checked" date, reasoning on click, unverified flag, lock/replace (choose an alternative from the filtered candidates).
- Running totals: mass of selected parts vs the Tier 1 estimate, total cost vs the €5,000 prototype budget (budget is a setting, `budget.prototype_eur`, default 5000, settings schema 3) with a bar and a plain status; shipping/VAT note for UK orders after Brexit (import VAT and possible customs charges into Ireland).
- Upgrades section from section 2.
- BOM export is Phase 5; the list already has the data.

## 5. Tests

- pytest: seed loads and validates; selection picks parts that satisfy their constraints for the default prototype and a 24 kg design; locked parts are respected; selected parts change the analysis masses; reasoning strings contain the numbers; listings refresh with a fake Claude response and URL checks mocked; totals and budget.
- Playwright `e2e/tests/phase4.spec.ts`: open Parts for the default design, see a complete list with links and a total against €5,000, replace a part, and see totals and the design's mass update.
