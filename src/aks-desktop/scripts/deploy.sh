#!/bin/bash
# Build the arena image, deploy it to microk8s and expose the dashboard on :8080.
# Run inside the Debian distro from anywhere (sudo is passwordless).
set -euo pipefail
export PATH="$PATH:/snap/bin"
HERE="$(cd "$(dirname "$0")" && pwd)"
K="sudo microk8s kubectl"

bash "$HERE/build.sh"
sed 's/\r$//' "$HERE/../k8s/arena.yaml" | $K apply -f -
$K -n arena rollout restart deploy/arena-dashboard
$K -n arena rollout status deploy/arena-dashboard --timeout=180s

sed 's/\r$//' "$HERE/arena-portforward.service" | sudo tee /etc/systemd/system/arena-portforward.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable arena-portforward.service >/dev/null 2>&1
sudo systemctl restart arena-portforward.service

for _ in $(seq 30); do
  curl -fsS -o /dev/null http://127.0.0.1:8080/healthz 2>/dev/null && break
  sleep 1
done
curl -fsS -o /dev/null http://127.0.0.1:8080/healthz
echo "Embedding Arena is up: http://localhost:8080"
