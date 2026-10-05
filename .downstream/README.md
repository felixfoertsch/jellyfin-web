# Patched, independently deployed Jellyfin Web

Both channels always reconstruct upstream source and replay the same reviewed
patch queue. They are published in **one existing GHCR package**:

| Image tags | Selected upstream source |
| --- | --- |
| `latest`, `stable` | Latest official published, non-prerelease Jellyfin Web **release tag**, plus our patches |
| `nightly`, `edge` | Latest commit on `jellyfin/jellyfin-web` **master**, plus our patches |
| Numeric version, e.g. `12.1` | That official release with the downstream patches/tooling used when built |

The release channel never falls back to master. The nightly channel never writes
`latest` or `stable`. A failing patch is never silently omitted. No running
container is restarted or upgraded by this repository's workflows.

The initial application and regression-test patch is commit
`618e2153e147937f22832c2e749afffb362c6793` from upstream PR #8513:
https://github.com/jellyfin/jellyfin-web/pull/8513

All media-segment types default to **Ask to skip**. Existing saved preferences,
including **None** and **Skip**, remain authoritative. This does not install a
segment provider or create missing markers. Nothing is automatically skipped
just because this patch is installed.

## Source history: upstream plus our patches

Every synchronization starts from the selected upstream tree, preserves the
fork's `.downstream/` and `.github/workflows/`, then strictly replays every patch
listed in `.downstream/series`. An ordinary automatic merge's file resolution is
not used: old fork edits cannot silently survive outside the patch queue.

`main` is the default branch: upstream master plus **one tooling commit and one
commit per applied patch**. `automation` owns maintained tooling and the patch
queue. The default branch's scheduled workflow checks out `automation`, rebuilds
the stack from upstream, validates it, then replaces `main` using an explicit
force-with-lease. No previous fork head or sync merge is retained in the stack.
Already-upstream patches are skipped. Stable commit metadata makes identical
replays produce identical commits. The tooling commit prepends the root README
headline; each applied patch commit adds its own list item, in series order
(oldest first), using its `Subject:` or filename. Upstream README content follows
unchanged. Already-upstream patches do not appear in the applied list.

After successful catch-up, `main` is **1 + applied patches ahead, zero behind**
upstream master. Later upstream pushes or failed checks can leave it behind until
the next successful run. Edit tooling and patches on `automation`, not `main`.

Release source is reconstructed from the **exact official release tag**, including
peeling annotated tags. It is preserved under a unique
`downstream-release-v<version>-<source-sha>` Git tag; it never replaces main.
Release history is also the selected release plus tooling and applied patches.

Only `.downstream/` and `.github/workflows/` are maintained directly. Other edits
must be made as patch files and listed in `.downstream/series`, or the next sync
will replace them. Upstream workflow files remain in Git history but are not
installed in the fork's executable workflow tree.

## Detection and publication

`.github/workflows/downstream.yml` runs on main/automation pushes, manual dispatch,
`repository_dispatch` of type `upstream-updated`, and a best-effort **five-minute
poll** (`2-59/5 * * * *`, UTC). Both channels run independently with fail-fast
disabled. Workflow concurrency serializes publishers and coalesces pending
wake-ups without cancelling a running publication.

Each channel:

1. Runs offline regression tests for ancestry, patch replay, release selection,
   channel isolation, freshness gates, and image promotion.
2. Resolves its fixed upstream ref. Release selection queries upstream's
   `releases/latest`, rejects drafts/prereleases, and uses its exact tag rather
   than `target_commitish`. Nightly selects `refs/heads/master`.
3. Reconstructs upstream plus the locally stored patch queue. Dispatch payloads
   never supply a repository, source ref, patch, or shell command.
4. Skips compilation when the exact source tree and all channel aliases already
   point to the same AMD64/ARM64 index. Missing/partial publications are retried;
   a weekly marker refreshes base images. Manual `rebuild` forces a new build.
5. Builds/tests the production client, runs the PR regression tests, TypeScript
   checks and full Vitest suite, then smoke-tests the AMD64 frontend/proxy image.
6. Builds and uploads an AMD64/ARM64 candidate under a unique source/run tag, with
   provenance and SBOM. **No rolling tag or success marker moves at this stage.**
7. Rechecks the selected upstream ref and commit after the build. If upstream
   moved, it leaves main and rolling tags untouched and requests an immediate
   catch-up workflow through `repository_dispatch`.
8. If still current, publishes validated source (nightly main or release source
   tag), then promotes the already-built index **by digest**, without another
   compilation, to that channel's aliases and success marker. Promotion verifies
   both architectures and checks every resulting alias.
9. Rechecks upstream once more after publication. A change during promotion
   requests another catch-up run instead of waiting for the next scheduled poll.

The exact upstream ref/commit is recorded in `.downstream/upstream.json`, commit
metadata, image labels and `/web/downstream-build.json`. Release rebuild skipping
uses the source **tree**, not commit parent/time, so nightly-only history changes
do not force a release rebuild. Patch/tooling changes do.

### Timing is best-effort, not a zero-lag guarantee

A fork's `push` trigger does **not** subscribe to pushes in the upstream repository.
A real upstream push/release webhook requires cooperation/admin access upstream
or an external watcher/relay. This repository installs the dispatch receiver,
**not** a webhook in Jellyfin's repository or a deployed external watcher.

