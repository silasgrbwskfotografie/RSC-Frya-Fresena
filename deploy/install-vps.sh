#!/usr/bin/env bash
# RSC Frya Fresena Hub: komplette Installation auf dem VPS hinter dem zentralen Proxy (/opt/docker/proxy).
# Macht die Schritte 2 bis 5 aus deploy/DEPLOY.md in einem Lauf und fragt vor allem, was andere Seiten betrifft.
#
# Am einfachsten vom PC aus mit deploy/installiere-vps.ps1 (kopiert dieses Skript und startet es).
# Von Hand:
#   scp -i $env:USERPROFILE\.ssh\<schluessel> deploy\install-vps.sh <benutzer>@<server>:/tmp/
#   sudo bash /tmp/install-vps.sh [--repo <adresse>] [--domain <domain>]
#
# Ablauf:
#   1. Prüfen: root, Docker, laufender Proxy, Netzwerk "proxy", DNS der Domain
#   2. Repository nach /opt/docker/apps/rsc-frya-fresena klonen (oder vorhandenes nutzen).
#      Öffentlich: per HTTPS. Privat (GitHub): legt einen Deploy-Key nur für dieses Repository an, zeigt ihn zum Eintragen
#      bei GitHub, klont per SSH und merkt sich den Schlüssel im Repository (core.sshCommand), damit update.sh ohne Rückfrage zieht.
#      Andere Programme auf dem Server (und deren GitHub-Zugang) bleiben unberührt.
#   3. setup.sh --proxy (fragt Admin und Passwort, startet den Container ohne Ports)
#   4. Proxy: Sicherung von .env und Caddyfile, RSC_DOMAIN und Caddy-Block ergänzen,
#      neue Konfiguration in einem Wegwerf-Container prüfen, erst nach Rückfrage übernehmen
#   5. Testen: neue Seite und bestehende Seite antworten, sonst Angebot zum Zurückrollen
#   6. Optional: Update-Helfer einrichten
# Mehrfach ausführbar: Was schon da ist, wird erkannt und nicht doppelt eingetragen.
set -euo pipefail

APP_DIR="/opt/docker/apps/rsc-frya-fresena"
PROXY_DIR="/opt/docker/proxy"
PROXY_CONTAINER="proxy-caddy"
HUB_CONTAINER="rsc-frya-hub"
DEFAULT_DOMAIN="pferd.silasgrbwskfotografie.de"
STAMP=$(date +%Y-%m-%d_%H%M%S)

say()  { printf '\n== %s\n' "$*"; }
ok()   { printf '   ok: %s\n' "$*"; }
warn() { printf '   ACHTUNG: %s\n' "$*"; }
die()  { printf '\nAbbruch: %s\n' "$*" >&2; exit 1; }
ask()  { local a; read -rp "   $1 [j/N] " a; [[ "$a" =~ ^[jJyY]$ ]]; }

REPO=""; DOMAIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="${2:-}"; shift 2;;
    --domain) DOMAIN="${2:-}"; shift 2;;
    *) die "Unbekannte Option: $1 (erlaubt: --repo <adresse>, --domain <domain>)";;
  esac
done
DEPLOY_KEY="/root/.ssh/rsc_frya_deploy"   # nur für dieses Repository, über core.sshCommand, keine globale SSH-Konfiguration

# ---------------------------------------------------------------- 1. Prüfen
say "1. Voraussetzungen"
[ "$(id -u)" -eq 0 ] || die "Bitte mit sudo starten: sudo bash $0"
command -v docker >/dev/null || die "Docker fehlt. Auf diesem VPS sollte Docker schon laufen, bitte prüfen."
docker compose version >/dev/null 2>&1 || die "docker compose fehlt."
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git; }
command -v curl >/dev/null || { apt-get update -qq && apt-get install -y -qq curl; }
[ -f "$PROXY_DIR/docker-compose.yml" ] && [ -f "$PROXY_DIR/Caddyfile" ] && [ -f "$PROXY_DIR/.env" ] \
  || die "Zentraler Proxy nicht gefunden ($PROXY_DIR mit docker-compose.yml, Caddyfile, .env)."
docker inspect -f '{{.State.Running}}' "$PROXY_CONTAINER" 2>/dev/null | grep -q true || die "Container $PROXY_CONTAINER läuft nicht."
docker network inspect proxy >/dev/null 2>&1 || die "Docker-Netzwerk 'proxy' fehlt."
grep -q '^(sicherheit)' "$PROXY_DIR/Caddyfile" || die "Im Proxy-Caddyfile fehlt der Block (sicherheit), den der neue Block importiert."
ok "Docker, Proxy ($PROXY_CONTAINER) und Netzwerk proxy sind da"

