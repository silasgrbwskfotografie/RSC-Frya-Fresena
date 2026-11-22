# RSC Frya Fresena Hub

Öffentliche Webseite und Vereinsverwaltung des Reitvereins **RSC Frya Fresena e. V.**: Mitglieder und Beiträge, Pferde und Stall, Reitunterricht,
Hallenbelegung, Turniere und Lehrgänge, Medien, Finanzen, mit Konten, Gruppen und Rechten.
Betreiber: Silas (Admin). Sprache der Oberfläche, Texte und Antworten: **Deutsch**. Entstanden aus der Vorlage eines anderen Vereins-Hubs (gleiche Technik), alle sportartfremden Teile sind entfernt.

## Arbeitsweise mit Silas

- Texte ohne Gedankenstriche (– und —), das wirkt nach KI. `noDashes()` im Frontend.
- Rechner: Windows 11, PowerShell 5.1. Git über GitHub Desktop: `C:\Users\silas\AppData\Local\GitHubDesktop\app-3.6.10\resources\app\git\cmd\git.exe`.
- **Commit nur nach „Ja, committen“.** Pushen macht Silas in GitHub Desktop. Ordner `.claude/` nie committen.
- **Kein lokaler Testserver, keine Vorschau, keine Testskripte gegen einen laufenden Server.** Nur Syntaxprüfungen und kleine Selbsttests einzelner Funktionen, am Ende sagen, was nicht getestet ist.
- Vor Löschen, Überschreiben oder Stoppen bestehender Dienste fragen. Auf dem VPS nichts selbst ausführen, Silas macht das.
- Keine Fakten erfinden (Adresse, Vorstand, Bankdaten, Beiträge, Preise, Zeiten, Pferde, Impressum, Fotos). Fehlendes als `placeholder()` (gelb) und in `PLATZHALTER.md`.
- Keine Passwörter, Tokens oder Zugangsdaten in Code, Befehle oder Chat.

## Aufbau

- `server.py`: der komplette Server in **Python 3 Standardbibliothek** (keine pip-Pakete). `http.server` + `sqlite3`, Datenbank `data/rsc.db`.
  - `TABLES`: Schema der Fachtabellen. Neue Spalten hier eintragen, `init_db()` ergänzt sie beim Start (Migration per `ALTER TABLE`). Nur additiv ändern.
  - Konten/Rechte liegen bewusst **nicht** in `TABLES`: `users`, `sessions`, `groups`, `group_perms`, `user_groups`, `user_perms`, `user_2fa`, `secrets`, `audit_log` u. a. (`AUTH_TABLES`).
  - Generische REST-API: `GET/POST /api/<tabelle>`, `GET/PUT/DELETE /api/<tabelle>/<id>` (`api_table`). Sonderlogik dort: Hallenbelegung prüft Überschneidungen
    (`arena_conflict`, 409), gebuchte Beiträge (`fee_payments.booked_tx`) sind gesperrt, Löschen nimmt abhängige Zeilen mit (Gesundheit, Teilnahme, Nennungen, Helfer).
  - Spezielle Endpunkte: `/api/me`, `/api/profile`, `/api/dashboard`, `/api/navcounts`, `/api/fees/generate` (`fee_generate`, `fee_plan`: je Mitglied mit Beitragsart,
    Familienbeitrag `per_family` nur einmal je `members.family`, nichts doppelt), `/api/fees/book` (Sammelbuchung bezahlter Beiträge in `transactions`),
    `/api/admin/...`, `/api/upload`, `/api/backup`, `/api/restore`, `/api/migrate`, `/api/settings`, `/api/website`, `/api/maintenance`, `/api/newsletter`,
    `/api/postfach`, `/api/mailtasks`, `/api/push`, `/api/notify`, `/healthz`.
  - Erinnerungen `notify_tick` (in `notify_loop`, ab `NOTIFY_HOUR`, jede nur einmal über `notify_sent`): Pferdegesundheit (`health_remind_days` vorher und am Tag,
    nur der jüngste Eintrag je Pferd und Art), Nennungsschluss und Termin (7 und 1 Tag), Ende Einstellervertrag (30 Tage). Arten in `NOTIFY_KINDS`.
