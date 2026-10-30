#!/bin/bash
# Build the arena image with podman and import it into microk8s' containerd.
set -euo pipefail
cd "$(dirname "$0")/../app"
TAG="${TAG:-arena:dev}"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Build from a Linux-side copy (much faster than building over the /mnt/c 9p mount)
cp -r . "$TMP/ctx"
# Some corporate networks block files.pythonhosted.org; fall back to the internal feed proxy.
if [ -z "${PIP_INDEX_URL:-}" ] && ! curl -fsS -o /dev/null --max-time 10 https://files.pythonhosted.org/ 2>/dev/null; then
  PIP_INDEX_URL=https://packagefeedproxy.microsoft.io/pypi/simple/
  echo "files.pythonhosted.org unreachable - using $PIP_INDEX_URL"
fi
sudo podman build -q --network=host --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL:-}" -t "localhost/$TAG" "$TMP/ctx"
sudo podman save --format docker-archive -o "$TMP/arena.tar" "localhost/$TAG"
sudo microk8s ctr image import "$TMP/arena.tar" >/dev/null
sudo microk8s ctr image tag --force "localhost/$TAG" "docker.io/library/$TAG" >/dev/null
sudo microk8s ctr images ls -q | grep -E "arena" || true
echo "built and imported $TAG"
