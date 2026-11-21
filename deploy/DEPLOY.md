# Betrieb: RSC Frya Fresena Hub

Webseite unter `/`, Hub unter `/hub`. Vorläufige Adresse: **https://reiten.silasgrbwskfotografie.de**.
Die Adresse steht nur in `DOMAIN` in der `.env` (und hinter dem Proxy in `RSC_DOMAIN` der Proxy-`.env`), nie im Code.

Zwei Betriebsarten:

| | A: hinter dem zentralen Proxy (VPS mit dem bestehenden Hub) | B: eigener Server mit eigenem Caddy |
|---|---|---|
| Aufruf | `sudo bash setup.sh --proxy` | `sudo bash setup.sh` |
| Compose-Datei | `docker-compose.proxy.yml` (in der `.env`: `COMPOSE_FILE=...`) | `docker-compose.yml` |
| Ports | keine, nur `expose: 8770` im Netzwerk `proxy` | Caddy belegt 80 und 443 |
| HTTPS | zentraler Caddy in `/opt/docker/proxy` | eigener Caddy (`Caddyfile`) |
| Container | `rsc-frya-hub` | `rsc-frya-hub`, `rsc-caddy` |

## A: Installation auf dem VPS hinter dem zentralen Proxy

```
Internet ──> 80/443 ──> proxy-caddy ──(Netzwerk "proxy")──> bestehender Hub (app1)
                                    └─────────────────────> rsc-frya-hub:8770
```

Regeln: kein `ports:` (nur `expose:`), Netzwerk `proxy` als `external`, eigener Container-Name `rsc-frya-hub`, Port 8770 (nicht 8765),
eigener Ordner `/opt/docker/apps/rsc-frya-fresena`. Keine neuen Ports in der Firewall öffnen. `server-haerten.sh` hier **nicht** ausführen (der VPS ist schon abgesichert).

### Schnellweg: ein Skript für die Schritte 2 bis 7

Nach dem DNS-Eintrag (Schritt 1) erledigt `deploy/install-vps.sh` den Rest in einem Lauf: Voraussetzungen und DNS prüfen, Repository klonen,
`setup.sh --proxy`, Proxy-`.env` und Caddyfile sichern und ergänzen, die neue Proxy-Konfiguration in einem Wegwerf-Container prüfen,
**nach Rückfrage** den Proxy neu erstellen, beide Seiten testen (bei Problemen mit der bestehenden Seite: Angebot zum Zurückrollen), optional den Update-Helfer einrichten.
Mehrfach ausführbar, schon Vorhandenes wird erkannt.

**Mit einem Befehl vom PC** (PowerShell im Projektordner, vorher in GitHub Desktop pushen, Platzhalter ersetzen):

```
powershell -ExecutionPolicy Bypass -File deploy\installiere-vps.ps1 -Server <ip> -Benutzer <benutzer> -Schluessel <schluesseldatei>
```

`deploy/installiere-vps.ps1` liest die Repository-Adresse aus Git, warnt bei nicht gepushten Commits, kopiert `install-vps.sh` per `scp` auf den VPS
und startet es dort per `ssh`. Alle Fragen beantwortest du im selben Fenster. Ist das Repository privat, legt das Skript einen Deploy-Key nur für dieses
Repository an, zeigt ihn zum Eintragen bei GitHub (Settings > Deploy keys, nur Lesen) und merkt ihn sich im Repository (`core.sshCommand`), damit `update.sh` ohne Rückfrage zieht.

Oder von Hand: `scp -i $env:USERPROFILE\.ssh\<schluessel> deploy\install-vps.sh <benutzer>@<server>:/tmp/`, dann auf dem VPS
`sudo bash /tmp/install-vps.sh [--repo <adresse>] [--domain <domain>]`.

Das Skript fragt: Domain (Vorgabe `reiten.silasgrbwskfotografie.de`, entfällt mit `--domain`), Repository-Adresse (entfällt mit `--repo`), Admin-Benutzername und Passwort (tippst du selbst),
die Bestätigung vor dem Neustart des Proxys und ob der Update-Helfer eingerichtet werden soll. Die Schritte 2 bis 7 unten sind der Weg von Hand.

### 1. DNS

Beim Anbieter der Domain `silasgrbwskfotografie.de`:

- **A-Eintrag** `reiten` → IP des VPS (dieselbe IP wie die bestehende Seite auf dem VPS). Bei IPv6 zusätzlich ein AAAA-Eintrag, sonst keinen anlegen.
- **MX- und TXT-Einträge (SPF, DKIM, DMARC) nicht anfassen.** Nur den neuen A-Eintrag für `reiten` setzen.

