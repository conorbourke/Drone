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

## 6. Implementation notes (backend, as built)

Binding for the UI and later phases; they record the choices this contract left open.

**Seed.** `backend/seed/parts.json` (53 parts, 69 listings, all `verified: false`) is loaded by Alembic data migration `0004` (chosen over a startup hook so it runs exactly once: an owner who later deletes every part does not get the seed back). The migration first deletes the Phase 1 example parts that are still exactly as loaded (identity, source, mass, price, spec, notes and listing URLs unchanged), then inserts the seed only if the parts table is empty. The manual loader (`python -m app.parts_catalog.load seed/parts.json`) still works and is idempotent.

**Table `part_selections`** (migration `0004`): `owner_id`, `project_id` (cascade), `version_id` (nullable; `ON DELETE CASCADE`, deliberately not the Phase 1 RESTRICT policy: a selection is derived design data that goes with its version and must never block deleting it), `role`, `category`, `part_id` (`ON DELETE CASCADE`: deleting a catalogue part drops the selections that used it and the engine fills the role again), `quantity`, `locked`. One row per role: partial unique indexes on `(version_id, role)` for versions and `(project_id, role) WHERE version_id IS NULL` for the draft. Locked rows are the owner's choices; unlocked rows record the engine's latest picks. The list itself is always computed from the design, the catalogue and the locked rows; the unlocked rows are written by every state-changing call (recompute, replace/lock, unlock, an analysis being queued, a version saved, duplicated or restored). `GET` never writes and reports `stored.in_sync`. Saving a version from the draft copies all the draft's rows; a version from a patch copies only the locked rows of its base; duplicate copies all; restore copies the version's rows over the draft's.

