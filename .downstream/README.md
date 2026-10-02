# Patched, independently deployed Jellyfin Web

This unofficial fork publishes **two independent channels in the same GHCR package**:

| Image tag | Upstream source |
| --- | --- |
| `latest`, `stable` | Latest officially published non-prerelease Jellyfin Web release, plus our patch |
| `nightly`, `edge` | Latest commit on `jellyfin/jellyfin-web` master, plus our patch |
| Numeric version, e.g. `12.1` | That official release, with the downstream patch/tooling used when built |

**Migration:** `latest` previously followed development master. It switches to the
release channel after the first successful release-channel publication. Use
`nightly` (or the retained `edge` alias) to continue following development builds.
No running container is restarted automatically. A separate package/visibility
setting is unnecessary; both channels use `ghcr.io/felixfoertsch/jellyfin-web`.

The exact application and regression-test changes come from commit
`618e2153e147937f22832c2e749afffb362c6793` in upstream PR #8513:
https://github.com/jellyfin/jellyfin-web/pull/8513

All media-segment types default to **Ask to skip**. Existing saved preferences,
including **None** and **Skip**, remain authoritative. This does not install a
segment provider or create missing segment markers. Nothing is automatically
skipped just because this patch is installed.

## Update and publication model

`.github/workflows/downstream.yml` runs on pushes to master, manual dispatch, and
hourly at minute 17 UTC (GitHub may delay scheduled jobs). Two independent matrix
jobs run with fail-fast disabled. Each job:

1. Tests source synchronization, release selection and channel isolation.
2. Selects its upstream ref: the release job queries the upstream GitHub
   `releases/latest` endpoint, rejects drafts/prereleases and fetches the **exact
   release tag**, never the release's `target_commitish`. Nightly fetches master.
3. Reconstructs that upstream source in an isolated worktree and replays the same
   locally stored patch queue. It never downloads a moving PR diff.
4. Checks whether the exact patched source tree and all channel aliases already
   point to the same published AMD64/ARM64 image. Unchanged sources are skipped;
   missing images, missing aliases and failed publications are retried. The
   marker changes weekly so base images are refreshed even without source changes.
   Manual **Run workflow -> rebuild** forces a rebuild at any time.
5. When building, runs the specific PR regression tests, TypeScript checks, the
   full Vitest suite, and production compilation using the upstream Node 24
   toolchain. The unprivileged Nginx image is then smoke-tested for redirects,
   assets, API routing, stream URLs and WebSocket handshakes against a mock backend.
6. Publishes validated source: nightly advances the fork's master using a normal
   fast-forward push; release creates a unique `downstream-release-v<version>-<sha>`
   Git tag. **Release builds never push release source into master.**
7. Publishes a multi-platform image to GHCR. Release jobs can write only
   `latest`/`stable`/the numeric version and release-specific build tags. Nightly
   jobs write only `nightly`/`edge` and nightly-specific build tags.

The source reconstruction is a **linear source-snapshot update**, not a
force-rebase or a Git merge of upstream history. Every upstream-owned file is
replaced by the selected upstream revision before applying patches. The exact
upstream ref and commit are recorded in `.downstream/upstream.json`; image labels
and `/web/downstream-build.json` also identify the channel, version and source.
GitHub's ahead/behind count is therefore NOT a source-freshness indicator.

Only `.downstream/` and `.github/workflows/` are maintained directly here. Other
source changes must be added as patches and listed in `.downstream/series`, or the
next sync will replace them. Upstream CI workflows are intentionally excluded;
we keep our own publishing workflow and do not import upstream deployment jobs.
No Docker Hub account, long-lived personal access token, or additional Actions
secret is needed: publishing uses the repository's `GITHUB_TOKEN`.

A conflicting patch, failing test or failed initial build stops **that channel**
without publishing a replacement image. The other channel can still succeed.
If publication fails after its source push/tag, the next scheduled/manual run
retries it. An exactly adopted upstream patch is detected with a reverse-apply
check; regression tests remain required. Concurrent human pushes to master are
never force-overwritten. Workflow-level concurrency prevents old and new runs
from publishing competing rolling tags.

Release build skipping uses the full patched **tree**, not its commit parent or
timestamp: ordinary nightly source updates do not force a rebuild of an unchanged
release. Changes to the patch queue, build recipes or trusted workflows do.

## Activation and permissions

- Enable GitHub Actions if the fork's Actions page shows the opt-in banner.
  Select **Patched Jellyfin Web -> Run workflow** to check both channels immediately.
