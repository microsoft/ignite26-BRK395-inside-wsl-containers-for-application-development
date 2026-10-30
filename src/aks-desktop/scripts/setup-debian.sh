#!/bin/bash
# Provision a fresh WSL Debian distro for the Embedding Arena. Run as root inside the distro
# (setup-wsl.ps1 does this for you). Idempotent: safe to re-run.
#   - passwordless root + passwordless sudo for the default user
#   - systemd, snapd, microk8s (dns + hostpath-storage), podman
#   - WSL networking fixes for microk8s
set -euo pipefail
export PATH="$PATH:/snap/bin"
USER_NAME="${1:-${SUDO_USER:-crloewen}}"
MK8S_CHANNEL="${MK8S_CHANNEL:-1.35/stable}"
HERE="$(cd "$(dirname "$0")" && pwd)"

install_unit() {  # copy a file from the (possibly CRLF) Windows checkout with LF endings
  sed 's/\r$//' "$1" > "$2"; chmod "$3" "$2"
}

echo "==> users: passwordless root and sudo"
id "$USER_NAME" >/dev/null 2>&1 || useradd -m -s /bin/bash "$USER_NAME"
passwd -d root >/dev/null
passwd -d "$USER_NAME" >/dev/null
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq sudo curl ca-certificates iproute2 nftables snapd podman >/dev/null
usermod -aG sudo "$USER_NAME"
printf '%s ALL=(ALL) NOPASSWD:ALL\n%%sudo ALL=(ALL) NOPASSWD:ALL\n' "$USER_NAME" > /etc/sudoers.d/90-nopasswd
echo 'Defaults secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin"' > /etc/sudoers.d/95-snap-path
chmod 440 /etc/sudoers.d/90-nopasswd /etc/sudoers.d/95-snap-path
visudo -cq

if [ "$(ps -p 1 -o comm=)" != "systemd" ]; then
  echo "!! systemd is not PID 1 - enable it in /etc/wsl.conf and 'wsl --terminate' the distro first" >&2
  exit 1
fi

echo "==> WSL networking fixes"
install_unit "$HERE/wsl-net-fix.sh" /usr/local/sbin/wsl-net-fix.sh 755
install_unit "$HERE/wsl-net-fix.service" /etc/systemd/system/wsl-net-fix.service 644
install_unit "$HERE/wsl-net-fix.timer" /etc/systemd/system/wsl-net-fix.timer 644
systemctl daemon-reload
systemctl enable --now wsl-net-fix.service wsl-net-fix.timer >/dev/null 2>&1

echo "==> microk8s ($MK8S_CHANNEL)"
systemctl enable --now snapd.socket snapd.service >/dev/null 2>&1
snap wait system seed.loaded
snap list microk8s >/dev/null 2>&1 || snap install microk8s --classic --channel="$MK8S_CHANNEL"
usermod -aG microk8s "$USER_NAME"
/usr/local/sbin/wsl-net-fix.sh
microk8s status --wait-ready --timeout 300 >/dev/null
microk8s enable dns hostpath-storage
mkdir -p /var/lib/arena-cache

echo "==> done: $(microk8s version), $(podman --version)"
