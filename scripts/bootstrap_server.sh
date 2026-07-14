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
apt-get install -y ca-certificates curl fail2ban git gnupg jq python3 sudo unattended-upgrades ufw util-linux

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
install -d -m 0700 -o bumpabestie -g bumpabestie /var/lib/bumpabestie
if [[ -n "$ROOT_DIR" && -f "$ROOT_DIR/infra/bin/bumpabestie-promote" ]]; then
  install -m 0755 -o root -g root \
    "$ROOT_DIR/infra/bin/bumpabestie-promote" /usr/local/sbin/bumpabestie-promote
fi
if [[ -n "$ROOT_DIR" \
  && -f "$ROOT_DIR/scripts/validate_temporary_auth_secret.sh" \
  && -f "$ROOT_DIR/infra/sudoers/bumpabestie-temporary-auth-secret" ]]; then
  install -m 0755 -o root -g root \
    "$ROOT_DIR/scripts/validate_temporary_auth_secret.sh" \
    /usr/local/sbin/bumpabestie-validate-temporary-auth-secret
  visudo -cf "$ROOT_DIR/infra/sudoers/bumpabestie-temporary-auth-secret" >/dev/null
  install -m 0440 -o root -g root \
    "$ROOT_DIR/infra/sudoers/bumpabestie-temporary-auth-secret" \
    /etc/sudoers.d/bumpabestie-temporary-auth-secret
  visudo -cf /etc/sudoers.d/bumpabestie-temporary-auth-secret >/dev/null
fi
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

docker_firewall_source=""
docker_firewall_binary="/usr/local/sbin/bumpabestie-cloudflare-docker-firewall"
docker_firewall_state="/etc/bumpabestie/cloudflare-docker-firewall.json"
docker_firewall_unit="bumpabestie-cloudflare-docker-firewall.service"
docker_firewall_pregate_unit="bumpabestie-cloudflare-origin-pregate.service"
docker_firewall_failure_unit="bumpabestie-cloudflare-docker-firewall-failure.service"
docker_firewall_dropin="10-bumpabestie-cloudflare-origin-pregate.conf"
if [[ -n "$ROOT_DIR" && -f "$ROOT_DIR/scripts/cloudflare_docker_firewall.py" ]]; then
  docker_firewall_source="$ROOT_DIR/scripts/cloudflare_docker_firewall.py"
  install -m 0755 -o root -g root "$docker_firewall_source" "$docker_firewall_binary"
fi

host_units=(
  bumpabestie-backup.service
  bumpabestie-backup.timer
  bumpabestie-disk-usage.service
  bumpabestie-disk-usage.timer
)
if [[ -n "$ROOT_DIR" && -f "$ROOT_DIR/infra/systemd/${host_units[0]}" ]]; then
  for unit_name in "${host_units[@]}"; do
    if [[ ! -f "$ROOT_DIR/infra/systemd/$unit_name" ]]; then
      echo "Required host unit is missing: $unit_name" >&2
      exit 2
    fi
    install -m 0644 "$ROOT_DIR/infra/systemd/$unit_name" /etc/systemd/system/
  done
  systemctl daemon-reload
  systemctl enable --now bumpabestie-backup.timer bumpabestie-disk-usage.timer
fi

if [[ -n "$docker_firewall_source" && -e "$docker_firewall_state" ]]; then
  "$docker_firewall_binary" verify-state
  for unit_name in \
    "$docker_firewall_pregate_unit" \
    "$docker_firewall_unit" \
    "$docker_firewall_failure_unit"; do
    if [[ ! -f "$ROOT_DIR/infra/systemd/$unit_name" ]]; then
      echo "Required Docker firewall unit is missing: $unit_name" >&2
      exit 2
    fi
    install -m 0644 -o root -g root \
      "$ROOT_DIR/infra/systemd/$unit_name" "/etc/systemd/system/$unit_name"
  done
  docker_firewall_dropin_source="$ROOT_DIR/infra/systemd/docker.service.d/$docker_firewall_dropin"
  if [[ ! -f "$docker_firewall_dropin_source" ]]; then
    echo "Required Docker firewall drop-in is missing: $docker_firewall_dropin" >&2
    exit 2
  fi
  install -d -m 0755 -o root -g root /etc/systemd/system/docker.service.d
  install -m 0644 -o root -g root \
    "$docker_firewall_dropin_source" \
    "/etc/systemd/system/docker.service.d/$docker_firewall_dropin"
  systemctl daemon-reload
  systemctl start "$docker_firewall_pregate_unit"
  systemctl enable --now "$docker_firewall_unit"
elif [[ -n "$docker_firewall_source" ]]; then
  echo "Docker firewall executable installed but intentionally inactive until validated state is refreshed."
fi

echo "Host bootstrap complete. Add the deploy key and clone the repository as bumpabestie."