- The jobs request `contents: write` and `packages: write`. Repository/organization
  policies must permit those, master updates and `downstream-release-*` source tags.
- Both image channels belong to the existing `jellyfin-web` GHCR package and inherit
  its visibility. For anonymous pulls, make the package Public; otherwise
  authenticate Docker with an appropriate read-packages credential.
- GitHub can disable schedules on an inactive public repository after 60 days.
  Inspect Actions if updates stop. A conflict cannot be repaired by dropping a patch.

## Images, version selection and rollback

```text
ghcr.io/felixfoertsch/jellyfin-web:latest
ghcr.io/felixfoertsch/jellyfin-web:nightly
ghcr.io/felixfoertsch/jellyfin-web:12.1
ghcr.io/felixfoertsch/jellyfin-web:release-sha-<source-sha>-run-<run-id>-<attempt>
ghcr.io/felixfoertsch/jellyfin-web:nightly-sha-<source-sha>-run-<run-id>-<attempt>
```

The numeric tag above is an example; each official version gets its own tag when
it is built as the latest release. There is no automatic historical backfill.
Numeric tags can move when the downstream patch/tooling or base images change;
use a unique build tag or digest for an exact deployment/rollback. Internal
`release-tree-*` and `nightly-tree-*` tags track successful publication and weekly
base-image refreshes; they are not the recommended deployment interface.
Existing `sha-...-run-...` tags from the original workflow are not deleted.

`latest` means the latest official **Web** release plus our patch, not guaranteed
compatibility with every older Jellyfin server. Match the Web/server release
series and test playback before adopting a new image. `nightly` is development
code. The workflow publishes images; it does **not** restart or upgrade running
server/frontend containers.

Architectures: `linux/amd64`, `linux/arm64`. Internal HTTP port: `8080`.
`/healthz` checks frontend health, not backend availability.
The matching source archive is served at `/web/downstream-source.tar.gz`; the
upstream license is at `/web/LICENSE.txt`.

## Deploy beside an existing Jellyfin server

The frontend serves `/web/` and proxies **all other routes** to the existing server,
including API calls, image requests, streaming and `/socket`. It needs no media,
configuration or cache volumes from Jellyfin and creates no second database.

Set `JELLYFIN_SERVER` to the server's Docker DNS hostname and HTTP port, for example
`jellyfin:8096`. Do not include `http://`, a path, or credentials. This assumes
Jellyfin's Base URL is empty. A prefix such as `/jellyfin` needs an Nginx adjustment.

For a separate Compose project, use the server's existing Docker network:

```bash
export JELLYFIN_DOCKER_NETWORK=your_existing_jellyfin_network
export JELLYFIN_SERVER=jellyfin:8096
# Default is the release channel. Set nightly, a numeric version, or a build tag.
export JELLYFIN_WEB_TAG=latest
docker compose -f .downstream/compose.yaml pull
docker compose -f .downstream/compose.yaml up -d
```

To switch only the frontend to development builds:

```bash
export JELLYFIN_WEB_TAG=nightly
docker compose -f .downstream/compose.yaml pull jellyfin-web
docker compose -f .downstream/compose.yaml up -d jellyfin-web
```

The example binds `127.0.0.1:8097` on the Docker host. Point the trusted HTTPS
reverse proxy there, forwarding WebSocket upgrades and the original host/protocol.
A containerized proxy can join the same network and use `http://jellyfin-web:8080`.
Configure Jellyfin's trusted proxies for the actual network. Do not publish the
backend HTTP port openly. Copy just the `jellyfin-web` service into the server's
existing Compose file when using one project. The bundled frontend can stay
enabled as a private fallback. Native clients and clients bundling their own web
assets are unaffected by this patch.

The image runs unprivileged with a read-only root filesystem in the example;
`/tmp` and rendered Nginx configuration use writable temporary filesystems.
Access logging is disabled to reduce logging of tokens in request URLs; Nginx
error logs may still contain request details and should be handled accordingly.

## Local validation and builds

```bash
python3 -m unittest discover -s .downstream/tests -v
# Clean checkout only; prepares a local source commit, never pushes it:
python3 .downstream/sync-upstream.py --upstream-ref refs/heads/master
# For a specific release, use its exact tag instead, e.g. refs/tags/v12.1.
docker build -f .downstream/Dockerfile -t jellyfin-web-local .
bash .downstream/smoke-test.sh jellyfin-web-local
```

`sync-upstream.py` refuses dirty working trees and applies patches in an isolated
worktree. The CI source-publish step occurs only after successful tests/builds or
verification that the identical source tree is already published for that channel.
There is no best-effort mode that omits customizations.
