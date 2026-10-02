# syntax=docker/dockerfile:1
# Build once on the runner architecture; dist/ is architecture-independent.
FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
ENV NODE_OPTIONS=--max-old-space-size=4096
# The exact PR regression test must remain present even if upstream changes.
RUN test -s src/apps/legacy/features/playback/utils/mediaSegmentSettings.test.ts \
    && npm test -- src/apps/legacy/features/playback/utils/mediaSegmentSettings.test.ts
RUN npm run build:check && npm test
RUN npm run build:production && test -s dist/index.html && test -s dist/config.json
# Ship the corresponding source, including this build recipe and patch queue.
RUN tar --exclude=./node_modules --exclude=./dist --exclude=./.git \
    -czf /tmp/downstream-source.tar.gz . \
    && mv /tmp/downstream-source.tar.gz dist/downstream-source.tar.gz

FROM nginxinc/nginx-unprivileged:stable-alpine AS runtime
ENV JELLYFIN_SERVER=jellyfin:8096
COPY --from=build --chown=101:101 /src/dist/ /usr/share/nginx/html/web/
COPY --from=build --chown=101:101 /src/LICENSE /usr/share/nginx/html/web/LICENSE.txt
COPY --chown=101:101 .downstream/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --chmod=755 .downstream/19-validate-server.sh /docker-entrypoint.d/19-validate-server.sh
ARG SOURCE_REVISION=local
ARG UPSTREAM_REVISION=unknown
LABEL org.opencontainers.image.title="Jellyfin Web with downstream segment defaults" \
      org.opencontainers.image.description="Unofficial standalone web client; follows upstream master" \
      org.opencontainers.image.source="https://github.com/felixfoertsch/jellyfin-web" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.licenses="GPL-2.0-or-later" \
      io.github.felixfoertsch.jellyfin-web.upstream="${UPSTREAM_REVISION}"
RUN printf '{"revision":"%s","upstream":"%s"}\n' "$SOURCE_REVISION" "$UPSTREAM_REVISION" \
    > /usr/share/nginx/html/web/downstream-build.json
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
