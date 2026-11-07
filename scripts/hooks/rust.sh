#!/usr/bin/env bash
# Rust hook wrapper for prek: scripts/hooks/rust.sh <fmt|clippy|machete>
# cargo/rustfmt/clippy are required (fail if missing); cargo-machete is optional locally (CI enforces it).
set -euo pipefail

find_cargo() {
  command -v cargo || command -v "$HOME/.cargo/bin/cargo" || command -v /opt/homebrew/bin/cargo || true
}

CARGO="$(find_cargo)"
if [ -z "$CARGO" ]; then
  echo "cargo not found. Install Rust via https://rustup.rs" >&2
  exit 127
fi

case "${1:-}" in
  fmt)
    exec "$CARGO" fmt --all
    ;;
  clippy)
    exec "$CARGO" clippy --all-targets --locked -- -D warnings
    ;;
  machete)
    if ! "$CARGO" machete --version >/dev/null 2>&1; then
      echo "cargo-machete not installed, skipping (install: cargo install cargo-machete --locked)"
      exit 0
    fi
    exec "$CARGO" machete
    ;;
  *)
    echo "usage: rust.sh <fmt|clippy|machete>" >&2
    exit 2
    ;;
esac