**Selection engine** `app/engine/selection.py` (rules and constants in its module docstring). The design context comes from a generic-parts fast analysis of the source (cached in process, about 1 s on first use). Parts and mass are iterated (up to 6 passes; the take-off mass with the parts comes from the Tier 1 mass model with the parts' masses); if the passes do not settle, the coupled roles (lift motor and propeller, pusher motor and propeller, battery) are held and everything is evaluated once more at the final mass, so every sentence quotes the final take-off mass. The battery is chosen last in each pass and never makes the aircraft heavier than the strongest catalogue motor and propeller pair can lift (2 % allowance). Endurance in the list is an estimate for ranking (generic analysis scaled by mass, hover and cruise power of the chosen rotors and the pack energy); the analysis with the parts gives the real figure.

**Analysis with parts.** `POST /api/projects/{id}/analyses` takes `parts: "selected" | "generic"` (default `"selected"`). With `"selected"` the parts list is computed and stored, and `inputs.parts` (spec dicts plus `label`, `mass_g`, `part_id`; see `apply_parts` in `app/engine/analysis.py`) and `inputs.parts_selection` go into the inputs, so they are part of `inputs_hash`; an empty catalogue or an invalid design falls back to generic parts. List items and `GET /api/analyses/{id}` carry `parts: "selected" | "generic"`. In the engine the lift and pusher motor constants are the catalogue ones; the propeller sizes are the selected propeller's; the static CT0/CP0 are fitted to the motor's thrust tests for that exact propeller (trade size within 0.15 in) when the catalogue has them; the battery's chemistry, S, P, capacity, mass and C ratings replace the design's; the boom tube's diameter and wall replace the design's; the motor, propeller, ESC, tilt-servo (+20 g hinge hardware per side), avionics (+20 g power module), battery, spar and boom masses replace the statistical ones. Full-throttle thrust of a catalogue motor is taken on the pack voltage under the motors' real full-throttle current (not their current rating). `result.parts` summarises what was used. The recommendation sweep runs with the same parts. Scale-to-weight jobs stay generic.

**Draft and version payloads** (`GET /api/projects/{id}`, `/draft`, `/versions/{vid}`, and every response that returns a draft or a version) carry `parts_selection` (null until something is stored):

```
{ "updated_at": ISO,
  "roles": { "<role>": { part_id, category, manufacturer, model, name, quantity, locked,
                         unit_mass_g, line_mass_g, verified, spec } },
  "masses_g": { lift_motor_each, lift_prop_each, esc_each, tilt_servo_each, cruise_motor,
                pusher_prop, battery, avionics, spar_tube_per_m, boom_tube_per_m } }
```

`masses_g` keys are present only for the roles stored; `battery` is the pack mass (custom cell packs x 1.08), `avionics` is autopilot + GPS + receiver + telemetry + 20 g power module. These are the numbers the Tier 1 engine should use in place of its statistical motor, propeller, ESC, tilt mechanism, battery, avionics allowance and tube masses.

**Settings schema 3** (upgrader 2 -> 3): `budget.prototype_eur` (default 5000, more than 0 and at most 1,000,000), with label, description and source in `meta`.

**Endpoints** (router-level auth, `X-Requested-With: fetch` on every state-changing call; `?source=draft` (default) or `?source=<version id>` on every parts-list route; 422 with a plain `detail` for a version of another project or a design that cannot be analysed):

- `GET /api/projects/{id}/parts-list` -> the list (shape below). Read-only.
- `POST /api/projects/{id}/parts-list/recompute` -> the list, after storing the picks (`stored.in_sync` true).
- `PUT /api/projects/{id}/parts-list/roles/{role}` `{part_id, quantity?, locked?=true}` -> the list. 404 unknown part; 422 for an unknown role, a role the layout has not got, a part of the wrong category, a pack with another series count, or a cell count that is not a multiple of the series count. `quantity` is used only for a battery built from cells (number of cells; default 2P).
- `DELETE /api/projects/{id}/parts-list/roles/{role}/lock` -> the list, with the role back in the engine's hands.
- `POST /api/projects/{id}/parts-list/refresh-all` -> 202 `{queued: [{part_id, name}], skipped: [{part_id, name, reason}], message}`; one worker job per part (priority after analyses and validation); parts refreshed within the hour are skipped. 503 plain without a key or while the worker starts.
- `POST /api/parts/{id}/refresh-listings?wait=true` -> 200 `{part: PartOut, refresh: {status: done|refused, message, added, updated, checked, working, ignored, model?, summary?}}` (synchronous, typically 30-120 s with the real API); `?wait=false` -> 202 `{part}` and the worker does it (poll `GET /api/parts/{id}`). 429 `{detail, retry_after_s}` with `Retry-After` within an hour of the last refresh; 503 `{detail: "Add the ANTHROPIC_API_KEY secret in GitHub and redeploy to enable supplier lookups"}` without a key; 502 `{detail}` when the lookup fails (stored on the part too).
- `PartOut` gains `listings_refreshed_at`, `listings_refresh_status` (`queued | running | done | refused | error | null`), `listings_refresh_message`; `ListingOut` gains `url_ok` (null = never checked), `url_status`, `url_checked_at`, `stale` (not checked in 30 days).

Parts-list response:

```
{ selection_version, layout, generated_at,
  source: {kind: "draft"|"version", version_id, version_number},
  stored: {in_sync, stored_at},
  roles: [ { role, label, system: propulsion|energy|flight_control|structure, filled,
             part: {id, category, manufacturer, model, name, mass_g, verified, source,
                    price_eur, price_source: listing|estimate|null, spec} | null,
             quantity,                       # units to buy (pairs, cells, 1 m tubes)
             unit_mass_g, line_mass_g,       # line = installed mass (tubes: cut length)
             unit_price_eur, price_source, line_price_eur,
             best_listing: Listing | null, listings: [Listing],
             reasoning: [sentence],          # the unfilled reason when filled is false
             alternatives: [ {part, feasible, reason_lost, deltas: {mass_g?, price_eur?,
                              hover_g_per_w?, max_thrust_g?, endurance_min?, margin?,
                              current_margin?, torque_margin?}, tier?: cheaper|premium,
                              quantity?, label?} ],
             flags: [ {code, message} ],     # unverified, no_listing, no_price, stale,
                                             # links_broken, no_thrust_data, design_mismatch,
                                             # sold_in_pairs, rotation, fit, power, radio,
                                             # joint, build, endurance, constraint, final_scale
             locked, unfilled_reason, metrics: {...}, custom_pack, tier1_mass_g } ],
  consumables: {label, system: "consumables", mass_g, mass_source, cost_eur, cost_source,
                items: [{label, cost_eur}]},
  totals: {mass_g, tier1_mass_g, mass_vs_tier1_g, avionics_parts_g, avionics_allowance_g,
           takeoff_mass_kg, takeoff_mass_generic_kg, estimated_endurance_min,
           generic_endurance_min, endurance_note, cost_eur, budget_eur,
           budget_status: under|near|over, budget_message, budget_fraction, unpriced_roles},
  upgrades: [ {role, role_label, part, label, extra_cost_eur, mass_change_g,
               endurance_gain_min, eur_per_min | null, trade_off} ],
  unfilled: [ {role, label, reason} ],
  uk_import_note, system_labels, catalogue_size, context: {...} }
Listing = {id, supplier_name, country, url, price_eur, in_stock, last_checked_at, url_ok,
           url_status, stale}
```

Totals: `mass_g` is the selected parts' installed mass plus consumables; `tier1_mass_g` the statistical (Tier 1 mass model) mass of the same components; `cost_eur` the priced lines (best listing, else the catalogue estimate) plus the consumables estimate; lines with no price are listed in `unpriced_roles` and named in `budget_message`. `budget_status`: `under` up to 90 % of the budget, `near` to 100 %, `over` above.

**Supplier lookup** `app/suppliers.py` (see its docstring): `claude-opus-5-5`, adaptive thinking, effort `medium`, `server-side-fallback-2026-07-01` with `fallbacks: "default"`, `web_search_20260209` (6 searches, `user_location` Ireland/Dublin) and `web_fetch_20260209` (6 fetches), `pause_turn` resumed up to 4 times, search errors read from result blocks, structured JSON (`output_config.format`) validated again with Pydantic (one retry without the format if the API refuses it with the web tools), only `match: "exact"` listings stored, every URL checked server-side (HEAD, then GET; 200-399 working; redirects not followed; IP literals and private, loopback or link-local hosts never contacted). Test seam `CLAUDE_FAKE_SUPPLIER_FILE` (ignored in production). The single-part refresh is synchronous by default; a platform proxy with a short idle timeout would need `?wait=false`.

**Assistant.** `get_parts_list` returns the draft's list in short form (per role: part, quantity, mass, price, locked, why, flags; totals with the budget message; the top upgrades; the UK import note), or an empty list with a note when the catalogue is empty.

## 7. UI notes (as built)

**Parts tab** (`frontend/src/tabs/PartsTab.tsx`, pieces in `frontend/src/tabs/parts/`, pure helpers in `frontend/src/lib/partsList.ts`, API calls and types in `frontend/src/api/partsList.ts`). Top to bottom: Totals, Parts list, Upgrades worth paying for, and the parts catalogue (the Phase 1 browser, now a collapsed card).
- A select picks the draft or a saved version (`?source=`). The draft is flushed before the list loads. When `stored.in_sync` is false (and the catalogue is not empty) the tab calls `recompute` once per source per visit and shows "Updating the parts list…"; "Recompute" does the same on demand. After any change to the draft's stored picks (recompute, replace, unlock) the workspace fetches `GET /draft` again so the Design tab's Tier 1 estimate uses the new `parts_selection.masses_g`. The Design tab also refetches it every time it opens (an analysis, a version save or a restore can change it), and a restore takes the selection from the restored draft.
- Lines are grouped by system (propulsion, energy, flight control, structure, then the consumables line). Each line: role label, "Your choice" when locked, manufacturer and model, quantity (cells and S/P for a custom pack, "n x 1 m" for tubes), line and unit mass, line and unit price with its source (best listing or catalogue estimate; "No price" lines say they are not counted), the best listing with an IE/UK flag (inline SVG plus the code), supplier link (new tab), price, stock, "checked 8 h ago (9 Oct 2026)" (full date-time on hover), link status (working / not checked / not working / stale; a failed check wins over stale), "+n more shops"; flags as chips with the server's plain message (the unverified flag links to the manufacturer `source` page); "Why this part" expands the reasoning and every listing; Replace; Unlock when locked; "Check prices now". Unfilled roles show the `unfilled_reason` (and a banner lists them).
- Replace opens a dialog with the role's alternatives: meets / does not meet the requirements, cheaper/premium tier, the custom-pack label, `reason_lost`, and the numbers (mass, price and endurance as changes against the current choice; hover g/W, full thrust and margins as the alternative's own values, as the API defines them). "Choose" (or "Choose anyway" for an infeasible one) PUTs `{part_id, quantity?, locked: true}`; `quantity` is passed for custom battery packs.
- Totals: cost against `totals.budget_eur` as a meter (`role="meter"`, fill in the status colour on a lighter track of the same hue, a budget line when over, status pill with icon and words) plus `budget_message`, unpriced lines, selected parts and consumables mass against the Tier 1 estimate of the same items, take-off mass with parts against generic, estimated endurance against generic (with `endurance_note`), and the UK import note.
- Upgrades: role, part or pack label, extra cost, mass change, endurance gain, € per minute (— when none), trade-off sentence.
- Price checks: "Check prices now" POSTs `refresh-listings?wait=false` and polls `GET /api/parts/{id}` every 3 s while the status is queued or running, then reloads the list. 429 shows "Checked within the last hour; try again in N minutes" (from `retry_after_s`; `ApiError` now carries the error body), 503 shows the server's message. "Check all prices" POSTs `refresh-all`, shows its message and skipped parts, and polls every queued part the same way. Each line shows its refresh state (queued, checking, checked, declined, failed) and message; the states of parts refreshed earlier are read from `GET /api/parts` on open.

