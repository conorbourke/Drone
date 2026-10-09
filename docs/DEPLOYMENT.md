# Deployment

Everything runs on one small Fly.io machine. The owner never installs anything: every step below happens in a web browser, and GitHub's servers build and deploy the app.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| Web app (the pages you use) | Served by the API process from the same container | Built from `frontend/` |
| API and engineering engine | One Fly.io machine in London (`lhr`), 1 GB RAM | Built from `backend/` |
| Database, uploaded files, backups | A 3 GB persistent volume mounted at `/data` | SQLite file plus daily in-app backups |
| Disaster-recovery copies | Fly.io daily volume snapshots, 14 kept | Restored by Claude Code if ever needed |
| Secrets (password, signing key, Claude API key) | Fly.io secrets, set from GitHub secrets | Never in the repository or the browser |

## One-time setup (about 15 minutes, all in the browser)

1. **Create a Fly.io account** at https://fly.io. Fly asks for a payment card; expected cost is about €7 a month (see Costs).
2. **Create an API token.** In the Fly.io dashboard open *Tokens* and create a token for your organisation (the default organisation is called *personal*). Copy it: it is shown once. Do not create a "deploy token" for a single app: that kind cannot create the app in the first place.
3. **Add secrets to GitHub.** In this repository on GitHub go to *Settings → Secrets and variables → Actions → New repository secret* and add:
   - `FLY_API_TOKEN`: the token from step 2.
   - `APP_SECRET_KEY`: any long random string (40 or more characters; a password manager can generate one). It signs your login session. Keep it private; changing it later signs you out everywhere.
   - `APP_PASSWORD`: the password you will log in with. Any characters are fine, including `#`, quotes and spaces. Keep it under 72 bytes: up to 72 plain letters, digits or symbols, fewer if you use accented or non-Latin characters.
   - `ANTHROPIC_API_KEY` (optional until Phase 3): your Claude API key.
4. **Optional variables** (same page, *Variables* tab): `FLY_APP_NAME` if you want a different app name. The name becomes the address `https://<name>.fly.dev` and must be unique across Fly.io; the default is `vtol-drone-designer`. `FLY_ORG` if your token belongs to an organisation other than *personal*.
5. **Run the deploy.** Open the *Actions* tab, choose *Deploy to Fly.io*, press *Run workflow*, keep the suggested branch, and press the green button. The first run takes about 5 to 8 minutes: it creates the app and its volume, stores the secrets, builds the container on GitHub's servers and starts it.
6. **Open the app.** The workflow's summary shows the address, for this project `https://conor-vtol-designer.fly.dev`. Log in with `APP_PASSWORD`.

## Every later deploy

- Pressing *Run workflow* again deploys the chosen branch.
- Pushes to a branch called `main` deploy automatically. The repository does not have one yet; when Claude Code's branch is merged into a `main` branch (or the default branch is renamed to `main` in *Settings → Branches*), every merge to it deploys on its own.
- Deploys keep the database: the volume is separate from the container. A deploy also keeps you logged in: sessions only end when the password or signing key changes.

## Changing the password, or signing out everywhere

Update the `APP_PASSWORD` (or `APP_SECRET_KEY`) secret in GitHub and run the deploy workflow. Either change invalidates every existing login session, on every device.

## Backups and restore

Two layers, with honest labels:

1. **In-app backups (undo a mistake).** Every day at 03:00 UTC the app copies its database to `/data/backups/` and keeps the newest 14. If the machine was restarted and the newest backup is older than a day, it backs up at startup. *Settings → Backups* in the app lists them, lets you make one now, and downloads any of them to your laptop. That download is your off-site copy; keep one somewhere safe before big changes. These files live on the same volume as the database, so they do not protect against the volume itself failing.
2. **Fly.io snapshots (disaster recovery).** Fly takes a daily snapshot of the whole volume and keeps 14. If the volume or its host ever fails, Claude Code restores it: either `fly volumes create data --snapshot-id <id>` followed by a redeploy, or copying your downloaded backup file into `/data` with `fly ssh sftp` and restarting. The Fly dashboard has no file upload or shell, so this is not something you need to do yourself; ask Claude Code.

## Costs

Fly.io pricing update effective 1 October 2026 (see `docs/DECISIONS.md` for sources):

| Item | Monthly |
|---|---|
| shared-cpu-1x machine with 1 GB RAM | about $6.70 |
| 3 GB volume | $0.45 |
| Snapshots and egress | pennies |

About €7 a month with 1 GB of RAM (Phases 1 and 2). From Phase 3 the machine has 2 GB for the aerodynamics and CAD tools, about €13 a month. Claude API usage is billed separately by Anthropic.

## Troubleshooting

- **"Name has already been taken" in the deploy log.** Someone else owns that Fly app name. Add the repository variable `FLY_APP_NAME` with a unique name and run the workflow again.
- **The deploy workflow fails at "Check that the required secrets exist".** Add the missing secret and re-run.
- **The deploy succeeds but the page will not load.** In the Fly.io dashboard open the app, then *Monitoring*, to read the machine's log. Copy the error into a message to Claude Code.
- **Login seems to do nothing.** Make sure the address starts with `https://`. The login cookie is only sent over HTTPS.
- **Locked out after wrong passwords.** Five wrong attempts lock the login for 15 minutes. Wait, then try again.
- **I lost the password.** Set a new `APP_PASSWORD` secret in GitHub and run the deploy workflow.

## Rotating the Fly token

In the Fly dashboard revoke the old token, create a new one, replace the `FLY_API_TOKEN` secret in GitHub. Nothing else changes.

## For Claude Code: scaling and changes

- Memory: change `memory` under `[[vm]]` in `fly.toml` and deploy.
- Region, app name and the health check live in `fly.toml`; the deploy steps live in `.github/workflows/deploy.yml`.
- The deploy always uses `--ha=false` so there is exactly one machine. Never remove that flag: a second machine would have its own empty database.
