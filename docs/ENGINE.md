# Tier 1 engine: methods, constants and sources

This is the reference for the instant estimates that run in the browser on every edit (Phase 2). It lists every method, constant and assumption, where each comes from, and how the uncertainty ranges are built. Phase 3's server analysis (AVL, XFOIL, propulsion and battery models) replaces most of these numbers with higher-fidelity ones; the table at the end says which.

**In one sentence:** the engine builds the aircraft's geometry from your parameters, adds up the weight of every part, works out where it balances, estimates lift and drag with standard textbook methods, and turns that into hover and cruise power and an endurance range.

**Code:** `frontend/src/engine/` (pure TypeScript, no React or DOM).

| File | What it does |
|---|---|
| `index.ts` | Public API: `estimate`, `compareLayouts`, `buildGeometry` and the types |
| `geometry.ts` | Planform, tail, fuselage loft, booms, rotors, render primitives, geometry warnings |
| `mass.ts` | Component weights, battery, motor sizing, the weight fixed-point loop, balance point |
| `aero.ts` | Airfoil data lookup, lift-curve slope, maximum lift, stall, drag build-up, Oswald, neutral point |
| `performance.ts` | Cruise, hover and transition power, mission energy, endurance, range, battery current |
| `propeller.ts` | Generic propeller CT(J), CP(J) curves (a port of the server's `propulsion.py`) for the cruise propeller efficiency |
| `checks.ts` | Input validation and the pass/warn/fail checks with plain messages |
| `compare.ts` | The three-layout comparison |
| `atmosphere.ts`, `units.ts`, `quantity.ts`, `constants.ts`, `types.ts` | Air, unit conversions, uncertainty rules, every constant with its source, types |

## Public API

```ts
estimate(input: EngineInput): Estimates                  // one design, about 0.4 ms (budget 5 ms)
compareLayouts(input: EngineInput): LayoutComparison[]   // the same design for front_tilt, rear_tilt, quad_pusher
buildGeometry(params: DesignParameters, options?: BuildGeometryOptions): Geometry

EngineInput = { parameters: DesignParameters, mission: Mission, settings: Settings, airfoils: Record<id, AirfoilSummary> }
Quantity    = { value, low, high, unit, label, explain, source }
Status      = { key, label, level: 'ok' | 'warn' | 'fail' | 'info', message }
```

`BuildGeometryOptions` is optional: `airfoils` (thickness from `/api/airfoils`), `coordinates` (section shapes from `/api/airfoils/{id}`; a NACA 4-digit shape is drawn until they load) and `render` (default true; `estimate` turns it off, so `Estimates.geometry.render` is empty and the 3D view should call `buildGeometry` itself).

Every output number is a `Quantity` with a range, a one- or two-sentence plain explanation and its source. `estimate` never throws. If the input can't be used (zero span, missing blocks, reversed payload range and so on), it returns `valid: false`, empty sections and `fail` statuses that say what to fix. Schema v1 documents work: missing v2 fields get the Phase 2 contract defaults (`withDefaults`).

## Coordinates and units

These follow Phase 2 contract section 1. The origin is the nose tip on the centreline, with x aft, y to the right wing and z up. Parameters use mm, g and degrees; the maths uses SI (m, kg, N, W). Air is sea-level ISA: ρ = 1.225 kg/m³, μ = 1.789 × 10⁻⁵ kg/(m·s), a = 340.3 m/s (ICAO Doc 7488 / ISO 2533). Everything is evaluated at sea level in still air.

## Geometry (`geometry.ts`)

Phase 3's Python port must reproduce these numbers to 0.1 % (`shared/fixtures/tier1_cases.json`), so the conventions are exact.

**Wing (trapezoidal; Raymer ch. 4 "Wing geometry").** The span b is tip to tip as seen from above, with root chord c_r, tip chord c_t and taper λ = c_t/c_r.
- Area S = b (c_r + c_t)/2, measured through the fuselage (the reference area). Aspect ratio A = b²/S.
- Mean aerodynamic chord c̄ = (2/3) c_r (1 + λ + λ²)/(1 + λ), at spanwise station ȳ = (b/6)(1 + 2λ)/(1 + λ).
- Leading edge of the MAC: x_LE + ȳ tan Λ_LE. The aerodynamic centre is the MAC quarter chord.
- Sweep of any chord line n: tan Λ_n = tan Λ_LE − (4n/A)(1 − λ)/(1 + λ).
- Exposed area = S minus the strip inside the fuselage: (w_f/2)(c_r + c(w_f/2)).
- Wetted area = 2 S_exposed (1 + 0.25 t/c), which is Torenbeek's relation with equal root and tip thickness. It agrees within about 1 % with Raymer's S_exp (1.977 + 0.52 t/c).
- Dihedral rotates each panel about the root chord line. The tip leading edge sits at (x_LE + (b/2) tan Λ, b/2, z + (b/2) tan Γ).
- Twist and incidence only change the drawn sections in Tier 1.

**Airfoil shape.** Thickness and its position come from `/api/airfoils` when loaded. Before that, the engine uses a built-in table of the library's nominal values (UIUC coordinates database; NACA 4-digit values follow exactly from the designation). An unknown id falls back to a generic 10 % section, flagged by a status.

**Tail (all four types).** `tail.arm_mm` runs from the wing MAC quarter chord to the tail quarter chord, so the tail quarter chord sits at x_ac + arm.
- **Conventional:** the horizontal tail is span × chord. One fin of height `tail.height_mm` × chord.
- **Twin-boom H:** the same horizontal tail, with two fins on the booms.
- **V and inverted V:** `span_mm` is the tip-to-tip span seen from above and Γ = `v_angle_deg`. Panel area S_t = span × chord / cos Γ. The horizontal equivalent is S_t cos Γ and the vertical equivalent is S_t sin Γ (projection). Pitch stiffness uses S_t cos² Γ (each panel's lift is tilted and so is its angle change; NACA Report 823, Purser & Campbell). Panel-based aspect ratio = span / (chord cos Γ). Inverted V panels slope down (panel angle −Γ).
- **Tail volume coefficients** use the projected areas (Raymer ch. 6): V_H = S_h l_t /(S c̄) and V_V = S_v l_t /(S b).
- **Tail support:** if the tail trailing edge is behind the fuselage (or behind the boom ends for the H tail), a tube of the boom diameter carries it. The engine counts its length, mass and drag.

**Fuselage loft.** Let d = max(width, height).
- The elliptical nose is l_n = min(1.2 d, 0.25 L) long, with section scale √(1 − (1 − x/l_n)²) at 13 equally spaced stations.
- The section stays constant up to the tail cone, which is l_t = min(2.5 d, 0.35 L) long and tapers linearly to 30 % of the full section.
- Section perimeter: ellipse by Ramanujan, π[3(a + b) − √((3a + b)(a + 3b))]. A rounded rectangle with corner radius r = 0.25 min(w, h) has perimeter 2(w + h) − (8 − 2π) r.
- Wetted area = the sum of frustum side areas between stations, using the radius that gives the same perimeter. The base is not counted.
- Fineness = L / d_eq, with d_eq = √(4 A_max/π).
- These loft proportions are drawing conventions (estimates). The same stations drive the 3D view, the wetted area and the fuselage mass.

**Booms, rotors and hinge.**
- Booms run along x at y = ±offset, z = wing z, from x_LE + x_offset over the boom length.
- Lift rotors sit at boom front + `motors.front_x_mm` and + `motors.rear_x_mm`, `motors.height_mm` above the boom.
- The pusher sits at (`pusher.x_mm`, 0, 0), thrusting forward.
- The tilt hinge sits at boom front + `tilt.axis_x_mm`. In front tilt the front pair tilts and the rear pair stops in cruise. Rear tilt is the reverse. In quad + pusher all four lift rotors stop.
- The bottom of the landing gear is min(−fuselage height/2, wing z − boom radius) − gear height.

**Geometry warnings:**
- Propellers overlapping front to rear or left to right (fail).
- Propeller discs over the fuselage, or over the wing leading or trailing edge at the boom (warn).
- Motors off the boom, or booms outside the wing.
- Tilt axis more than a quarter of a propeller diameter from the tilting motors.
- Unusual V-tail angle.
- Tail support needed (info).

## Mass and balance (`mass.ts`)

Each part has a mass, an x position, a relative uncertainty, a source and an explanation. The mission scale picks the construction: `prototype` is 3D-printed lightweight foaming PLA with carbon tubes; `final` is carbon-fibre composite.

**Placeholders.** The printed-structure densities below are first guesses. They must be calibrated against the built weights entered in Phase 6. The motor, ESC and propeller relations are replaced by real parts in Phase 4.

| Item | Relation | Basis | Uncertainty |
|---|---|---|---|
| Wing skin and ribs | Areal density × wing area: printed 0.75 kg/m², carbon 1.0 kg/m² | Printed: LW-PLA skin ~0.45 mm at ~0.55 g/cm³ over ~2.05 m² wetted per m², +50 % for ribs and bays (**placeholder**). Carbon: two 200 g/m² plies per skin with resin on a foam core | ±30 % / ±35 % |
| Wing spar, prototype | Carbon tube through the full span. Diameter = 70 % of root thickness, rounded down to whole mm, 8–30 mm | Tube mass per metre (below) | ±15 % |
| Wing spar, final | Spar caps sized for root bending: M = n·(W/2)·(4/3π)(b/2), n = 6 (4 limit × 1.5). Cap area = M/(σ h) with σ = 600 MPa and h = 0.85 × root thickness. Cap mass is integrated as root area × semi-span/3, × 1.3 for webs and joints | Beam bending with an elliptic load (centroid 4/3π of the semi-span, Anderson); allowable is a knock-down from ~1500 MPa for UD carbon | ±35 % |
| Fuselage | Areal density × wetted area: printed 0.8 kg/m² (**placeholder**), carbon 1.0 kg/m² | Shell, frames, wing and boom mounts | ±35 % |
| Tail | Areal density × panel area: printed 0.5 kg/m² (**placeholder**), carbon 0.7 kg/m² | | ±35 % |
| Booms and tail tube | Carbon tube mass per metre m′ = ρ π (D − t) t, with ρ = 1550 kg/m³ and wall t = max(1 mm, 0.05 D) | Tube geometry. Checks against catalogue tubes: 20×18 ~95 g/m, 8×6 ~33 g/m, 30×27 ~210 g/m | ±15 % |
| Lift motors (4) | Mass m = 1.2 P^0.73 g, with P the maximum continuous electrical power in W | **Statistical**: the engine author's power-law fit to catalogue outrunners (~55 g at 200 W, ~100 g at 400 W, ~250 g at 1.6 kW, T-Motor MN/U class). It has the same form as published regressions (Gur & Rosen 2009; Lundström et al. 2010), but the coefficients are **not** from them | ±30 % |
| Motor sizing | Each motor's full thrust = T/W_min × W × 1.03 (download) × worse pair share / 2. Power = momentum-theory ideal power / FM 0.55 / motor efficiency 0.80 | Leishman ch. 2; FM at full load from Brandt & Selig static data | via the motor row |
| ESCs (4) | m = 1.0 g/A × rating + 5 g, with rating = 1.2 × full-power current at nominal pack voltage | **Statistical** fit to catalogue ESCs (20 A ~25 g, 40 A ~45 g, 80 A ~85 g) | ±40 % |
| Propellers | m = 20 g × (D / 305 mm)^2.5 × blades/2 | **Statistical** fit (12″ composite ~20 g, 30″ carbon ~190 g with adapter) | ±40 % |
| Motor mounts | 20 % of motor mass | Estimate | ±50 % |
| Tilt mechanism (tilt layouts) | Per side: 25 g + 25 % of the tilted motor + propeller | Estimate (servo, hinge, bearings) | ±50 % |
| Pusher (quad + pusher) | Motor sized for static thrust 0.5 × weight (momentum theory, FM 0.6, motor efficiency 0.8), plus ESC, propeller and mount by the rows above | Common QuadPlane practice (0.3–0.6 × weight) so it can accelerate through transition and climb on the wing | as above |
| Control servos (4) | Each 6 g + 0.25 % of take-off mass | Estimate: 9 g class at 2.5 kg, 60–70 g class at 24 kg | ±40 % |
| Avionics | Owner allowance `allowances.avionics_g`, under the wing leading edge | Owner input | ±20 % |
| Landing gear | Skids 3 %, legs 4 %, none 0 % of take-off mass | Estimate; Raymer ch. 15 gives 3–6 % for light aircraft | ±50 % |
| Battery | Mass = energy / pack specific energy: LiPo 145 Wh/kg, Li-ion 200 Wh/kg. Energy = cells_series × 3.7 V (LiPo) or 3.6 V (Li-ion) × capacity_mah × cells_parallel | **Statistical**, from datasheets: a 6S 5000 mAh LiPo (111 Wh) weighs 750–820 g. 21700 cells reach 230–260 Wh/kg; packs reach ~190–210 | ±10 % |
| Wiring | f/(1 − f) × the rest of the empty mass (so wiring is the fraction f of the empty mass), at its CG | Owner input `allowances.wiring_fraction` | ±20 % |
| Payload | At the nose-bay centre (x = nose_bay.length/2); maximum and minimum from the mission | Contract | exact |

`battery.capacity_mah` is the capacity of one parallel unit, so pack capacity = capacity_mah × cells_parallel.

**Iteration.** Motor size depends on total mass and on the balance point, because the motor pair carrying more weight sets the size. The engine starts from the mission target mass and repeats the build-up until the mass changes by less than 0.05 g and the CG by less than 0.05 mm, for at most 20 passes. It reports `converged` and the pass count; when it did not converge, the take-off mass explanation says so instead of claiming convergence. Typical designs converge in 7–10 passes. A different starting guess gives the same answer (tested).

**Balance.** CG = Σ m x / Σ m, at both the maximum and minimum payload, also given as % of MAC from the MAC leading edge. The hover load share of the front motor pair comes from the moment balance: (x_rear − x_cg)/(x_rear − x_front).

## Aerodynamics (`aero.ts`)

**Reynolds number** Re = ρ V c̄ / μ on the MAC at cruise. The wing's maximum lift is looked up at the stall-speed Reynolds number, using two passes: stall speed with cruise-Re data, then again with the resulting stall Re.

**Airfoil data.** XFOIL polar summaries from `/api/airfoils` are interpolated linearly in log(Re). Outside the table the nearest row is used (info status). With no data (still loading, or an unknown id), the engine uses typical low-Re values (cl_max 1.15, cl_α 5.9/rad; Selig et al., Summary of Low-Speed Airfoil Data) with a warning. Unit tests use hand-written values typical of SD7037 and NACA 0009 (`frontend/src/engine/__fixtures__/airfoils.ts`, clearly marked as test values).

**Lift-curve slope** (Helmbold/DATCOM; Raymer ch. 12.4; Anderson, *Fundamentals*, Helmbold's equation):

CL_α = 2πA / (2 + √(4 + (Aβ/η)²(1 + tan²Λ_t/β²))) × F·S_exp/S

Here η = cl_α/(2π/β), β² = 1 − M², Λ_t is the sweep of the maximum-thickness line and F = 1.07(1 + d/b)². Following Raymer, if F·S_exp/S exceeds 1, 0.98 is used instead. For Λ = 0 and M = 0 this equals Anderson's form a = a₀ / (√(1 + (a₀/πA)²) + a₀/πA) (tested).

The section slope cl_α is capped at 2π per radian (thin-airfoil theory; Anderson, *Fundamentals* ch. 4) before it enters the formula, for the wing and the tail. Between Re 60k and 200k the XFOIL fit over −2° to 6° can exceed 2π (SD7037: 8.57/rad at 60k, MH32 8.26, Clark Y 8.34) because a laminar separation bubble distorts the lift curve inside the fit range. That is a local kink of the section curve, not a steeper whole-wing slope; used as it stands it would overstate the wing and tail lift slopes and move the neutral point. The estimate lists this as an assumption.

**Maximum lift and stall.** CL_max = 0.9 × section cl_max × cos Λ_c/4 (Raymer ch. 12.4; ±10 %). Stall speed V_s = √(2W / (ρ S CL_max)) at the heaviest payload (Anderson, *Aircraft Performance and Design* ch. 5). Cruise lift coefficient CL = W/(qS). The cruise-to-stall ratio is V/V_s.

**cl_max as a lower bound.** Each polar row carries `cl_max_at_sweep_end`: true when the XFOIL alpha sweep ended before the section stalled, so the tabulated cl_max is only the highest value reached. When a row used for the stall lookup (either interpolation neighbour, or the clamped end row) has it set, the engine still uses that cl_max, but treats it as a lower bound: the CL_max range extends +25 % instead of +10 % on the high side (`CL_MAX_SWEEP_END_EXTRA` = 0.15, an estimate), so the stall-speed range widens on its low side (and cruise/stall on its high side), and an assumption note says so. The nominal values and the other side of each range are unchanged.

**Parasite drag: component build-up** (Raymer ch. 12.5). CD₀ = [Σ C_f·FF·Q·S_wet + Σ (D/q)] / S, × 1.10 for leakage and protuberances.

- **Skin friction.**
  - Laminar: C_f = 1.328/√Re (Blasius).
  - Turbulent: C_f = 0.455 / ((log₁₀ Re)^2.58 (1 + 0.144 M²)^0.65).
  - The two are mixed by the laminar fraction: printed prototype wing and tail 15 %, body 5 %; final carbon 35 % and 10 % (Raymer: smooth composites reach ~50 %; printed layer lines reduce this; estimate).
  - Roughness caps the turbulent Reynolds number at R_cutoff = 38.21 (l/k)^1.053, with k = 4.05 × 10⁻⁵ m for sanded and painted prints (Raymer "production sheet metal") and 5.2 × 10⁻⁷ m for moulded carbon (Raymer "smooth molded composite").
- **Form factors.**
  - Wing and tail: [1 + (0.6/(x/c)_m)(t/c) + 100(t/c)⁴][1.34 M^0.18 cos^0.28 Λ_m].
  - Raymer's Mach term was fitted at M ≳ 0.2, where it is about 1.0. Below M = 0.2 the engine holds it at its M = 0.2 value; otherwise it would claim that flying slower lowers form drag.
  - Fuselage, booms and tail tube: 0.9 + 5/f^1.5 + f/400.
- **Interference factors Q** (Raymer): wing 1.0, fuselage 1.0, conventional tail 1.05, H tail 1.08, V tail 1.03, booms 1.1 (estimate: most of each boom is clear of the wing).
- **Stopped propellers.** A stopped lift propeller lies edge-on to the cruise flow.
  - Each blade is treated as a flat plate (Cd 1.2, Hoerner) with projected height h = c sin θ + 0.12 c cos θ at 0.75 R.
  - Chord c = 0.08 D and pitch angle θ = atan(pitch / (0.75 π D)), over 0.85 R of blade.
  - This is averaged over the random stop position (× 2/π).
  - A 330 × 140 mm two-blade propeller gives D/q = 0.00167 m², about +0.004 on CD₀ per propeller for the default wing. This is an engine method with ±50 % uncertainty; the overall drag factor carries it.
  - Front tilt and rear tilt stop two propellers; quad + pusher stops four.
- **Motor pods.** A motor can is modelled as a cylinder with height 0.6 d and mean density 3500 kg/m³ (diameter from motor mass). Stopped cans count side-on at Cd 0.8; running tilted motors face-on behind the spinner at Cd 0.3 (Hoerner).
- **Landing gear.** Four round struts of diameter 6 % of the gear height (at least 4 mm), Cd 1.0 on frontal area (Hoerner, subcritical cylinder).

**Induced drag and Oswald efficiency.** CD_i = CL²/(π e A) (Anderson, *Fundamentals* ch. 5). Raymer's ch. 12.6 fit gives e:
- Straight wings: e = 1.78(1 − 0.045 A^0.68) − 0.64.
- Leading-edge sweep above 30°: e = 4.61(1 − 0.045 A^0.68)(cos Λ_LE)^0.15 − 3.1.
- Between 25° and 35° the two are blended linearly to avoid a jump, and e is clamped to 0.5–0.95.

Lift-to-drag = CL/(CD₀ + CD_i).

**Neutral point and static margin** (Nelson ch. 2; Etkin & Reid ch. 2).
- The neutral point is the lift-weighted position of the wing and tail aerodynamic centres, moved forward by the fuselage: x_np = (a_w x_acw + a_t′ x_act)/(a_w + a_t′) − c̄ Cm_α,f/(a_w + a_t′).
- The effective tail term is a_t′ = η_t a_t (S_t,pitch/S)(1 − dε/dα), with tail efficiency η_t = 0.9 (Nelson: 0.8–1.0). The tail slope a_t comes from the same Helmbold formula on the tail's aspect ratio, using the tail airfoil's cl_α.
- Downwash gradient dε/dα = 2 CL_α,w/(π A) (contract; Nelson).
- **Fuselage destabilising moment** (Multhopp's strip method as given in Nelson ch. 2): Cm_α,f = (k₂ − k₁)(π/2)/(S c̄) Σ w_f² (dε_u/dα) Δx over 40 strips.
  - (k₂ − k₁) is the apparent-mass factor of a prolate spheroid for the fuselage fineness (Lamb's values, Nelson's chart; 0.78 at fineness 4, 0.92 at 8).
  - Ahead of the wing the upwash factor is 1 + c_r CL_α/(4π x), where x is the distance ahead of the root quarter chord. This bound-vortex estimate replaces Nelson's chart (an engine simplification).
  - Over the wing root the strips are skipped. Behind it the factor is (x/l_h)(1 − dε/dα).
  - Booms and motor pods are not included (small; Phase 3 AVL bodies).
- Static margin = (x_np − x_cg)/c̄, reported in % MAC at both payloads. Moving the battery forward raises it (tested).
- **Against AVL.** On the default design AVL's neutral point (server analysis) is 16 mm, 7.2 % MAC, forward of this method's. AVL models the fuselage as a slender body, which is more destabilising than Multhopp's strips on this short, wide fuselage, and it computes the downwash at the actual tail position (the inverted V dips towards the wing wake). The server notes differences of up to about 10 % MAC on short, wide fuselages and offers no simple correction to the fuselage term, so the method is unchanged and the neutral-point band is ±10 % MAC instead (Uncertainty model, rule 5). The full analysis uses AVL's value.

## Performance (`performance.ts`)

- **Cruise power** = D V / (η_prop η_motor η_ESC) + avionics.
  - D = qS(CD₀ + CD_i).
  - η_motor = 0.85 and η_ESC = 0.95 (Gundlach ch. 7 ranges 0.80–0.90 and 0.93–0.97). The server's ESC is also 0.95; its motor model gives 0.87–0.88 at cruise for the generic motors, close enough that Tier 1 keeps 0.85.
  - η_prop comes from the propeller model below, at the cruise thrust and speed, for the propellers that fly the cruise: the tilting pair (thrust = D/2 each) in front and rear tilt, the pusher (thrust = D) in quad + pusher. It is reported as `cruise_propeller_efficiency`.
  - Avionics draw 8 W for the prototype and 25 W for the final aircraft (estimate: autopilot, GPS, radios, servos).
- **Propeller model** (`propeller.ts`, a compact port of the server's `backend/app/engine/propulsion.py`, same coefficients and sources). Fixed pitch, axial inflow (Brandt & Selig, AIAA 2011-1255; UIUC Propeller Data Site):
  - T = CT(J) ρ n² D⁴, P = CP(J) ρ n³ D⁵, J = V/(nD), η = J CT/CP.
  - Static coefficients from the pitch-to-diameter ratio (two blades): CT₀ = 0.040 + 0.150 (P/D) up to P/D 0.6 (0.130 + 0.050 (P/D − 0.6) above); CP₀ = 0.005 + 0.085 (P/D) + 0.06 max(0, P/D − 0.5)². More blades scale CT₀ by (B/2)^0.75 and CP₀ by (B/2)^0.9.
  - Fall-off with advance ratio, zero thrust at J_T = 1.1 (P/D) + 0.05: CT = CT₀(1 − 0.5x − 0.5x²), CP = CP₀ max(0.05, 1 − 0.7x²), x = J/J_T.
  - These are the server engine author's fit to the published UIUC trends for small fixed-pitch propellers (APC Slow Flyer 10×4.7: CT₀ ≈ 0.11, CP₀ ≈ 0.045, zero thrust near J 0.55, peak efficiency ≈ 0.6 near J 0.4–0.45), not a regression on the data files: ±15 % on CT and CP.
  - Operating point: with n = V/(JD), the thrust is CT(J) ρ V² D²/J², which falls steadily from very large at small J to zero at J_T, so J is found by bisection; no motor model is needed for η_prop.
  - The pusher's pitch is not in the schema; it is assumed 0.7 × diameter with two blades, as on the server (`PUSHER_PITCH_RATIO`).
  - Why it matters: in cruise the tilted hover propellers carry only half the drag each and run close to their zero-thrust advance ratio. The default 330 × 140 mm propellers at 16 m/s sit at J ≈ 0.48 against J_T = 0.52, with η ≈ 0.34; the fixed 0.65 used until Phase 3 made the cruise power about 45 % too low and the endurance far too long. A pusher chosen for cruise (P/D 0.7) runs near its peak, η ≈ 0.71.
- **Hover power** (momentum theory, Leishman ch. 2). Each rotor needs ideal power P_i = T^1.5/√(2ρA), equal to T × the induced velocity v_i = √(T/(2ρA)).
  - Thrust per rotor comes from the hover load share of each pair at the heaviest payload, × 1.03 for download on the wing and booms (estimate; Leishman notes ~10 % for tiltrotors whose wing sits under the rotor).
  - Shaft power = Σ P_i / FM with figure of merit FM = 0.65 (contract; Leishman: 0.7–0.8 for good rotors, lower for small fixed-pitch propellers).
  - Electrical power = shaft / (η_motor η_ESC) + avionics.
- **Transition power** = 1.25 × hover motor power + avionics (contract, stated assumption; Phase 3 models the transition).
- **Peak current** = transition power / nominal pack voltage. C-rate = peak current / pack Ah.
- **Mission profile** (contract): take-off hover 45 s, transition 15 s, cruise, transition 15 s, landing hover 45 s.
  - Usable energy = nominal × (1 − reserve from settings) × 0.95.
  - Cruise time = (usable − VTOL energy) / cruise power.
  - Range = cruise time × cruise speed, in still air.
  - Endurance is also reported at the lightest payload.
- **Disc loading** = hover thrust / total disc area.
- Hover thrust-to-weight is shown as assumed, equal to the settings minimum, because the motors are sized to it (real parts arrive in Phase 4).

## Checks (`checks.ts`)

All Tier 1 checks are informative. Thresholds come from the settings document, whose sources are in `app/defaults.py` and shown on the Settings page, except where noted.

| Check | Rule |
|---|---|
| Take-off mass | fail above the legal limit (25 kg) or the design limit (24 kg); warn from the warning mass (23 kg) or when the high end of the range exceeds the design limit |
| Mass against target | warn when the build-up differs from the mission target by more than 15 %, info otherwise |
| Mass convergence | warn if the fixed point did not converge |
| Static margin, both payloads | fail below 0 (unstable); warn outside settings min–max (5–20 %); ok inside |
| Hover balance, both payloads | fail if the CG is outside the motors; warn if one pair carries more than 65 % |
| Cruise / stall | fail below 1; warn below the settings minimum (1.3) |
| Cruise CL | warn above 70 % of CL_max (engine decision) |
| Tail volumes | warn if V_H is outside 0.3–0.8 or V_V is below 0.02 (Raymer table 6.4: homebuilt 0.50 / 0.04, sailplane 0.50 / 0.02) |
| Endurance | ok at or above the mission target; warn below it; fail if the usable energy does not cover the VTOL phases |
| Battery C-rate | fail above a typical rating (LiPo 25 C, Li-ion 3 C, **placeholders until Phase 4**); warn above the settings fraction (80 %) of it |
| Hover T/W | info: "assumed, motors sized to the settings minimum" |
| Notes | battery outside the fuselage; airfoil data missing or Re outside the table; structure fraction outside 18–45 % (typical 25–35 %, Gundlach ch. 8); range with the EU A3 / IAA BVLOS rules; prototype scale used for a heavy aircraft |

Input validation turns degenerate input into `fail` statuses with no numbers. Examples: non-positive span, chords, fuselage, booms, tail, cruise speed, propeller or battery; non-finite positions; sweep of 60° or more or dihedral of 45° or more; V-tail angle outside 0–85°; fuselage at least as wide as the span; rear motor not behind the front motor; reversed payload range; wiring fraction outside 0–0.5. A tip chord larger than the root is a warning.

## Uncertainty model (`quantity.ts`)

Each range is roughly a "one standard deviation" band: the number is unlikely to be outside it, but it is not a guaranteed bound.

1. **Geometry** taken directly from your parameters is exact (low = high).
2. **Mass.** Each component has a relative uncertainty (table above). Structure items share one method, so their errors are added linearly. All other items are independent and combined by root-sum-square: σ_M = √((Σ_struct u_i m_i)² + Σ_other (u_i m_i)²). For the default design this gives about ±11 %. The CG range uses the same rule on the moments (x_i − x_cg) u_i m_i / M.
3. **Simple power laws** use first-order propagation (ISO GUM style): for y ∝ Π x_i^a_i, the relative range is √Σ(a_i u_i)². Example: stall speed ∝ W^0.5 CL_max^−0.5, so ±√((0.5 u_M)² + (0.5 × 0.10)²).
4. **Power, endurance and range** use one-factor-at-a-time sensitivity. The closed-form performance model runs with each factor at its low and high end while the others stay nominal. The downward deviations are combined by root-sum-square, and so are the upward ones, so the range can be lopsided. The factors are:

| Factor | Range | Basis |
|---|---|---|
| Drag (CD₀ and CD_i together) | ±15 % | Contract example; component build-ups are typically within 10–20 % |
| Mass | ±σ_M/M from rule 2 | |
| Battery energy | ±5 % | Contract example; cell spread and temperature |
| Figure of merit | ±0.05 | Leishman band for small rotors |
| Motor × ESC efficiency (and the hover chain) | ±7 % | Estimate |
| Cruise propeller CT | ±15 % | As the server (`PROP_UNCERTAINTY`): trend fit, not a regression on the UIUC files |
| Cruise propeller CP | ±15 % | As above |

The drag and mass factors also move the cruise thrust, so the propeller efficiency is re-solved for each.

5. **Other single ranges:** neutral point ±10 % MAC, combined with the CG range for the static margin; CL_max ±10 % (Raymer); lift-curve slope ±8 %; CD₀ ±15 %; Oswald ±0.05. The neutral-point band was ±5 % MAC in Phase 2. The Phase 3 AVL analysis put the default design's neutral point 7.2 % MAC forward of Tier 1's, and the server notes up to about 10 % MAC between AVL's slender-body fuselage and Multhopp's strips on short, wide fuselages, so ±5 % understated it (see "Neutral point and static margin"). This is an estimate, not a fitted statistic.

## Layout comparison (`compare.ts`)

`compareLayouts` re-runs the full estimate for front tilt, rear tilt and quad + pusher. To compare layouts rather than balance shifts, it adapts the design the way a designer would:
- The tilt axis keeps its offset from the tilting motors, so rear tilt moves it to the rear pair.
- The battery is moved, within the fuselage behind the nose bay, so the balance point with the heaviest camera matches your current design's.

Each change is listed in `adjustments`. The result gives mass, endurance, cruise and hover power, a complexity rating with reasons (tilt mechanism, extra motor, transition simplicity, stopped propellers) and the ArduPilot note. The rear-tilt note reads "Less common in ArduPilot than front tilt" (owner decision, Phase 2).

In Tier 1, front tilt and rear tilt give the same numbers once re-balanced: the same parts and two stopped propellers either way. Their real differences are propeller wash over the wing, transition behaviour and ArduPilot support, which Phase 3's transition model and the complexity rating cover. Quad + pusher comes out a little heavier (extra motor, ESC and propeller against the tilt mechanism) and has more cruise drag (four stopped propellers), but it needs much less cruise power: its pusher runs near peak efficiency (≈ 0.7) while the tilted hover propellers run lightly loaded (≈ 0.34 on the default design). The server analysis agrees (default design: 110 W against 204 W).

## Golden fixtures

`shared/fixtures/tier1_cases.json` holds three cases, each with its full parameters and computed geometry numbers:
- `default_prototype`: the new-project default, battery at 290 mm.
- `final_24kg`: lengths × (24/2.5)^⅓ of the Phase 2 document (battery 380 mm → 807.6 mm), 35 mm booms, Li-ion 12S8P, 20 m/s, 1.5–4 kg payload. This is a test starting point; the engine re-runs everything.
- `quad_pusher`: the default with the quad + pusher layout (battery 290 mm, not re-balanced).

The geometry numbers are wing, tail, fuselage, booms, rotors, hinge and gear. `frontend/src/engine/fixtures.test.ts` rebuilds the geometry from the stored parameters and compares to 0.1 %. Run `UPDATE_FIXTURES=1 npx vitest run src/engine/fixtures.test.ts` after an intended geometry change; Phase 3's Python port must then follow.

## Reference outputs (fixture designs, test airfoil values)

These are printed by `ENGINE_SUMMARY=1 npx vitest run src/engine/summary.test.ts --disableConsoleIntercept`. They use the hand-written test airfoil values, so the app's numbers will differ slightly once the XFOIL tables are loaded.

| | Default prototype (front tilt) | Quad + pusher | 24 kg final scale (front tilt) |
|---|---|---|---|
| Take-off mass (heaviest camera) | 3.39 kg (3.03–3.76) | 3.36 kg (3.00–3.72) | 22.1 kg (20.0–24.3) |
| Battery | 0.77 kg at 290 mm, 111 Wh LiPo 6S1P | same | 7.8 kg, 1555 Wh Li-ion 12S8P |
| CG (heaviest / lightest camera) | 339 / 359 mm (18 / 27 % MAC) | 373 / 396 mm | 736 / 805 mm |
| Static margin (heaviest / lightest) | 22.7 % / 13.8 % MAC | 7.5 % / −2.7 % MAC | 19.7 % / 5.0 % MAC |
| Stall speed | 11.3 m/s (10.4–12.1) | | |
| CD₀, L/D | 0.045, 9.2 | 0.055, 7.7 | 0.035, 10.5 |
| Cruise propeller efficiency | 0.34 at J 0.48 | 0.71 at J 0.54 | 0.30 at J 0.48 |
| Cruise power | 218 W (179–260) at 16 m/s | 128 W (96–164) | 1.72 kW (1.41–2.04) at 20 m/s |
| Hover power | 432 W (354–516) | 420 W (344–502) | 3.3 kW (2.8–4.0) |
| Wing-flight endurance | 19.0 min (15.3–23.3) | 32.7 min (24.0–42.6) | 37 min (30–45) |

The default "2.5 kg" starting values come out at about 3.4 kg once every part is counted: lift motors around 100 g each, sized for 2:1 thrust, plus a 400 g camera and a 770 g battery. The engine flags this against the 2.5 kg target. Plausibility against published small QuadPlanes and against the 24 kg class is discussed in the Phase 2 report.

Until Phase 3 these numbers used a fixed cruise propeller efficiency (0.65 tilt, 0.75 pusher): 117 W and 36 min for the default, 810 W and 79 min at 24 kg. The tilt-layout endurances roughly halved with the propeller model, which is the main lesson: hover-sized propellers are poor cruise propellers.

**Default battery position.** New projects put the battery at 290 mm (it was 380 mm). At 380 mm AVL gives the default design a static margin of −2.5 % MAC with the lightest camera (unstable), and the server recommended moving the battery about 90 mm forward. At 290 mm both engines give a positive margin at both payloads: AVL 16.7 % / 7.5 % MAC, Tier 1 22.7 % / 13.8 % (Tier 1 flags the heaviest-camera value as above the 20 % maximum; its neutral point is the further aft of the two, and AVL's is inside the band). Quad + pusher with the same battery position is still unstable with the lightest camera in AVL (1.5 % / −9.1 % MAC); the layout comparison re-balances it by moving the battery.

## Tier 1 vs Tier 2

The instant numbers (Tier 1, in the browser on every edit) and the full analysis (Tier 2, Analyse on the server: AVL, XFOIL, the motor and propeller model, the battery model and the transition sweep) share the geometry and the mass model, so mass and balance agree exactly. They differ where Tier 1 uses a textbook shortcut. The analysis panel lists each difference with its reason (`tier1_comparison` in the result). On the default design (battery 290 mm, sea level, 16 m/s):

| | Tier 1 | Tier 2 (fast mode) | Why |
|---|---|---|---|
| Cruise power | 218 W | 204 W | Same propeller curves; Tier 1's CD₀ is about 11 % higher (flat-plate friction × form factor for the wing and tail against XFOIL strip-integrated profile drag) and its motor efficiency 0.85 against the motor model's 0.88 |
| Wing-flight endurance | 19.0 min | 20.7 min | Follows the cruise power; Tier 2 also includes battery sag and the modelled transition |
| Static margin (heaviest / lightest) | 22.7 / 13.8 % MAC | 16.7 / 7.5 % MAC | AVL's neutral point is about 16 mm (7 % MAC) further forward (slender-body fuselage, downwash at the real tail position) |
| Hover power | 432 W | 417 W | Figure of merit 0.65 and motor 0.85 against the propeller's static coefficients and the motor model |

Quad + pusher (battery 290 mm): Tier 1 128 W and 32.7 min against Tier 2 111 W and 38.6 min. The pusher propeller efficiency is the same in both (0.71); the difference is the drag (Tier 1 4.28 N against 3.79 N), which near the propeller's peak efficiency passes straight into the power.

When to trust which:
- Use Tier 1 for direction and size while editing: which way a change moves mass, balance, drag and endurance, and roughly by how much. Since the propeller model was added its cruise power is no longer optimistic; it is now slightly pessimistic because of its drag build-up.
- Use Tier 2 for decisions: stability (AVL neutral point), cruise power and endurance, transition margins, battery current and structure. Expect Tier 1 cruise power and endurance within about 10–15 % of Tier 2, and Tier 1 static margin up to about 10 % MAC higher (it sits forward of AVL's neutral point on short, wide fuselages, which is why its band is ±10 % MAC).
- The numbers differ most for designs far from the default: unusual fuselages (neutral point), very different propeller pitch or cruise speed (propeller operating point), or a heavy reliance on the tail's lift.

## What Phase 3 replaces

| Tier 1 method (browser) | Phase 3 replacement (server) |
|---|---|
| Helmbold/DATCOM lift slope, 0.9 × cl_max rule | AVL vortex lattice (CL_α, span loading); XFOIL cl_max at the root, MAC and tip Reynolds numbers |
| Raymer Oswald fit, CL²/(πeA) | AVL induced drag (CD_i, span efficiency) at the trimmed cruise point |
| Neutral point from wing + tail + Multhopp fuselage; downwash 2CL_α/(πA) | AVL neutral point (Xnp), Cm_α, stability derivatives, trim elevator |
| Flat-plate skin friction × form factors for wing and tail profile drag | XFOIL profile drag strip-integrated with AVL's local CL |
| FM 0.65, η_motor 0.85 (the cruise η_prop already uses the same generic CT(J), CP(J) curves as the server) | Motor model (Kv, R, I₀) matched to CT(J), CP(J) propeller curves at hover and cruise separately, with battery sag |
| Transition = 1.25 × hover for 15 s | Transition speed sweep with tilt schedule or pusher thrust, thrust margin, peak power |
| Nominal pack voltage, typical C ratings | Battery model with internal resistance, voltage sag, peak current against continuous and burst ratings |
| Bending-sized spar caps or a guessed tube | Spar and boom bending checks at load factor × safety factor for the chosen tube |
| Statistical motor/ESC/propeller/battery masses | Real parts (Phase 4) |
| Printed and carbon areal densities | Calibration with built weights (Phase 6) |

### Transition and top-speed thrust margins (server, `transition.py`)

The sweep runs from 0 to 1.3 × cruise speed but carries two separate margins, each with its own check:

- **Transition thrust margin** (`transition.min_margin`, `min_thrust_margin`, check `transition_margin`, threshold `checks.transition_thrust_margin_min`): available / required thrust of the busiest rotor group from hover to the end of the transition, `transition_end_speed_mps` = 1.1 × the speed at which the wing alone carries the weight at the transition attitude (1.2 V_s). The tilt reaches its limit at or before that speed. The 10 % speed buffer (21 % on lift) is an engine assumption: a speed undershoot at the end of the transition should not hand weight back to rotors that are already tilted. In `points[]`, `thrust_margin` is this margin and is `null` above the transition range (`phase: "wing_borne"`), so the chart, its minimum marker and the check use the same numbers.
- **Top-speed thrust margin** (`transition.top_speed`, `transition.top_speed_margin`, summary `top_speed_thrust_margin`, check `top_speed_margin`): on every wing-borne point of the sweep, the forward thrust of the cruise propellers at full throttle (tilting pair at the tilt limit, or the pusher) / the forward force needed (drag, plus m·a below cruise speed), per point `forward_thrust_margin`. Warning below 1.15 (the ±15 % thrust-coefficient uncertainty of the generic propeller model); never a fail, because the cruise-speed check (`cruise_thrust`) covers flight at cruise. `top_speed_mps` is where the margin reaches 1.0. For tilt layouts the limit is usually propeller unloading: a low-pitch hover propeller's thrust falls to zero as the advance ratio J = V/(nD) approaches about 1.1 P/D + 0.05, and at full throttle the motor cannot spin it fast enough to keep J low.

Before this split the reported minimum was taken only up to cruise speed while the chart showed the whole sweep. On the default design it reported 2.16 at 0 m/s while the curve fell to 1.01 at 20.8 m/s (1.3 × cruise). Now the transition margin is 2.16 at 0 m/s (hover to 14.7 m/s, check ok). The top-speed margin is 1.01 at 20.8 m/s (check warn: the 13 × 5.5 in hover propellers run at J = 0.48 against a zero-thrust J of 0.52). The estimated top speed is 20.8 m/s.

## References

- D. P. Raymer, *Aircraft Design: A Conceptual Approach*, 6th ed., AIAA, 2018: ch. 4 (wing geometry), ch. 6 (tail volume coefficients), ch. 7 (wetted area), ch. 12 (lift-curve slope, maximum lift, parasite-drag build-up, skin friction, form factors, interference, Oswald efficiency), ch. 15 (weights).
- J. D. Anderson, *Fundamentals of Aerodynamics*, 6th ed., 2017 (Helmbold's equation, induced drag); *Aircraft Performance and Design*, 1999, and *Introduction to Flight* (stall speed; the CP-1 example data used in the stall and induced-drag tests).
- J. G. Leishman, *Principles of Helicopter Aerodynamics*, 2nd ed., Cambridge, 2006: ch. 2 (momentum theory, figure of merit, download).
- R. C. Nelson, *Flight Stability and Automatic Control*, 2nd ed., 1998: ch. 2 (static stability, tail contribution, downwash, Multhopp's fuselage method). B. Etkin and L. D. Reid, *Dynamics of Flight*, 3rd ed., 1996: ch. 2 (neutral point).
- J. Gundlach, *Designing Unmanned Aircraft Systems*, 2nd ed., AIAA, 2014: ch. 7 (electric propulsion efficiencies, batteries), ch. 8 (structure mass fractions).
- S. F. Hoerner, *Fluid-Dynamic Drag*, 1965: flat plates, cylinders, struts.
- J. B. Brandt and M. S. Selig, "Propeller Performance Data at Low Reynolds Numbers", AIAA 2011-1255 (UIUC propeller database).
- E. Torenbeek, *Synthesis of Subsonic Airplane Design*, 1982 (lifting-surface wetted area).
- E. Purser and J. P. Campbell, NACA Report 823, 1945 (V-tail effectiveness).
- M. S. Selig et al., *Summary of Low-Speed Airfoil Data*, vols. 1–3, 1995–97 (low-Re airfoil trends behind the generic and test values).
- O. Gur and A. Rosen, "Optimizing Electric Propulsion Systems for Unmanned Aerial Vehicles", J. Aircraft 46(4), 2009; D. Lundström, K. Amadori, P. Krus, "Validation of Models for Small Scale Electric Propulsion Systems", AIAA 2010-483 (form of the motor-mass relation only).
- ICAO Doc 7488 / ISO 2533 (standard atmosphere).

## Known limitations

- Sea level, still air, no temperature or altitude effect.
- Wing twist and incidence do not affect Tier 1 numbers.
- Flaps and control-surface deflections are not modelled.
- Dihedral does not change lift.
- Booms, motor pods and the tail tube do not enter the neutral point, and the fuselage term (Multhopp) is less destabilising than AVL's slender body on short, wide fuselages: hence the ±10 % MAC band.
- The cruise propeller model has no motor: the motor and ESC efficiencies are fixed, and whether the motor can reach the cruise rpm is only checked in the full analysis.
- No propeller-wash effects on the wing or tail.
- Front tilt and rear tilt differ only in the complexity rating.
- Equation and chapter references follow the editions above. Raymer's chapter 12 numbering is stable across recent editions, but check your copy.