GitHub's shortest hosted schedule is five minutes; scheduled runs can be delayed
or dropped and runners can queue. There is also an unavoidable small race between
checking a remote ref and publishing. Catch-up dispatches reduce this gap, but
**the fork can lag during detection/queueing/building and after a failed run**.
An upstream conflict or failed test deliberately retains the last good published
state until repaired, rather than publish upstream without the patches.

An optional authenticated upstream relay can send a wake-up to this repository:

```bash
# Run where gh is already authenticated for this repository. No payload/ref needed.
gh api --method POST repos/felixfoertsch/jellyfin-web/dispatches \
  -f event_type=upstream-updated
```

Internal catch-up dispatches use the job's `GITHUB_TOKEN`; GitHub explicitly allows
`repository_dispatch` to trigger a workflow from that token. Check failures in
Actions and configure GitHub workflow-failure notifications. Identical upstream
with a known patch failure is retried on later checks; it is never treated as synced.

References:
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- https://docs.github.com/en/webhooks/using-webhooks/creating-webhooks
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow

## Activation and permissions

- Enable GitHub Actions if the fork's Actions page shows the opt-in banner.
  Select **Patched Jellyfin Web -> Run workflow** to check both channels immediately.
- Jobs request `contents: write` and `packages: write`, including source updates,
  release-source tags and internal dispatches. Repository policies must permit
  lease-protected force-pushes to main. Keep main as the default branch and
  automation as the maintained tooling branch.
  Changes to trusted workflow files themselves must be made by a permitted user
  or app, not by the scheduled source-sync job.
- Both channels belong to the existing `jellyfin-web` package. Make that package
  Public for anonymous pulls, or authenticate Docker for private pulls.
- No additional secret is needed for the built-in poll/build/publish/dispatch
  path. An external watcher would need its own restricted authentication.
- GitHub may disable a public repository's schedule after 60 days of inactivity.
  A cron expression is not proof that checks are actually being delivered.

## Images, version selection and rollback

```text
ghcr.io/felixfoertsch/jellyfin-web:latest
ghcr.io/felixfoertsch/jellyfin-web:nightly
ghcr.io/felixfoertsch/jellyfin-web:12.1
ghcr.io/felixfoertsch/jellyfin-web:release-sha-<source-sha>-run-<run-id>-<attempt>
ghcr.io/felixfoertsch/jellyfin-web:nightly-sha-<source-sha>-run-<run-id>-<attempt>
```

The numeric version is an example; a release receives a version tag when built
as upstream's latest official release. There is no historical backfill. Numeric
tags can change with downstream patches/tooling and base-image refreshes; pin a
unique build tag or digest for an exact rollout/rollback. A unique candidate tag
may exist even if freshness checking prevented its promotion to the rolling tag.
Internal `release-tree-*` / `nightly-tree-*` tags mark successful promotions and
weekly base refreshes. Old tags from previous workflows are not deleted.

`latest` means the latest official **Web** release plus patches, not guaranteed
compatibility with every older Jellyfin server. Match the Web/server release
series and test playback before adopting a new image. `nightly` is development
code. The workflow publishes images; it never upgrades running containers.

Architectures: `linux/amd64`, `linux/arm64`. Internal HTTP port: `8080`.
`/healthz` checks frontend health, not backend availability.
Corresponding source: `/web/downstream-source.tar.gz`; license: `/web/LICENSE.txt`.

## Deploy beside an existing Jellyfin server

The frontend serves `/web/` and proxies all other routes to the existing server,
including API, images, streaming and `/socket`. It needs no media/config/cache
volumes from Jellyfin and creates no second database.

Set `JELLYFIN_SERVER` to the existing server's Docker hostname and HTTP port, such
as `jellyfin:8096`, without a scheme, path or credentials. Jellyfin's Base URL must
be empty for the supplied configuration. A `/jellyfin` prefix needs proxy changes.

```bash
export JELLYFIN_DOCKER_NETWORK=your_existing_jellyfin_network
export JELLYFIN_SERVER=jellyfin:8096
export JELLYFIN_WEB_TAG=latest  # release; use nightly for upstream master

docker compose -f .downstream/compose.yaml pull jellyfin-web
docker compose -f .downstream/compose.yaml up -d jellyfin-web
```

The example binds `127.0.0.1:8097` on the host. Point the trusted HTTPS proxy there,
forwarding WebSocket upgrades and original host/protocol. A containerized proxy
can join the same network and use `http://jellyfin-web:8080`. Configure Jellyfin's
trusted proxies appropriately. Do not expose the backend HTTP port openly.

Copy just the `jellyfin-web` service into an existing Compose project when needed.
The bundled frontend can remain a private fallback. Native clients and clients
bundling their own web assets are unaffected by this patch.

The example runs unprivileged with a read-only root and writable temporary
filesystems for `/tmp` and rendered Nginx configuration. Access logging is disabled
to reduce token leakage; Nginx error logs may still contain request details.

## Local validation and builds

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s .downstream/tests -v
# Clean checkout only: creates a local source commit, never pushes it.
python3 .downstream/sync-upstream.py --upstream-ref refs/heads/master
# For a release, pass its exact tag instead, e.g. refs/tags/v12.1.
docker build -f .downstream/Dockerfile -t jellyfin-web-local .
bash .downstream/smoke-test.sh jellyfin-web-local
```

The sync refuses dirty checkouts and applies patches in an isolated worktree.
Conflicts leave HEAD and the working tree unchanged. No best-effort mode skips
customizations. A failed build/promotion preserves existing rolling images;
publication failures after a source push/tag are retried on the next check.