Prüfen auf dem VPS (kann je nach Anbieter bis zu einigen Stunden dauern):

```
getent hosts reiten.silasgrbwskfotografie.de
```

Erst wenn hier die IP des VPS steht, kann Caddy das Zertifikat holen.

### 2. Programm ablegen

```
sudo mkdir -p /opt/docker/apps/rsc-frya-fresena && sudo chown <benutzer>: /opt/docker/apps/rsc-frya-fresena
cd /opt/docker/apps/rsc-frya-fresena
git clone <Repository-Adresse> .
```

Bei einem privaten Repository braucht der VPS einen Lesezugang (z. B. Deploy-Key bei GitHub, nur Lesen). Den Schlüssel legst du selbst an, er gehört nicht ins Repository.

### 3. Einrichten

```
cd /opt/docker/apps/rsc-frya-fresena && sudo bash setup.sh --proxy
```

Das Skript prüft Docker, fragt Domain (`reiten.silasgrbwskfotografie.de`), Admin-Benutzername und das Admin-Passwort (du tippst es, es wird nicht angezeigt),
schreibt die `.env` (`chmod 600`, mit `COMPOSE_FILE=docker-compose.proxy.yml`) und startet den Container. Es öffnet keinen Port.

### 4. Proxy erweitern

1. In `/opt/docker/proxy/.env` diese Zeile ergänzen:
   ```
   RSC_DOMAIN=reiten.silasgrbwskfotografie.de
   ```
2. In `/opt/docker/proxy/Caddyfile` den Block aus `deploy/caddy-proxy-block.txt` einfügen (unter dem Block `(sicherheit)`, er nutzt `import sicherheit`).
3. Prüfen, bevor etwas neu geladen wird (ein Fehler im Caddyfile legt **alle** Seiten des VPS lahm):
   ```
   cd /opt/docker/proxy && docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile
   ```
   Hinweis: `validate` kennt `RSC_DOMAIN` erst nach Schritt 4, bis dahin meldet es die Variable als leer. Dann zuerst Schritt 4 und danach `docker compose logs --tail 50 caddy` ansehen.
4. Neue `.env`-Werte übernimmt Caddy nur mit neu erstelltem Container (ein reines `reload` reicht nicht).
   **Achtung: Das unterbricht auch die bestehende Seite für ein paar Sekunden.** Am besten zu einer ruhigen Zeit:
   ```
   cd /opt/docker/proxy && docker compose up -d && docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
   ```
   (`up -d` erstellt den Container nur bei geänderter `.env` neu, das neue Caddyfile liest erst `reload` ein.)

### 5. Testen

```
curl -I https://reiten.silasgrbwskfotografie.de
curl -s https://reiten.silasgrbwskfotografie.de/healthz
cd /opt/docker/proxy && docker compose logs --tail 100 caddy
```

Erwartet: Status 200, `{"ok": true, "version": "..."}`, im Log ein geholtes Zertifikat ohne Fehler.
Danach prüfen, dass die **bestehende Seite auf dem VPS weiter erreichbar** ist (deren `/healthz`).

### 6. Erste Anmeldung

1. `https://reiten.silasgrbwskfotografie.de/hub` mit dem Admin aus Schritt 3 anmelden.
2. Unter **Mein Profil** die Zwei-Faktor-Anmeldung einrichten.
3. In `/opt/docker/apps/rsc-frya-fresena/.env` die Zeile `TMH_ADMIN_PASSWORD` leeren (wird nur beim ersten Start gebraucht).
4. **Einstellungen > E-Mail-Versand** (SMTP, IMAP für das Postfach) eintragen, sobald der Mail-Anbieter feststeht.
5. **Gruppen & Rechte** prüfen, Personen unter **Konten** einladen.
6. **Webseite > Texte**: Platzhalter füllen (siehe `PLATZHALTER.md`). „Suchmaschinen ausschließen“ bleibt an, solange die Domain vorläufig ist.

### 7. Update-Helfer (optional, für Admin > Updates)

```
cd /opt/docker/apps/rsc-frya-fresena && sudo bash deploy/updater/install.sh
```

Hinter dem Proxy darf der Helfer nur „Jetzt prüfen“ und „Hub aktualisieren“. Proxy, System-Updates und Neustart des VPS bleiben beim bestehenden Server-Betrieb.

## B: Eigener Server mit eigenem Caddy

