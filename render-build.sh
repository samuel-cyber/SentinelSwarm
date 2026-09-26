#!/usr/bin/env bash
# render-build.sh — build script for Render deployment
# Set as the "Build Command" in your Render service settings, or leave
# Render's auto-detect to run it if it is present in the repo root.
set -euo pipefail

echo "==> Installing Python dependencies"
pip install -r orchestrator/requirements.txt

echo "==> Installing Bob Shell (IBM Bob CLI)"
# The official Linux install script downloads the correct bobshell release
# and places the `bob` binary on PATH (~/.local/bin or /usr/local/bin).
# Docs: https://bob.ibm.com/docs/shell/getting-started/install-and-setup
curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash

echo "==> Confirming bob is on PATH"
bob --version

echo "==> Accepting Bob license (non-interactive)"
bob --accept-license run "ping" --format json || true
# The `|| true` means a non-zero exit here (e.g. missing API key) won't
# abort the build — we just need the license flag written to disk.

echo "==> Build complete"
