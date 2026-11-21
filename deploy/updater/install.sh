#!/usr/bin/env bash
# Update-Helfer für den Admin-Bereich des Hubs einrichten (einmalig, auf dem VPS):
#   cd /opt/docker/apps/rsc-frya-fresena && sudo bash deploy/updater/install.sh
# Danach zeigt Admin > Updates die Versionen und kann den Hub aktualisieren (mit eigenem Caddy auch Caddy, System-Updates, Neustart).
# Entfernen: sudo systemctl disable --now rsc-updater.path rsc-updater-check.timer
set -euo pipefail
cd "$(dirname "$0")"
APP="$(cd ../.. && pwd)"   # Projektordner (zwei Ebenen über diesem Skript)

if [ "$(id -u)" != "0" ]; then echo "Bitte mit sudo starten."; exit 1; fi
command -v python3 >/dev/null || { echo "python3 fehlt (sudo apt install python3)."; exit 1; }

install -m 755 rsc-updater.py /usr/local/sbin/rsc-updater.py
# Pfad des Projekts in die Units schreiben (Standard /opt/docker/apps/rsc-frya-fresena)
for u in rsc-updater.service rsc-updater.path rsc-updater-check.service rsc-updater-check.timer; do
  sed "s|/opt/docker/apps/rsc-frya-fresena|$APP|g" "$u" > "/etc/systemd/system/$u"; chmod 644 "/etc/systemd/system/$u"
done
# Ordner für Anfrage und Status: der Hub (Benutzer 1000 im Container) darf hier schreiben, der Helfer prüft alles selbst
install -d -o 1000 -g 1000 -m 755 "$APP/data/updater"

systemctl daemon-reload
systemctl enable --now rsc-updater.path rsc-updater-check.timer
echo "-> Erste Prüfung der Versionen läuft (dauert etwa eine Minute) ..."
systemctl start rsc-updater-check.service || true
echo "Fertig. Im Hub unter Admin > Updates nachsehen."
