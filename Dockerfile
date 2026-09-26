#
# Sentinel Swarm — Docker deployment for Render.
#
# Replaces the native Python buildpack, which was failing inside Render's own
# tooling ("render-build-tool: command not found" in their common.sh) before
# any of our build steps ran.  Nothing in this file is a workaround for that
# error — it just removes the buildpack from the path entirely.
#
# The app shells out to the `bob` CLI at request time, so Node and the Bob
# Shell package must both survive into the FINAL image, not just the builder.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# --- System packages --------------------------------------------------------
# ca-certificates / curl / gnupg : required by NodeSource's setup script
# procps                         : the Bob installer's spinner calls `ps -p`
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        procps \
 && rm -rf /var/lib/apt/lists/*

# --- Node.js 22 -------------------------------------------------------------
# Bob Shell is distributed as an npm package and its CLI is a Node program, so
# Node has to be present at runtime too — it cannot be dropped from the final
# image.  The installer requires >= 22.15; NodeSource's 22.x track satisfies
# that, and the check below fails the build loudly if it ever does not.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN node -v && npm -v \
 && node -e 'const [maj,min]=process.versions.node.split(".").map(Number); if (maj<22||(maj===22&&min<15)) { console.error("Node " + process.versions.node + " is too old; Bob Shell needs >= 22.15"); process.exit(1); }'

# --- npm global prefix ------------------------------------------------------
# Pin the prefix so `bob` lands somewhere predictable.  Create the
# lib/node_modules dir up front: npm creates the leaf package directory but
# NOT its parent, which is exactly what produced
# "npm error enoent: mkdir '/usr/local/lib/node_modules'" on the buildpack.
# /usr/local/bin precedes /usr/bin on PATH, so the installed `bob` wins.
ENV NPM_CONFIG_PREFIX=/usr/local
RUN mkdir -p /usr/local/lib/node_modules

# --- tput stub --------------------------------------------------------------
# Slim images ship no terminfo tools.  The Bob install chain calls `tput` for
# terminal colors; a missing binary aborts an install running under `set -e`,
# and at runtime it would abort a live /run-swarm in front of judges.
# Installing into /usr/local/bin (not /tmp) means one stub covers both the
# build and the runtime container, and shadows any real tput on PATH.
RUN printf '%s\n' \
      '#!/bin/sh' \
      '# No-op tput. cols/lines are handled because their OUTPUT is used as a' \
      '# value; returning empty there can break arithmetic downstream. Every' \
      '# other subcommand (setaf, sgr0, bold, ...) is a color call whose' \
      '# output is discarded, so printing nothing and exiting 0 is correct.' \
      'case "$1" in' \
      '  cols)  echo 80 ;;' \
      '  lines) echo 24 ;;' \
      '  *)     exit 0 ;;' \
      'esac' \
      > /usr/local/bin/tput \
 && chmod 755 /usr/local/bin/tput \
 && [ "$(command -v tput)" = "/usr/local/bin/tput" ] \
 && [ "$(tput cols)" = "80" ] \
 && [ -z "$(tput setaf 1)" ]

# --- Python dependencies ----------------------------------------------------
# Copied on its own so editing source does not re-run pip.
WORKDIR /app
COPY orchestrator/requirements.txt orchestrator/requirements.txt
RUN pip install --no-cache-dir -r orchestrator/requirements.txt

# --- Application source -----------------------------------------------------
# .dockerignore keeps .env, .venv/ and PLANTED_BUGS.md out of this layer.
COPY . .

# --- Bob Shell --------------------------------------------------------------
# `--pm npm` is REQUIRED, not cosmetic.  The installer prompts on /dev/tty when
# more than one package manager is present, and it reads /dev/tty explicitly,
# so no stdin redirect can answer it — with no terminal the read fails and its
# `while true` loop spins forever printing "✗ Invalid selection".
# Args must go to the piped interpreter via `bash -s --`, since otherwise bash
# consumes the script from stdin and never sees them.
RUN curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash -s -- --pm npm

# Same verification the buildpack used — this is what caught the prefix mistake.
RUN echo "bob resolves to: $(command -v bob || echo 'NOT FOUND')" \
 && echo "npm prefix: ${NPM_CONFIG_PREFIX}" \
 && if ! command -v bob >/dev/null 2>&1; then \
        echo "ERROR: bob was installed but is not on PATH." >&2; \
        echo "NPM_CONFIG_PREFIX=${NPM_CONFIG_PREFIX}" >&2; \
        echo "PATH=${PATH}" >&2; \
        exit 1; \
    fi

RUN bob --version

# Accept the license at build time.  `|| true` keeps a non-zero exit (no API
# key in the build environment) from failing the build: the acceptance flag is
# written to disk before the `run` subcommand needs credentials.
# Note: if BOB_API_KEY IS set as a build arg, this spends one real API call.
RUN bob --accept-license run "ping" --format json || true

EXPOSE 8000

# --bind 0.0.0.0:$PORT is REQUIRED on Render: the proxy cannot reach a process
# listening on gunicorn's default 127.0.0.1:8000, which presents as a green
# deploy with an unreachable app.  Render injects $PORT (10000 by default).
#
# --workers 1: the pipeline rewrites files on disk (Medic patches the
# scenario's app.py, Saboteur writes test_generated.py), so concurrent workers
# would race on the same files.  One sync worker serialises them, which also
# matches how the demo is actually driven.
#
# `exec` replaces the shell so gunicorn receives SIGTERM directly and shuts
# down cleanly on deploy/restart instead of being killed.
CMD exec gunicorn app:app --chdir orchestrator --timeout 300 --workers 1 --bind 0.0.0.0:${PORT:-8000}
