#!/usr/bin/env bash
set -Eeuo pipefail

script_source="${BASH_SOURCE[0]:-}"
ROOT_DIR=""
if [[ -n "$script_source" && -f "$script_source" ]]; then
  ROOT_DIR="$(cd "$(dirname "$script_source")/.." && pwd)"
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root on a fresh Ubuntu 24.04 host" >&2
  exit 2
fi
if [[ -z "${ADMIN_SSH_CIDR:-}" ]]; then
  echo "Set ADMIN_SSH_CIDR, for example 203.0.113.10/32" >&2
  exit 2
fi
os_id="$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')"
os_version="$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')"
if [[ "$os_id:$os_version" != "ubuntu:24.04" ]]; then
  echo "This bootstrap is supported only on Ubuntu 24.04" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y
apt-get install -y ca-certificates curl fail2ban git gnupg jq unattended-upgrades ufw

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
version_codename="$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release | tr -d '"')"
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$version_codename" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if ! id -u bumpabestie >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups docker bumpabestie
else
  usermod --append --groups docker bumpabestie
fi
install -d -m 0750 -o bumpabestie -g bumpabestie /opt/bumpabestie /opt/bumpabestie/releases
if [[ -f /root/.ssh/authorized_keys ]]; then
  install -d -m 0700 -o bumpabestie -g bumpabestie /home/bumpabestie/.ssh
  install -m 0600 -o bumpabestie -g bumpabestie \
    /root/.ssh/authorized_keys /home/bumpabestie/.ssh/authorized_keys
fi

cat >/etc/ssh/sshd_config.d/50-bumpabestie-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitRootLogin prohibit-password
MaxAuthTries 3
X11Forwarding no
AllowTcpForwarding no
EOF
sshd -t
systemctl reload ssh

if ! swapon --show --noheadings | grep -q .; then
  fallocate -l "${SWAP_SIZE:-2G}" /swapfile
  chmod 0600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi
printf '%s\n' \
  'vm.overcommit_memory=1' \
  'vm.swappiness=10' \
  >/etc/sysctl.d/99-bumpabestie.conf
sysctl --system >/dev/null

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow from "$ADMIN_SSH_CIDR" to any port 22 proto tcp
ufw --force enable
systemctl enable --now docker fail2ban unattended-upgrades

if [[ -n "$ROOT_DIR" && -f "$ROOT_DIR/infra/systemd/bumpabestie-backup.service" ]]; then
  install -m 0644 "$ROOT_DIR/infra/systemd/bumpabestie-backup.service" /etc/systemd/system/
  install -m 0644 "$ROOT_DIR/infra/systemd/bumpabestie-backup.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now bumpabestie-backup.timer
fi

echo "Host bootstrap complete. Add the deploy key and clone the repository as bumpabestie."