- **Öffentliche Webseite unter `/`**, Hub unter **`/hub`**. `website.py` baut die Seiten serverseitig, ohne Datenbankzugriff, nur aus
  `public_info`, `public_horses`, `public_fees`, `public_events`, `public_arena`, `public_articles` (Feld für Feld gebaut, nie Zeilen durchreichen).
  Seiten (`website.PAGES`): Start, Verein, Reitunterricht (Voltigieren schaltbar `site_volti_on`), Schulpferde, Anlage, Termine, Hallenplan (nur mit `site_arena_public` = 1),
  News, Mitgliedschaft, Kontakt, Impressum, Datenschutz; dazu `/newsletter`, `/links` (noindex), Wartungsseite. Andere übliche Adressen per 301 (`website.REDIRECTS`).
  `web/assets/`: `site.css` (als `<style>` im Kopf über `site_css()`), `site.js` (nur Einblenden und Menü), `logo.webp`. Nur Systemschriften.
  Bilder der Seiten im Hub (Webseite > Bilder, `website.IMAGE_SLOTS`, WebP aus dem Browser, `data/site`); ohne Bild Fläche in Vereinsfarbe mit Platzhalter (`page_hero`).
  Bilder und PDFs aus der Mediathek öffentlich nur signiert über `/bild/<sig>/<datei>` (`public_image`, `public_file`, Schlüssel `share_key` in `secrets`).
  Texte: Einstellungen `site_*` (`DEFAULT_SETTINGS`, leer = Platzhalter), Listen als Zeilen „links | rechts“ (`pairs()`: Vorstand, Angebote, Preise).
  Neue Standardtexte für bestehende Server: `SITE_TEXTS_VERSION` erhöhen (füllt nur leere Felder).
  Kontaktformular `POST /kontakt` -> `contact_requests` (Anliegen `CONTACT_TOPICS` in server.py, website.py und `01-grundlagen.js` gleich halten, Vorauswahl `?anliegen=probestunde|mitgliedschaft|einstellplatz|frage`).
  Newsletter mit Double-Opt-In wie gehabt (`public_newsletter`, `nl_*`).
  **Suchmaschinen**: `site_noindex` = 1 (Standard, vorläufige Domain) setzt auf jeder Seite `noindex, nofollow`, `robots.txt` sperrt alles, die Sitemap ist leer.
  Abschaltbar im Hub unter Webseite > Texte > Suchmaschinen. Strukturierte Daten (`org_ld` SportsOrganization, `event_ld`) nur ohne noindex.
- `static/`: Frontend des Hubs ohne Build (Vanilla JS, Hash-Router). `app.js` (API, Router, Formulare `editRecord`/`fieldHtml`, Rechte `can`, `PAGE_AREA`, Menü, Tab-Leiste, Darstellung hell/dunkel),
  `pages/*.js` mit gemeinsamem Bereich, Reihenfolge über die `<script>`-Liste in `index.html`. `01-grundlagen.js`: Auswahllisten, Feld-Definitionen `F.<tabelle>`, Hilfen
  (`expandArena` wie `arena_expand` im Server, `simpleTable`, `dueBadge`, `downloadCsv`). Seiten: `02-uebersicht` (Widgets je Konto), `18-mitglieder-finanzen` (Mitglieder, Beiträge,
  Arbeitsdienste, Finanzen), `30-stall` (Pferde mit Gesundheit, Boxen, Einsteller), `31-unterricht` (Reitstunden mit Teilnahme und Anwesenheit, Hallenbelegung),
  `32-veranstaltungen` (Turniere, Lehrgänge, Nennungen, Ergebnisse, Helferplan), dazu Postfach, News, Mediathek, Webseite, Newsletter, Konten, Einstellungen, Admin.
  Feld-Optionen als Text oder `[Wert, Beschriftung]` (z. B. Wochentag `1` bis `7`). Farben nur über die Variablen oben in `style.css`
  (`--red` heißt aus der Vorlage so und ist die Akzentfarbe Tannengrün, `--red-text` Warnungen, `--gold`).
