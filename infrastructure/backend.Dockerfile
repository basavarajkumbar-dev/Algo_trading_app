# GAINZALGO PRO MAX — backend image
#
# Phase 1 scope: a single-stage image good enough for local development
# and Docker Compose. A slimmer multi-stage build (separating build-time
# tooling like ruff/mypy from the runtime image) is a reasonable later
# optimization once the dependency set stabilizes — not necessary yet.
#
# Phase 2 note: this image's build context is the REPO ROOT (see
# docker-compose.yml's `backend.build.context: .`), not `backend/`,
# because the entrypoint script below also needs to copy in
# infrastructure/backend-entrypoint.sh, which `COPY` cannot reach if the
# context were scoped to backend/ alone (Docker forbids referencing
# paths outside the build context). All COPY sources here are therefore
# written relative to the repo root.

FROM python:3.12-slim

WORKDIR /app

# System deps needed to build asyncpg/psycopg-style wheels if a
# platform lacks prebuilt wheels; kept minimal.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/. .
COPY infrastructure/backend-entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

# --reload is intentional for this early phase (fast local iteration).
# A dedicated production Dockerfile/compose profile without --reload
# should be introduced when a deployment target beyond local dev exists.
ENTRYPOINT ["/entrypoint.sh"]
