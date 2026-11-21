#!/usr/bin/env bash
# RSC Frya Fresena Hub: Einrichtung auf einem Linux-Server (Debian/Ubuntu)
# Aufruf im Projektordner:
#   mit eigenem Caddy (Ports 80/443 gehören diesem Programm):        sudo bash setup.sh
#   hinter dem zentralen Proxy (/opt/docker/proxy, deploy/DEPLOY.md):  sudo bash setup.sh --proxy
set -euo pipefail
cd "$(dirname "$0")"
PROXY=0
[ "${1:-}" = "--proxy" ] && PROXY=1

echo "== RSC Frya Fresena Hub: Einrichtung =="

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo ausführen: sudo bash setup.sh"; exit 1
fi

# 0. Git (für Updates) und curl sicherstellen
if ! command -v git >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  apt-get update -qq && apt-get install -y -qq git curl ca-certificates
fi

# 1. Docker installieren, falls nicht vorhanden (offizielles Skript von docker.com)
if ! command -v docker >/dev/null 2>&1; then
  echo "-> Docker wird installiert ..."
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose fehlt. Bitte das Paket docker-compose-plugin installieren."; exit 1
fi

# Hinter dem Proxy: dieser muss schon laufen, sonst fehlt das Netzwerk "proxy" und niemand holt das Zertifikat
if [ "$PROXY" = 1 ] && ! docker network inspect proxy >/dev/null 2>&1; then
  echo "Das Docker-Netzwerk 'proxy' fehlt. Läuft der zentrale Proxy unter /opt/docker/proxy? (siehe deploy/DEPLOY.md)"; exit 1
fi

# 2. .env anlegen
if [ ! -f .env ]; then
  cp .env.example .env
  DOMAIN="${SETUP_DOMAIN:-}"   # deploy/install-vps.sh gibt die Domain vor
  [ -n "$DOMAIN" ] || read -rp "Domain (z. B. reiten.silasgrbwskfotografie.de): " DOMAIN
  case "$DOMAIN" in ""|*" "*|*/*|*:*) echo "Bitte nur den Namen ohne https:// und ohne Schrägstrich."; rm -f .env; exit 1;; esac
  REDIR=""; MAIL=""
  if [ "$PROXY" = 0 ]; then   # hinter dem zentralen Proxy regelt dessen Caddyfile Zertifikat und Umleitungen
    read -rp "Weitere Adressen, die auf diese Domain umleiten (mehrere mit Leerzeichen, leer = keine): " REDIR
    read -rp "E-Mail für das HTTPS-Zertifikat: " MAIL
  fi
  read -rp "Admin-Benutzername: " ADMIN
  while [ -z "$ADMIN" ]; do read -rp "Admin-Benutzername (Pflicht): " ADMIN; done
  while true; do
    read -rsp "Admin-Passwort (mind. 10 Zeichen, wird nicht angezeigt): " PW; echo
    read -rsp "Passwort wiederholen: " PW2; echo
    if [ "$PW" != "$PW2" ]; then echo "Stimmt nicht überein."; continue; fi
    if [ ${#PW} -lt 10 ]; then echo "Zu kurz."; continue; fi
    case "$PW" in *\'*) echo "Bitte ohne Apostroph ('), das stört die Konfigurationsdatei."; continue;; esac
    break
  done
  sed -i "s|^DOMAIN=.*|DOMAIN=${DOMAIN}|; s|^ACME_EMAIL=.*|ACME_EMAIL=${MAIL}|; s|^TMH_ADMIN_USER=.*|TMH_ADMIN_USER=${ADMIN}|" .env
  # Passwort in einfachen Anführungszeichen speichern, damit Sonderzeichen wie $ oder # erhalten bleiben
  grep -v '^TMH_ADMIN_PASSWORD=' .env > .env.tmp
  printf "TMH_ADMIN_PASSWORD='%s'\n" "$PW" >> .env.tmp
  unset PW PW2
  REDIR=$(echo "${REDIR:-}" | tr ',' ' ' | tr -s ' ' | sed 's/^ //; s/ $//')
  if [ -n "$REDIR" ]; then printf "REDIRECT_DOMAINS=%s\n" "$REDIR" >> .env.tmp; fi
  if [ "$PROXY" = 1 ]; then printf "# Betrieb hinter /opt/docker/proxy: nur der Hub, ohne eigenen Caddy\nCOMPOSE_FILE=docker-compose.proxy.yml\n" >> .env.tmp; fi
  mv .env.tmp .env
  chmod 600 .env
  echo "-> .env angelegt (nur für root lesbar)."
else
  echo "-> .env ist schon vorhanden, wird nicht verändert."
fi

# 3. Datenordner mit passenden Rechten (Container läuft als Benutzer 1000)
mkdir -p data
chown -R 1000:1000 data

# 4. Starten
docker compose up -d --build
echo
DOM=$(grep '^DOMAIN=' .env | cut -d= -f2)
if grep -q '^COMPOSE_FILE=docker-compose.proxy.yml' .env; then
  echo "Der Hub läuft hinter dem zentralen Proxy (Container rsc-frya-hub, Port 8770 nur im Netzwerk proxy)."
  echo "Jetzt noch im Proxy eintragen (deploy/DEPLOY.md, Schritt 4):"
  echo "  1. /opt/docker/proxy/.env:       RSC_DOMAIN=${DOM}"
  echo "  2. /opt/docker/proxy/Caddyfile:  Block aus deploy/caddy-proxy-block.txt"
  echo "  3. cd /opt/docker/proxy && docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile"
  echo "  4. cd /opt/docker/proxy && docker compose up -d   (neue .env-Werte, kurze Unterbrechung auch für andere Seiten)"
else
  echo "Fertig. Das HTTPS-Zertifikat holt Caddy beim ersten Aufruf automatisch."
fi
echo "Adresse: https://${DOM}   (Hub unter /hub)"
echo "Status:  docker compose ps    |    Logs: docker compose logs -f hub"
echo "Updates: sudo bash update.sh"
echo "Wichtig: Das Admin-Passwort steht in der .env. Nach der ersten Anmeldung dort TMH_ADMIN_PASSWORD leeren."