- Betrieb: `Dockerfile` (Port 8770, Benutzer 1000), `docker-compose.yml` (mit eigenem Caddy `rsc-caddy`), `docker-compose.proxy.yml` (hinter dem zentralen Proxy, nur `expose`, Netzwerk `proxy` external,
  Container `rsc-frya-hub`), `Caddyfile`, `deploy/caddy-proxy-block.txt` (Block für `/opt/docker/proxy/Caddyfile`, Variable `RSC_DOMAIN`), `.env` (nicht im Repo),
  `setup.sh` (`--proxy`), `update.sh`, `deploy/install-vps.sh` (komplette Installation hinter dem Proxy: klont, ruft `setup.sh --proxy`, ergänzt Proxy-`.env` und Caddyfile
  mit Sicherung, prüft im Wegwerf-Container, Proxy-Neustart nur nach Rückfrage, Test beider Seiten mit Zurückrollen; privates Repository über Deploy-Key
  `/root/.ssh/rsc_frya_deploy` nur per `core.sshCommand` im Repository, nie global), `deploy/installiere-vps.ps1` (Start vom PC: scp und ssh, ASCII ohne Umlaute
  wegen PowerShell 5.1), `server-haerten.sh` (nur frischer Server), Update-Helfer `deploy/updater/` (`rsc-updater.py`, hinter dem Proxy nur „check“ und „hub“,
  ebenso `UPD_ACTIONS` im Server). Anleitung `deploy/DEPLOY.md`. Projektordner auf dem VPS: `/opt/docker/apps/rsc-frya-fresena`.
- Daten zur Laufzeit in `data/` (nicht im Repo): `rsc.db`, `media/`, `site/`, `backups/`, `updater/`.

## Rechte-Modell

- Bereiche (`AREAS` in server.py, `AREA_SHORT` in `17-profil.js`, `PAGE_AREA` in `app.js`): `verein`, `mitglieder`, `finanzen`, `unterricht`, `stall`, `veranstaltungen`, `medien`, `postfach`, `wartung`.
- Stufen: 0 = kein Zugriff, 1 = Lesen, 2 = Bearbeiten. Admin (`users.is_admin`) darf alles. Effektiv = Ausnahme der Person (`user_perms`), sonst Maximum der Gruppen.
- Standardgruppen (`DEFAULT_GROUPS`, nur beim ersten Start): Vorstand, Kasse, Reitlehrer, Stall, Turnierteam, Medien, Wartung.
- `TABLE_AREA` ordnet jede Tabelle einem Bereich zu, `TABLE_AREAS_ANY` erlaubt mehrere (Beiträge: Mitglieder|Finanzen, Einsteller: Stall|Finanzen,
  Arbeitsdienste: Mitglieder|Stall|Veranstaltungen, Anfragen: Mitglieder|Verein|Unterricht|Stall, Hallenbelegung: Unterricht|Stall|Veranstaltungen). Im Frontend `'a|b'` in `PAGE_AREA`/`data-area`.
- `SHARED_READ`: Nachschlage-Tabellen für Auswahllisten (Mitglieder, Pferde, Boxen, Termine, Stunden, Beitragsarten, Mediathek, Hallenbelegung), lesbar mit irgendeinem Leserecht.
  Dabei blendet `AREA_FIELDS` Kontaktdaten der Mitglieder und interne Pferdedaten aus, `FINANCE_FIELDS` die Boxenmiete, `WRITE_FIELDS` Kontaktdaten der Einsteller.
- `ADMIN_TABLES` (`hub_plans`, `admin_notes`): nur Admins. `SERVER_FIELDS` setzt nur der Server.
- Wartungsmodus wie gehabt (Bereich `wartung`, zählt nicht für `any_read`/`any_write`, ebenso `postfach`).
- Neue Tabelle: in `TABLES` und `TABLE_AREA`, Felder in `F`, Seite in `static/pages/`, Menüpunkt in `index.html` mit `data-area`, Eintrag in `PAGE_AREA`, Name in `TABLE_NAMES` (Protokoll).
- **Der Server prüft jede Anfrage.** Das Frontend blendet nur aus.

## Sicherheit (nicht aufweichen)

- Ohne Sitzung erreichbar: die Webseite (`website.PAGES`, `/news/<artikel>`, `/newsletter...`, `/links`, `POST /kontakt`), `/bild/<sig>/<datei>` (nur signiert, nur Bilder und PDF),
  `/web/assets/...`, `/web/site/...`, `/signatur/<bild>.png`, `/sitemap.xml`, `/robots.txt`, `/api/public/<info|termine|pferde|beitraege>`,
  `/login`, `/setup` (nur lokal ohne Benutzer), `/invite`, `/passwort-vergessen`, `/passwort-neu`, `/healthz`, `/manifest.webmanifest`, `/sw.js`, Icons.
  Alles unter `/hub`, `/static`, `/media` und `/api` (außer `/api/public`) braucht eine Sitzung.
