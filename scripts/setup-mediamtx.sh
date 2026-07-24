#!/usr/bin/env bash
set -euo pipefail

VERSION="1.19.3"
OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"

case "$ARCH" in
  arm64|aarch64) ARCH="arm64" ;;
  x86_64) ARCH="amd64" ;;
  *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

case "$OS" in
  darwin|linux) ;;
  *) echo "Unsupported operating system: $OS" >&2; exit 1 ;;
esac

ASSET="mediamtx_v${VERSION}_${OS}_${ARCH}.tar.gz"
BASE_URL="https://github.com/bluenviron/mediamtx/releases/download/v${VERSION}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

curl --fail --location --silent --show-error "$BASE_URL/$ASSET" --output "$WORK_DIR/$ASSET"
curl --fail --location --silent --show-error "$BASE_URL/checksums.sha256" --output "$WORK_DIR/checksums.sha256"
(
  cd "$WORK_DIR"
  grep " \\*$ASSET$" checksums.sha256 | shasum -a 256 -c -
  tar -xzf "$ASSET"
)

mkdir -p "$HOME/.local/bin"
install -m 755 "$WORK_DIR/mediamtx" "$HOME/.local/bin/mediamtx"
echo "Installed MediaMTX v${VERSION} to $HOME/.local/bin/mediamtx"
