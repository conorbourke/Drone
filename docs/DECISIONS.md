# Decisions

The brief lists five decisions for Claude Code to confirm with the owner before building. Phase 1 could not wait on all of them, so each one below records the default that was taken, the reasoning, and what the owner can change. Items marked **needs owner confirmation** are not final.

## 1. Hosting provider — Fly.io (confirmed by the owner, 9 Oct 2026)

**Decision:** Fly.io, one machine in `lhr` (London, the nearest region to Ireland), a 3 GB persistent volume at `/data` for the SQLite database, uploaded files and backups, automatic HTTPS.

**Why Fly.io:**
- Runs an ordinary Docker image, so the compiled Fortran tools (AVL, XFOIL) and CadQuery that Phase 3 and Phase 5 need are no problem.
- Persistent volumes, so one container holds the database and files without a separate database service.
- Everything is driven from GitHub Actions with a single API token, so the owner never installs anything. The owner's only steps are in a browser (create a Fly account, create a personal access token in the Fly dashboard, paste secrets into GitHub, click "Run workflow"). The container image is built on the GitHub runner, so the deploy never depends on Fly's remote builder, which Fly removes after idle periods and which only a laptop CLI session could restore.
- A single always-on small machine is cheap enough that the app never needs to stop when idle, so there are no cold starts and the daily backup always runs.

**Cost estimate (Fly.io pricing update effective 1 October 2026, see sources):**

| Item | Rate | Monthly |
|---|---|---|
| shared-cpu-1x machine, 256 MB base | $2.19 / month | $2.19 |
| extra RAM to reach 1 GB (0.75 GB) | $6.00 / GB / month | $4.50 |
| 3 GB volume | $0.15 / GB / month | $0.45 |
| Egress (Europe) | $0.02 / GB | pennies |

Roughly $7–8 (≈ €7) a month always on. Phase 3 (AVL/XFOIL) and Phase 5 (CadQuery) will likely need 2 GB of RAM, taking it to about $14 (≈ €13) a month. Both are inside the €10–30 budget. Fly's daily volume snapshots (14 kept) add well under a euro. Pay-as-you-go Claude API usage is separate.

**Alternatives considered:** Railway (similar, slightly less control over volumes), Render (persistent disk only on paid plans), Hetzner VPS (about €4 a month, but a self-managed server), Oracle Cloud's Always Free ARM VM (genuinely free, but a self-managed server with a halved allowance since August 2026 and frequent capacity shortages), and Cloudflare (its free plan cannot run the compiled aerodynamics and CAD tools; Cloudflare Containers need the $5 a month paid plan and have no persistent disk). The owner chose Fly.io. The Dockerfile is provider-neutral, so switching later is a matter of a new deploy workflow.

**Owner can change:** region, machine size and the app name are repository variables and `fly.toml` values; see `docs/DEPLOYMENT.md`.

Sources: [Fly.io 2026 pricing update](https://fly.io/pricing-update/), [Fly.io resource pricing](https://fly.io/docs/about/pricing/).

## 2. Rear-tilt layout — enabled as a layout choice, flagged "less common" (needs owner confirmation in Phase 2)

ArduPilot's QuadPlane tiltrotor support uses `Q_TILT_MASK`, a bitmask that says which motors tilt; any motor can be included, so a quad with only the rear pair tilting is configurable. The official guide lists front-pair tilt and all-four tilt as the common setups, and forum threads show people flying rear-tilt quads but also hitting setup pitfalls (tilt type, servo assignment). The data model therefore keeps `rear_tilt` as a first-class layout. Before Phase 2 enables it as a full option, Claude Code should confirm the exact parameter set with ArduPilot's documentation and ideally a SITL (software-in-the-loop) run, and the UI should carry a note that rear tilt is less common in the ArduPilot community than front tilt.

Sources: [ArduPilot tilt rotor guide](https://ardupilot.org/plane/docs/guide-tilt-rotor.html), [forum: QuadPlane rear tilt rotors](https://discuss.ardupilot.org/t/quadplane-rear-tilt-rotors-tiltin-problem/79576), [forum: Q_TILT_MASK questions](https://discuss.ardupilot.org/t/q-tilt-mask-misc-vtol-questions/27430).

## 3. Assistant: advise only, or "try this as a new version" — proposed: both, with the button creating a clearly labelled version (needs owner confirmation before Phase 3)

Proposed default: the assistant can propose a change and the UI offers a "Try as new version" button that creates a version named after the suggestion. The assistant never edits the draft directly. This keeps the owner in control and makes every assistant idea comparable with the version tools. Nothing in Phase 1 depends on this choice; the version model already supports it (`parent_version_id`).

## 4. Default thresholds for the checks (proposed, needs owner confirmation before Phase 3)

These are stored in Settings with a description and source, and are editable in the browser.

| Check | Proposed default | Reasoning and source |
|---|---|---|
| Hover thrust-to-weight minimum | 2.0 | Common multirotor/VTOL design rule: hover at or below about 50 % throttle leaves authority for gusts, descent control and a motor-out margin. To be cited precisely in Phase 3 (ArduPilot QuadPlane tuning guidance recommends hover throttle well below half). |
| Static margin range | 5 % to 20 % of mean aerodynamic chord | Standard fixed-wing stability guidance (Raymer, *Aircraft Design: A Conceptual Approach*; typical RC/UAV practice 5–15 %). Too low is unstable, too high is sluggish and trim-draggy. |
| Cruise-to-stall speed ratio minimum | 1.3 (cruise speed at least 1.3 × stall speed) | The 1.3 × stall approach/manoeuvre margin is standard in piloted aviation and widely used for UAV cruise margin. |
| Battery reserve fraction | 20 % | LiPo and Li-ion packs should not be run below about 20 % remaining; also gives a landing reserve. |
| Battery current, maximum fraction of rating | 0.8 (peak draw ≤ 80 % of continuous rating) | Keeps the pack inside its continuous discharge rating with headroom for ageing and cold. |
| MTOW design limit / legal limit / warning | 24 kg / 25 kg / 23 kg | From the brief (EU Open A3 upper limit 25 kg, 1 kg safety margin). |

## 5. Published designs for the validation suite (needs owner confirmation before Phase 3)

Candidates will be proposed at the start of Phase 3. Criteria: published span, weight, cruise speed, battery, and measured endurance or power; preferably a QuadPlane flown with ArduPilot. Nothing in Phase 1 depends on this.

## Other defaults taken in Phase 1

- **Database:** SQLite on the persistent volume, with daily backups the owner can download in the browser. One user and modest data make a separate database server unnecessary; SQLAlchemy keeps a later move to Postgres cheap.
- **Login:** a single password set as a server secret. Sessions are signed cookies that last 30 days. Five failed attempts lock the login for 15 minutes.
- **Design parameter defaults:** a plausible 2.5 kg front-tilt prototype (1800 mm span). These are starting values for a new project, not an analysed design; the engine arrives in Phases 2 and 3.
- **Parts database:** structure only. The seed contains a few example rows that are clearly marked unverified; Phase 4 seeds real components with sources.
