#!/usr/bin/env bash
# Первичная настройка чистого Ubuntu 24.04 (VDS Hostland): Docker, ufw, fail2ban, unattended-upgrades.
# Запуск от root: bash server_init.sh
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "✗ Запустите от root (sudo bash server_init.sh)"
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "→ обновление пакетов"
apt-get update -qq
apt-get upgrade -y -qq

echo "→ базовые утилиты"
apt-get install -y -qq \
  ca-certificates curl gnupg git ufw fail2ban unattended-upgrades \
  age apache2-utils

echo "→ Docker Engine"
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

systemctl enable --now docker

echo "→ ufw: 22/80/443"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "→ fail2ban (sshd)"
cat >/etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 5
bantime = 1h
findtime = 10m
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban

echo "→ автообновления безопасности"
dpkg-reconfigure -f noninteractive unattended-upgrades || true
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF

echo "→ каталоги приложения"
mkdir -p /srv/dok /var/backups/dok/daily /var/backups/dok/monthly /var/log
chmod 750 /var/backups/dok

echo "→ cron бэкапа (03:30 UTC)"
CRON_LINE="30 3 * * * /srv/dok/deploy/backup.sh >> /var/log/dok-backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'dok/deploy/backup.sh' || true; echo "$CRON_LINE") | crontab -

echo
echo "✓ Базовая настройка сервера готова."
echo "  Далее: клонируйте репозиторий в /srv/dok, заполните deploy/.env,"
echo "  затем: cd /srv/dok && ./deploy/deploy.sh"
echo "  Мониторинг: зарегистрируйте https://app.dok.moscow/login в UptimeRobot / Uptime Kuma"
echo "  с уведомлением на телефон."
