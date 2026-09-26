#!/usr/bin/env bash
# render-build.sh — build script for Render deployment
# Set as the "Build Command" in your Render service settings, or leave
# Render's auto-detect to run it if it is present in the repo root.
set -euo pipefail

echo "==> Installing Python dependencies"
pip install -r orchestrator/requirements.txt

echo "==> Installing Bob Shell (IBM Bob CLI)"
# The official Linux install script downloads the bobshell release and
# installs the `bob` binary with `npm install -g`, i.e. into npm's global
# prefix — NOT ~/.local/bin, and not /usr/local unless that is the prefix.
# Docs: https://bob.ibm.com/docs/shell/getting-started/install-and-setup
#
# --pm npm is REQUIRED here, not optional.
# The installer prompts on /dev/tty to choose between npm/pnpm/yarn whenever
# more than one is present, and it reads from /dev/tty explicitly — so no
# amount of stdin redirect (`< /dev/null`, `echo 1 |`) can answer it. With no
# controlling terminal (Render's build container) the read fails, the choice
# stays empty, and its `while true` validation loop spins forever printing
# "✗ Invalid selection". Render's image has both npm and yarn, which is why
# it triggers here but not on a single-manager machine.
#
# The script supports two flags only: --pm/--package-manager and --version/-v.
# There is no environment variable it checks before prompting. Args must be
# passed to the piped interpreter via `bash -s --`, otherwise bash consumes
# the script from stdin and never sees them.

# npm creates the leaf package dir under its global prefix but will NOT create
# the prefix itself, so a prefix that does not exist yet fails the install with
# "npm error enoent: mkdir '/usr/local/lib/node_modules'". /usr/local is the
# system default and is not writable by Render's build user anyway, so we point
# npm at a directory under the project and make sure it exists first.
# NPM_CONFIG_PREFIX is set in Render's dashboard; the fallback here and the one
# in the PATH diagnostic below must stay in sync.
mkdir -p "${NPM_CONFIG_PREFIX:-/opt/render/project/.npm-global}"

curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash -s -- --pm npm
# Pin a version for reproducible builds (current release as of this writing):
#   curl -fsSL https://bob.ibm.com/download/bobshell.sh \
#     | bash -s -- --pm npm --version 2.0.5

echo "==> Confirming bob is on PATH"
# npm's global bin dir is on PATH during the build but is NOT guaranteed to be
# on PATH in the runtime container, which is a separate environment. Resolve it
# and record it so the runtime can be pointed at the same directory.
NPM_GLOBAL_BIN="$(npm prefix -g 2>/dev/null || echo "${NPM_CONFIG_PREFIX:-/opt/render/project/.npm-global}")/bin"
echo "    npm global bin: ${NPM_GLOBAL_BIN}"
echo "    bob resolves to: $(command -v bob || echo 'NOT FOUND')"

if ! command -v bob >/dev/null 2>&1; then
    echo "ERROR: bob was installed but is not on PATH." >&2
    echo "Check that NPM_CONFIG_PREFIX (currently" >&2
    echo "'${NPM_CONFIG_PREFIX:-<unset>}') is set in Render's env vars, then add" >&2
    echo "${NPM_GLOBAL_BIN} to the runtime PATH environment variable." >&2
    exit 1
fi

bob --version

echo "==> Accepting Bob license (non-interactive)"
# Writes the license-acceptance flag to disk. `|| true` keeps a non-zero exit
# (e.g. no API key present in the build environment) from failing the build.
# Note: if BOB_API_KEY IS set in the build env, this spends one real API call
# per deploy — remove this step if you would rather not pay that.
bob --accept-license run "ping" --format json || true

echo "==> Build complete"