**Tier 1 with parts.** `EngineInput.partsMasses` (optional, `PartsMasses` = `parts_selection.masses_g`). `mass.ts` replaces, key by key: lift motors (mounts stay 20 % of the motor), lift propellers, ESCs (also the pusher ESC), tilt mechanism (servo + 20 g hinge hardware per side), pusher motor and propeller, battery, avionics, prototype spar tube (g/m x span) and boom tubes (g/m x length, also the tail support), each with 3 % uncertainty (tilt 15 %, avionics 10 %), `from_parts: true` and a source naming the selected part, as the server's `app/engine/mass.py` does. `MassResult.parts_used` lists them and an assumption says so. The battery energy still comes from the design inputs. The Design tab passes the draft's masses; the compare page passes each version's. The estimates panel says which masses come from selected parts (or that none do) with a link to the Parts tab, and marks those rows in the breakdown with "part". Tests: `frontend/src/engine/parts.test.ts`, `frontend/src/lib/partsList.test.ts`.

**Settings.** The `Settings` type is schema 3 (`budget.prototype_eur`); the Settings page has a Budget card with that row (label, description and source from `meta`).

**Test ids** (new; all earlier ids unchanged): `parts-list`, `parts-source`, `parts-recompute`, `parts-updating`, `parts-error`, `parts-empty-catalogue`, `parts-unfilled-summary`, `parts-system` (`data-system`), `parts-line` (`data-role`, `data-part-id`, `data-locked`), `parts-line-part`, `parts-line-qty`, `parts-line-mass`, `parts-line-price` (each `data-value`), `parts-locked`, `parts-supplier-link`, `parts-supplier-country` (`data-country`), `parts-last-checked`, `parts-link-status` (`data-status`: working, unchecked, broken, stale), `parts-no-listing`, `parts-flag` (`data-code`), `parts-unverified-source`, `parts-reasoning-toggle`, `parts-reasoning`, `parts-replace`, `parts-unlock`, `parts-refresh`, `parts-refresh-status` (`data-status`), `parts-refresh-message`, `parts-refresh-all`, `parts-refresh-all-message`, `parts-unfilled` (`data-role`), `parts-consumables`, `parts-totals`, `parts-total-cost`, `parts-budget` (`data-value`), `parts-budget-bar` (`data-status`), `parts-budget-status` (`data-status`), `parts-budget-message`, `parts-unpriced`, `parts-mass-total`, `parts-mass-tier1`, `parts-takeoff`, `parts-endurance` (`data-value`), `parts-uk-note`, `parts-upgrades`, `parts-upgrade` (`data-role`), `parts-replace-dialog`, `parts-alternative` (`data-part-id`, `data-feasible`), `parts-alternative-choose`, `parts-replace-cancel`, `parts-no-alternatives`, `parts-catalogue`, `estimates-parts-masses` (`data-count`), `setting-budget.prototype_eur`, `setting-row-budget.prototype_eur`. Kept: `parts-category`, `parts-search`, `parts-table`, `part-row`, `part-unverified` (inside the catalogue card).

**End-to-end** (`e2e/tests/phase4.spec.ts`): the seeded catalogue (at least 50 parts in all eleven categories, listings present) on a fresh database; Parts for a new project's default design recomputes on open and shows all eleven roles, the consumables line, https supplier links, a total against €5,000 and the UK note; the Design tab names the masses from parts; replacing the ESC with the first feasible alternative locks it and changes the total cost, the parts mass and the Design tab's take-off mass; Unlock; "Check prices now" goes queued to done through the worker with `CLAUDE_FAKE_SUPPLIER_FILE=e2e/fixtures/supplier_fake.json` (an empty answer, so no shop is contacted) and a second click within the hour shows the 429 message; the catalogue card lists the seeded parts.
