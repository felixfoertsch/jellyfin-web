# Patched, independently deployed Jellyfin Web

This unofficial fork follows **jellyfin/jellyfin-web master**, not stable releases.
It replays the exact application and regression-test changes from commit
`618e2153e147937f22832c2e749afffb362c6793` in upstream PR #8513:
https://github.com/jellyfin/jellyfin-web/pull/8513

All media-segment types default to **Ask to skip**. Existing saved preferences,
including **None** and **Skip**, remain authoritative. This does not install a
segment provider or create missing segment markers. Nothing is automatically
skipped just because this patch is installed.

## Update and publication model

`.github/workflows/downstream.yml` runs on pushes to master, manual dispatch, and
hourly at minute 17 UTC (GitHub may delay scheduled jobs). It:

1. Tests the source-sync tool against temporary local Git repositories.
2. Fetches current upstream master into a temporary worktree.
3. Replays the locally stored patch queue, without downloading a moving PR diff.
4. Builds the production web client with the upstream Node 24 toolchain, executes
   the specific PR regression tests, TypeScript checks, and the full Vitest suite.
5. Builds the unprivileged Nginx image and smoke-tests redirects, assets, API
   routing, stream URLs and WebSocket handshakes against a mock backend.
6. Advances this fork's master with a normal fast-forward push, then publishes
   a multi-platform image to GitHub Container Registry.

The sync is a **linear source-snapshot update**, not a force-rebase or a Git merge
of upstream history. Every upstream-owned file is replaced by upstream's current
version before our patches are applied. The exact upstream commit is recorded in
`upstream.json`, the commit message, image labels and `/web/downstream-build.json`.
GitHub's ahead/behind count is therefore NOT a source-freshness indicator.

Only `.downstream/` and `.github/workflows/` are maintained directly here. Other
source changes must be added as patches and listed in `.downstream/series`, or the
next sync will replace them. Upstream CI workflows are intentionally excluded;
we keep our own publishing workflow and do not import upstream deployment jobs.
This also avoids needing a long-lived token with workflow-write permission.

A conflicting patch, failing test or failed initial image build stops the run
without advancing master or publishing a replacement image. If publication
itself fails after the source push, the next scheduled/manual run retries it.
An exactly adopted upstream patch is detected with a reverse-application check;
its regression tests still run. Concurrent human pushes are never force-overwritten.

Identical source does not create an empty source commit. Cached image builds are
still checked hourly so base-image updates and failed publications are retried.
No Docker Hub account, personal access token, or additional Actions secret is
needed for publishing: the job uses the repository's `GITHUB_TOKEN`.

## First activation

- Enable GitHub Actions in the fork if its Actions page shows the fork-workflow
  opt-in banner. Select **Patched Jellyfin Web → Run workflow** to run immediately.
- The workflow requests `contents: write` and `packages: write`. Repository or
  organization restrictions must allow these; a protected master requiring PRs
  will prevent the sync's direct push until an appropriate exception is configured.
- GHCR makes newly published packages private by default. After the first publish,
  make the `jellyfin-web` package public in its package settings for anonymous pulls,
  or authenticate Docker with an appropriate read-packages credential.
- GitHub can disable schedules on an inactive public repository after 60 days.
  Regular upstream-sync commits normally provide activity; inspect Actions if
  updates stop. A conflict cannot be repaired automatically by dropping the patch.

## Images and rollback

```text
ghcr.io/felixfoertsch/jellyfin-web:latest
ghcr.io/felixfoertsch/jellyfin-web:edge
ghcr.io/felixfoertsch/jellyfin-web:sha-<full-source-sha>-run-<run-id>-<attempt>
```

`latest` and `edge` both follow upstream development master. Do not assume they
match the version of a stable Jellyfin server. Test playback against your server
before adopting a new image. Pin an immutable build tag or digest to control
rollout and rollback. The pipeline publishes images; it does **not** restart or
upgrade your running server or frontend containers.

Architectures: `linux/amd64`, `linux/arm64`. Internal HTTP port: `8080`.
`/healthz` checks frontend health, not backend availability.
The matching source archive is served at `/web/downstream-source.tar.gz`; the
upstream license is at `/web/LICENSE.txt`.

## Deploy beside an existing Jellyfin server

The frontend serves `/web/` and proxies **all other routes** to the existing server,
including API calls, image requests, streaming and `/socket`. It needs no media,
configuration or cache volumes from Jellyfin and creates no second database.

Set `JELLYFIN_SERVER` to the existing server's Docker DNS hostname and HTTP port,
for example `jellyfin:8096`. Do not include `http://`, a path, or credentials.
This configuration assumes Jellyfin's Base URL is empty (root deployment).
A custom Base URL such as `/jellyfin` requires a corresponding Nginx adjustment.

For a separate Compose project, use the server's existing Docker network:

```bash
export JELLYFIN_DOCKER_NETWORK=your_existing_jellyfin_network
export JELLYFIN_SERVER=jellyfin:8096
docker compose -f .downstream/compose.yaml pull
docker compose -f .downstream/compose.yaml up -d
```

The example binds `127.0.0.1:8097` on the Docker host. Point your existing trusted
HTTPS reverse proxy at that address, forwarding WebSocket upgrades and the
original host/protocol. A containerized reverse proxy can instead join the same
network and use `http://jellyfin-web:8080` directly. Configure Jellyfin's trusted
proxies for your actual network. Do not publish the backend HTTP port openly.

To add this to the server's existing Compose file, copy just the `jellyfin-web`
service and use the same Docker network as the server. No second Jellyfin server
container or media mounts are required. The bundled frontend can stay enabled as
a private fallback. Only clients using this web frontend receive this change.
Native clients and clients bundling their own web assets are unaffected.

The image runs unprivileged and the example uses a read-only root filesystem,
with writable temporary filesystems for `/tmp` and rendered Nginx configuration.
Access logging is disabled to reduce accidental logging of tokens in request URLs;
Nginx error logs may still contain request details, so handle logs accordingly.

## Local validation and builds

```bash
python3 -m unittest discover -s .downstream/tests -v
# On a clean checkout only; prepares a local source-sync commit:
python3 .downstream/sync-upstream.py

docker build -f .downstream/Dockerfile -t jellyfin-web-local .
bash .downstream/smoke-test.sh jellyfin-web-local
```

`sync-upstream.py` refuses dirty working trees, applies patches in an isolated
worktree, and never pushes anything itself. The CI's push step happens only after
the initial image build and smoke tests succeed. Re-run a failed workflow after
fixing an incompatible patch; there is no best-effort mode that omits customizations.