- Webseite: keine externen Schriften, Skripte, Tracker oder Einbettungen (ohne Ausnahme). Öffentlicher Hallenplan nie mit Namen, bei „Buchung“ ohne Titel.
  Impressum und Datenschutz sind eigene Texte (`LEGAL_IMPRESSUM`, `LEGAL_DATENSCHUTZ`), bei jeder Änderung der Vorlage den SHA-256 der alten Fassung in `OLD_LEGAL_DATENSCHUTZ`.
  Neue Datenverarbeitung (Formular, Dienst, Cookie) immer auch in der Datenschutzerklärung ergänzen.
- Domain/Basisadresse nur über `DOMAIN` in der `.env` (daraus `SITE_URL`), `base_url` nie aus `X-Forwarded-Host`.
- Schreibende Anfragen brauchen den Header `X-TMH: 1` (CSRF-Schutz). Passwörter scrypt, Sitzungen als Token-Hash, Cookie `rsc_session` HttpOnly, SameSite=Lax, Secure.
- Zwei-Faktor-Anmeldung (TOTP), Passwort vergessen, Einladungen, Sperre nach Fehlversuchen, Änderungsprotokoll `audit_log`, Serverumzug verschlüsselt (`.rschub`) wie gehabt.
- Link- und Dateifelder: `has_bad_link` lehnt gefährliche Schemata ab. Eingespielte Datenbanken über `db_check_import`.
- Content-Security-Policy in `end_headers`: Webseite nur eigene Quellen, keine Inline-Skripte (JSON-LD ist erlaubt).
- Bankdaten (IBAN) stehen bewusst nicht im Hub, nur die SEPA-Mandatsreferenz.

## Konventionen

- Hub-Design „schlicht“ (1.1): letzter Abschnitt in `static/style.css` überschreibt die älteren. Keine Schatten (außer Dialog und Aufklappmenü), kein Verlauf, kein Glas,
  kein Hover-Versatz, Systemschrift auch für Titel und Zahlen (`tabular-nums`), Karten 8 px, Knöpfe 6 px, Plaketten 4 px. Akzent nur für Hauptknöpfe, aktiven Menüpunkt, Markierungen.
  Übersicht: Kopf, Kennzahlen-Leiste (`.dash-kpis`), Widgets mit Kopfzeile (`.dash-wh`/`.dash-wb`); Widget-Schlüssel in `02-uebersicht.js` nie umbenennen (gespeicherte `dash_layout`).
- Vereinsdesign aus dem Logo: Tannengrün `#0D393A`, Gold `#B0945C`. Logo `static/logo.png` (Hub) und `web/assets/logo.webp` (Webseite) nicht verändern.
- Keine externen Abhängigkeiten ohne guten Grund (Server: nur Standardbibliothek, Frontend: kein Build).
- Versionsnummer an zwei Stellen gleich halten: `VERSION` in server.py und `APP_VERSION` in static/app.js.
- Datenbank-Änderungen nur **additiv** (neue Tabellen/Spalten). Nie Spalten umbenennen oder löschen.

## Prüfen (ohne Server)

```
python -c "import ast; ast.parse(open('server.py', encoding='utf-8').read()); ast.parse(open('website.py', encoding='utf-8').read())"
python -m pyflakes server.py website.py
node --check static/app.js; Get-ChildItem static/pages/*.js | ForEach-Object { node --check $_.FullName }
```

Selbsttests einzelner Funktionen (Wegwerf-Datenbank über `TMH_DATA`, kein Serverstart): Webseite mit leeren und vollen Daten rendern, `fee_plan`, `arena_conflict`, `public_*`.

## Ausliefern

1. Version in `server.py` und `static/app.js` hochzählen, Änderung in `CHANGELOG.md` eintragen.
2. Nach „Ja, committen“ committen, Silas pusht auf `main`.
3. Auf dem VPS (Silas): `cd /opt/docker/apps/rsc-frya-fresena && sudo bash update.sh`. Betrieb siehe `deploy/DEPLOY.md`.