[ -n "$DOMAIN" ] || { read -rp "   Domain [$DEFAULT_DOMAIN]: " DOMAIN; DOMAIN=${DOMAIN:-$DEFAULT_DOMAIN}; }
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]] || die "Ungültige Domain: $DOMAIN (nur Name, ohne https://)"

# DNS: zeigt die Domain auf diesen Server? Sonst bekommt Caddy kein Zertifikat.
DNS_IPS=$(getent ahosts "$DOMAIN" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' || true)
MY_IPS=$(hostname -I 2>/dev/null || true)
if [ -z "$DNS_IPS" ]; then
  warn "$DOMAIN löst noch nicht auf (DNS: A-Eintrag auf die IP dieses Servers, MX/TXT nicht anfassen)."
  ask "Trotzdem weitermachen? (Zertifikat klappt erst, wenn DNS stimmt)" || die "Erst DNS eintragen, dann erneut starten."
else
  MATCH=0; for ip in $DNS_IPS; do [[ " $MY_IPS " == *" $ip "* ]] && MATCH=1; done
  if [ "$MATCH" = 1 ]; then ok "DNS: $DOMAIN zeigt auf diesen Server ($DNS_IPS)"
  else
    warn "DNS: $DOMAIN zeigt auf $DNS_IPS, dieser Server hat $MY_IPS"
    ask "Trotzdem weitermachen?" || die "DNS prüfen und erneut starten."
  fi
fi

# ---------------------------------------------------------------- 2. Programm ablegen
say "2. Programm in $APP_DIR"
OWNER="${SUDO_USER:-root}"
if [ -d "$APP_DIR/.git" ]; then
  ok "Repository ist schon da ($(git -C "$APP_DIR" log --oneline -1 2>/dev/null || echo '?'))"
else
  [ -e "$APP_DIR" ] && [ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ] && die "$APP_DIR ist nicht leer, aber kein Git-Repository. Bitte prüfen."
  [ -n "$REPO" ] || read -rp "   Repository-Adresse (z. B. https://github.com/<konto>/<repository>.git): " REPO
  [ -n "$REPO" ] || die "Keine Repository-Adresse."
  # GitHub-Adresse in beiden Formen: https://github.com/a/b(.git) und git@github.com:a/b(.git)
  GH_PATH=$(printf '%s' "$REPO" | sed -nE 's#^(https://github\.com/|git@github\.com:)([^/]+/[^/]+)/?$#\2#p' | sed 's/\.git$//')
  HTTPS_URL="$REPO"; SSH_URL=""
  if [ -n "$GH_PATH" ]; then HTTPS_URL="https://github.com/${GH_PATH}.git"; SSH_URL="git@github.com:${GH_PATH}.git"; fi
  mkdir -p "$APP_DIR"
  if GIT_TERMINAL_PROMPT=0 git ls-remote "$HTTPS_URL" >/dev/null 2>&1; then
    ok "Repository ist öffentlich erreichbar, klone per HTTPS"
    git clone "$HTTPS_URL" "$APP_DIR" || die "git clone fehlgeschlagen."
  else
    [ -n "$SSH_URL" ] || die "Repository nicht erreichbar: $REPO (privat und nicht bei GitHub? Dann bitte von Hand klonen)."
    say "Privates Repository: Zugang über einen Deploy-Key (nur Lesen, nur dieses Repository)"
    install -d -m 700 /root/.ssh
    [ -f "$DEPLOY_KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "rsc-frya-fresena@$(hostname)" -f "$DEPLOY_KEY"
    GSSH="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    until GIT_SSH_COMMAND="$GSSH" GIT_TERMINAL_PROMPT=0 git ls-remote "$SSH_URL" >/dev/null 2>&1; do
      echo
      echo "   Bitte diesen Schlüssel bei GitHub eintragen:"
      echo "   https://github.com/${GH_PATH}/settings/keys  >  Add deploy key  >  Titel z. B. VPS, Haken 'Allow write access' NICHT setzen"
      echo
      cat "$DEPLOY_KEY.pub"
      echo
      read -rp "   Eingetragen? Enter zum Prüfen, a + Enter zum Abbrechen: " A
      [ "$A" = "a" ] && die "Abgebrochen. Der Schlüssel bleibt in $DEPLOY_KEY für den nächsten Versuch."
    done
    ok "Zugang über Deploy-Key klappt"
    GIT_SSH_COMMAND="$GSSH" git clone "$SSH_URL" "$APP_DIR" || die "git clone fehlgeschlagen."
    git -C "$APP_DIR" config core.sshCommand "$GSSH"   # update.sh (git pull als root) nutzt denselben Schlüssel
  fi
  chown -R "$OWNER": "$APP_DIR"
  ok "geklont nach $APP_DIR"
fi
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true
[ -f "$APP_DIR/setup.sh" ] && [ -f "$APP_DIR/docker-compose.proxy.yml" ] || die "Im Repository fehlen setup.sh oder docker-compose.proxy.yml."

# ---------------------------------------------------------------- 3. Einrichten und starten
say "3. Einrichten (setup.sh --proxy)"
if [ -f "$APP_DIR/.env" ]; then
  grep -q '^COMPOSE_FILE=docker-compose.proxy.yml' "$APP_DIR/.env" || die "$APP_DIR/.env ist nicht für den Proxy-Betrieb eingerichtet (COMPOSE_FILE fehlt)."
  ENV_DOMAIN=$(grep '^DOMAIN=' "$APP_DIR/.env" | cut -d= -f2)
  [ "$ENV_DOMAIN" = "$DOMAIN" ] || die "In $APP_DIR/.env steht DOMAIN=$ENV_DOMAIN, gewählt ist $DOMAIN. Bitte angleichen."
  ok ".env vorhanden, wird nicht verändert"
  (cd "$APP_DIR" && docker compose up -d --build)
else
  SETUP_DOMAIN="$DOMAIN" bash "$APP_DIR/setup.sh" --proxy
fi
docker inspect -f '{{.State.Running}}' "$HUB_CONTAINER" 2>/dev/null | grep -q true || die "Container $HUB_CONTAINER läuft nicht. Logs: cd $APP_DIR && docker compose logs --tail 50 hub"
ok "Container $HUB_CONTAINER läuft (ohne Ports, nur im Netzwerk proxy)"

# ---------------------------------------------------------------- 4. Proxy erweitern
say "4. Zentralen Proxy erweitern"
NEED_ENV=1; NEED_BLOCK=1
if grep -q '^RSC_DOMAIN=' "$PROXY_DIR/.env"; then
  CUR=$(grep '^RSC_DOMAIN=' "$PROXY_DIR/.env" | tail -1 | cut -d= -f2)
  [ "$CUR" = "$DOMAIN" ] || die "In $PROXY_DIR/.env steht schon RSC_DOMAIN=$CUR. Bitte von Hand klären."
  NEED_ENV=0; ok "RSC_DOMAIN ist schon eingetragen"
fi
if grep -q "reverse_proxy $HUB_CONTAINER:8770" "$PROXY_DIR/Caddyfile"; then
  NEED_BLOCK=0; ok "Caddy-Block ist schon eingetragen"
fi

if [ "$NEED_ENV" = 1 ] || [ "$NEED_BLOCK" = 1 ]; then
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
  cp "$PROXY_DIR/.env" "$WORK/env"
  cp "$PROXY_DIR/Caddyfile" "$WORK/Caddyfile"
  [ "$NEED_ENV" = 1 ] && printf '\n# RSC Frya Fresena (deploy/install-vps.sh, %s)\nRSC_DOMAIN=%s\n' "$STAMP" "$DOMAIN" >> "$WORK/env"
  [ "$NEED_BLOCK" = 1 ] && { printf '\n'; cat "$APP_DIR/deploy/caddy-proxy-block.txt"; } >> "$WORK/Caddyfile"

  # Neue Konfiguration prüfen, ohne den laufenden Proxy anzufassen: Wegwerf-Container mit demselben Image und der neuen .env
  IMAGE=$(docker inspect -f '{{.Config.Image}}' "$PROXY_CONTAINER")
  echo "   Prüfe die neue Konfiguration mit $IMAGE ..."
  if ! docker run --rm --network none --env-file "$WORK/env" -v "$WORK/Caddyfile:/etc/caddy/Caddyfile:ro" "$IMAGE" \
       caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
    die "Die neue Proxy-Konfiguration ist ungültig. Am Proxy wurde nichts geändert."
  fi
  ok "Konfiguration gültig"

  echo
  echo "   Jetzt werden $PROXY_DIR/.env und Caddyfile ersetzt (Sicherung: *.vor-rsc-$STAMP)"
  echo "   und der Proxy mit 'docker compose up -d' neu erstellt."
  echo "   Dabei sind ALLE Seiten auf diesem VPS für ein paar Sekunden nicht erreichbar."
  ask "Proxy jetzt umstellen?" || die "Abgebrochen. Am Proxy wurde nichts geändert. Der Hub läuft schon, ist aber noch nicht erreichbar."
  cp -p "$PROXY_DIR/.env" "$PROXY_DIR/.env.vor-rsc-$STAMP"
  cp -p "$PROXY_DIR/Caddyfile" "$PROXY_DIR/Caddyfile.vor-rsc-$STAMP"
  cat "$WORK/env" > "$PROXY_DIR/.env"            # Inhalt ersetzen, Rechte und Besitzer bleiben
  cat "$WORK/Caddyfile" > "$PROXY_DIR/Caddyfile"
  # up -d erstellt den Container nur neu, wenn sich die .env geändert hat; ein geändertes Caddyfile liest erst reload ein
  (cd "$PROXY_DIR" && docker compose up -d && sleep 2 && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile)
  ok "Proxy neu gestartet und Konfiguration geladen"
  ROLLBACK=1
else
  ROLLBACK=0
fi

# ---------------------------------------------------------------- 5. Testen
say "5. Testen"
OTHER=$(grep '^HUB_DOMAIN=' "$PROXY_DIR/.env" | tail -1 | cut -d= -f2 || true)
check() { curl -fsS --max-time 10 "https://$1/healthz" 2>/dev/null; }

if [ -n "$OTHER" ]; then
  echo -n "   Bestehende Seite $OTHER "
  OK_OTHER=0
  for _ in $(seq 1 15); do if check "$OTHER" >/dev/null; then OK_OTHER=1; break; fi; echo -n "."; sleep 2; done
  if [ "$OK_OTHER" = 1 ]; then echo " antwortet"; else
    echo " antwortet NICHT"
    if [ "$ROLLBACK" = 1 ] && ask "Proxy auf den alten Stand zurücksetzen?"; then
      cp -p "$PROXY_DIR/.env.vor-rsc-$STAMP" "$PROXY_DIR/.env"
      cp -p "$PROXY_DIR/Caddyfile.vor-rsc-$STAMP" "$PROXY_DIR/Caddyfile"
      (cd "$PROXY_DIR" && docker compose up -d && sleep 2 && docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile)
      die "Proxy zurückgesetzt. Logs: cd $PROXY_DIR && docker compose logs --tail 100 caddy"
    fi
  fi
else
  warn "HUB_DOMAIN nicht in $PROXY_DIR/.env gefunden, bestehende Seite bitte selbst prüfen."
fi

echo -n "   Neue Seite https://$DOMAIN (Zertifikat holen kann bis zu einer Minute dauern) "
OK_NEW=0
for _ in $(seq 1 30); do if R=$(check "$DOMAIN"); then OK_NEW=1; break; fi; echo -n "."; sleep 3; done
if [ "$OK_NEW" = 1 ]; then echo " antwortet: $R"
else
  echo " antwortet noch nicht"
  warn "Häufigste Ursache: DNS zeigt noch nicht auf den Server. Logs: cd $PROXY_DIR && docker compose logs --tail 100 caddy"
fi

# ---------------------------------------------------------------- 6. Update-Helfer
say "6. Update-Helfer (Admin > Updates im Hub)"
if systemctl list-unit-files rsc-updater.path >/dev/null 2>&1 && systemctl is-enabled rsc-updater.path >/dev/null 2>&1; then
  ok "schon eingerichtet"
elif ask "Update-Helfer einrichten? (darf hinter dem Proxy nur prüfen und den Hub aktualisieren)"; then
  bash "$APP_DIR/deploy/updater/install.sh"
fi

say "Fertig"
echo "   Webseite: https://$DOMAIN    Hub: https://$DOMAIN/hub"
echo "   Nächste Schritte (deploy/DEPLOY.md, Schritt 6):"
echo "   - im Hub anmelden und unter Mein Profil die Zwei-Faktor-Anmeldung einrichten"
echo "   - danach in $APP_DIR/.env die Zeile TMH_ADMIN_PASSWORD leeren"
echo "   - E-Mail-Versand unter Einstellungen eintragen"
[ "$ROLLBACK" = 1 ] && echo "   Sicherungen des Proxys: $PROXY_DIR/.env.vor-rsc-$STAMP und Caddyfile.vor-rsc-$STAMP"
echo "   Updates später: cd $APP_DIR && sudo bash update.sh"