1. Frischer Debian-Server: einmalig `sudo bash server-haerten.sh` (Firewall, fail2ban, automatische Sicherheitsupdates, SSH nur mit Schlüssel).
2. DNS wie oben (A-Eintrag auf die IP dieses Servers).
3. `git clone <Repository-Adresse> /opt/docker/apps/rsc-frya-fresena`, dann dort `sudo bash setup.sh` (fragt zusätzlich die E-Mail für das Zertifikat und Umleitungen, z. B. `www.`).
4. Testen wie oben. Der Update-Helfer darf hier auch Caddy, System-Updates und Neustart.

## Täglicher Betrieb

| Was | Befehl |
|---|---|
| **Hub aktualisieren** (nach Push auf `main`) | `cd /opt/docker/apps/rsc-frya-fresena && sudo bash update.sh` |
| Status | `cd /opt/docker/apps/rsc-frya-fresena && docker compose ps` |
| Logs | `cd /opt/docker/apps/rsc-frya-fresena && docker compose logs -f --tail 100 hub` |
| Neu starten | `cd /opt/docker/apps/rsc-frya-fresena && docker compose restart` |
| Version | `curl -s https://reiten.silasgrbwskfotografie.de/healthz` |

`update.sh` sichert vorher die Datenbank (`data/backups/vor-update-...db`), holt `main` per `git pull --ff-only`, baut den Container neu und wartet auf `/healthz`.
Auf dem Server keine Dateien im Repository von Hand ändern (das Skript bricht dann ab).

## Sicherungen

- Der Hub sichert jede Nacht die Datenbank nach `data/backups/` (`rsc-JJJJ-MM-TT.db`, 14 Tage, `TMH_BACKUP_KEEP`).
- Zusätzlich regelmäßig auf den PC holen (PowerShell, Platzhalter ersetzen):
  ```
  scp -i $env:USERPROFILE\.ssh\<schluessel> -r <benutzer>@<server>:/opt/docker/apps/rsc-frya-fresena/data/backups .\RSC-Hub-Sicherung
  ```
- Im Hub: **Einstellungen > Datensicherung** (ZIP mit Daten und Bildern, ohne Konten) und **Serverumzug** (alles, verschlüsselt mit Passwort).
- `data/` nie löschen: Datenbank, Mediathek, eigene Bilder der Webseite.

## Domain später wechseln

1. DNS der neuen Domain auf den Server.
2. `DOMAIN` in `/opt/docker/apps/rsc-frya-fresena/.env` und `RSC_DOMAIN` in `/opt/docker/proxy/.env` ändern (Betrieb B: nur `DOMAIN`).
3. `docker compose up -d` im Projektordner und im Proxy-Ordner (Betrieb A).
4. Im Hub unter **Webseite > Texte > Suchmaschinen** „Suchmaschinen ausschließen“ ausschalten. Optional die alte Adresse per Umleitung auf die neue zeigen lassen.

## Notfall

- **Seite nicht erreichbar**: `docker ps` (laufen `proxy-caddy` und `rsc-frya-hub`?), dann die Logs von Proxy und Hub.
- **Zertifikat fehlt**: Proxy-Logs nach `error` durchsuchen. Häufigste Ursachen: DNS zeigt nicht auf den Server, `RSC_DOMAIN` fehlt in der Proxy-`.env` oder der Proxy wurde nur neu geladen statt mit `up -d` neu erstellt.
- **Caddyfile kaputt** (alle Seiten weg): letzte Änderung im Caddyfile rückgängig machen, `validate`, dann `docker compose up -d` im Proxy-Ordner.
- **Admin-Passwort vergessen**: in der `.env` `TMH_RESET_PASSWORD=1` und `TMH_ADMIN_PASSWORD='neues-passwort'` setzen, `docker compose up -d`,
  danach beide Zeilen wieder zurücksetzen (`0` bzw. leer).
- **Zurück auf eine ältere Version**: `git log --oneline -10`, `sudo git checkout <commit> && docker compose up -d --build`, später `sudo git checkout main && sudo bash update.sh`.
  Datenbank-Änderungen sind nur additiv, eine ältere Version läuft mit neueren Daten weiter. Im Notfall die Sicherung `vor-update-...db` einspielen (Einstellungen > Backup einspielen).
- **Hub komplett entfernen** (nur nach Rückfrage, Daten vorher sichern): Block aus dem Proxy-Caddyfile und `RSC_DOMAIN` entfernen, Proxy `up -d`, dann im Projektordner `docker compose down`.
