#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root on a fresh Ubuntu 24.04 host" >&2
  exit 2
fi
if [[ -z "${ADMIN_SSH_CIDR:-}" ]]; then
  echo "Set ADMIN_SSH_CIDR, for example 203.0.113.10/32" >&2
  exit 2
fi
if [[ "$(. /etc/os-release && echo "$ID:$VERSION_ID")" != "ubuntu:24.04" ]]; then
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
. /etc/os-release
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu %s stable\n' "$(dpkg --print-architecture)" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

if ! id -u bumpabestie >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups docker bumpabestie
fi
install -d -m 0750 -o bumpabestie -g bumpabestie /opt/bumpabestie /opt/bumpabestie/releases

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow from "$ADMIN_SSH_CIDR" to any port 22 proto tcp
ufw --force enable
systemctl enable --now docker fail2ban unattended-upgrades

echo "Host bootstrap complete. Add the deploy key and clone the repository as bumpabestie."
