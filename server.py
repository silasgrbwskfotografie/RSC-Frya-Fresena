"""
RSC Frya Fresena Hub
Öffentliche Webseite und Vereinsverwaltung des Reitvereins RSC Frya Fresena e. V.:
Mitglieder, Beiträge, Pferde, Stall, Reitunterricht, Hallenbelegung, Veranstaltungen, Medien, Finanzen.

Betrieb auf dem Server: siehe deploy/DEPLOY.md (Docker Compose mit Caddy und HTTPS).
Benötigt nur Python 3.10+ (keine zusätzlichen Pakete).

Konfiguration über Umgebungsvariablen:
  TMH_HOST            Adresse, an die der Server bindet (Standard 127.0.0.1, im Container 0.0.0.0)
  TMH_PORT            Port (Standard 8770)
  TMH_DATA            Datenordner (Standard: Ordner "data" neben dieser Datei)
  TMH_SERVER          1 = Serverbetrieb (kein Browser, keine Ersteinrichtung über die Webseite)
  TMH_SECURE_COOKIES  1 = Login-Cookie nur über HTTPS (hinter Caddy immer 1)
  TMH_TRUST_PROXY     1 = Client-IP aus X-Forwarded-For übernehmen (hinter Caddy)
  TMH_ADMIN_USER      Benutzername des ersten Admins
  TMH_ADMIN_PASSWORD  Passwort des ersten Admins (wird nur genutzt, solange es keinen Benutzer gibt)
  TMH_RESET_PASSWORD  1 = Passwort von TMH_ADMIN_USER beim Start auf TMH_ADMIN_PASSWORD zurücksetzen
  TMH_BACKUP_KEEP     Anzahl täglicher Sicherungen, die aufbewahrt werden (Standard 14)
  TMH_UPDATE_URL      Rohadresse von server.py im Repository (Versionsprüfung im Hub, leer = keine Prüfung)
"""

import base64
import contextlib
import hashlib
import hmac
import secrets
import shutil
import time
import io
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import uuid
import webbrowser
import zipfile
from datetime import date, datetime, timedelta
from email.utils import parseaddr
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, parse_qs, quote
import urllib.request
import urllib.error

import website   # öffentliche Webseite (website.py, gleiche Ordnerebene)

VERSION = "1.0"
APP_NAME = "RSC Frya Fresena Hub"
ENV = os.environ.get
HOST = ENV("TMH_HOST", "127.0.0.1")
PORT = int(ENV("TMH_PORT", "8770"))
SERVER_MODE = ENV("TMH_SERVER") == "1"
SECURE_COOKIES = ENV("TMH_SECURE_COOKIES") == "1"
TRUST_PROXY = ENV("TMH_TRUST_PROXY") == "1"
BACKUP_KEEP = int(ENV("TMH_BACKUP_KEEP", "14"))
SITE_URL = (ENV("SITE_URL") or "").rstrip("/")   # öffentliche Adresse aus DOMAIN in der .env (docker-compose setzt SITE_URL)
MAX_BODY = 300 * 1024 * 1024          # 300 MB (Backup einspielen)
MAX_JSON = 60 * 1024 * 1024           # 60 MB (Bild-Upload als Base64)
SESSION_DAYS = 30
COOKIE = "rsc_session"

BASE = Path(__file__).resolve().parent
STATIC = BASE / "static"
DATA = Path(ENV("TMH_DATA") or (BASE / "data")).resolve()
MEDIA = DATA / "media"
DB_PATH = DATA / "rsc.db"
BACKUPS = DATA / "backups"
STARTED = time.time()
# Seite „Admin“ im Hub: Prüfung auf neue Versionen (nur der Kopf von server.py) und Server-Pfade. Leer = keine Prüfung (z. B. privates Repository).
UPDATE_SOURCE = ENV("TMH_UPDATE_URL", "")
REPO_URL = ENV("TMH_REPO_URL", "")
BEHIND_PROXY = ENV("COMPOSE_FILE", "") == "docker-compose.proxy.yml"   # Betrieb hinter /opt/docker/proxy (siehe deploy/DEPLOY.md)
HOST_DIR = ENV("TMH_HOST_DIR") or "/opt/docker/apps/rsc-frya-fresena"
SSH_USER = ENV("TMH_SSH_USER", "<benutzer>")   # nur für die Befehle auf der Seite Admin, aus der .env
SSH_KEY = ENV("TMH_SSH_KEY", "<schluessel>")    # Name der Schlüsseldatei am PC (ohne Endung), nur Anzeige

# Anliegen im Kontaktformular der Webseite (landet als Quelle in contact_requests), gleiche Liste in website.py (CONTACT_TOPICS)
CONTACT_TOPICS = ("Probestunde", "Mitgliedschaft", "Einstellplatz", "Allgemeine Frage")

# ---------------------------------------------------------------------------
# Datenbank-Schema
# Jede Tabelle: Spaltenname -> SQL-Typ. "id" wird automatisch angelegt.
# Neue Spalten können hier einfach ergänzt werden, sie werden beim Start
# automatisch zur bestehenden Datenbank hinzugefügt (nur additiv ändern).
# ---------------------------------------------------------------------------
TABLES = {
    "hub_plans": {                              # Planung neuer Projekte für den Hub (Seite Admin > Planung, nur Admins)
        "title": "TEXT",
        "status": "TEXT DEFAULT 'Idee'",        # Idee / Geplant / In Arbeit / Erledigt
        "priority": "TEXT DEFAULT 'Mittel'",    # Hoch / Mittel / Niedrig
        "category": "TEXT",
        "target_version": "TEXT",
        "due": "TEXT",
        "description": "TEXT",
        "steps": "TEXT",                        # Schritte, eine pro Zeile, erledigte mit [x]
        "notes": "TEXT",
        "sort": "INTEGER DEFAULT 0",
        "done_at": "TEXT",
    },
    "admin_notes": {                            # Notizen der Admins (nur Admins)
        "title": "TEXT",
        "body": "TEXT",                         # Klartext, oder bei encrypted=1 JSON {v, salt, iv, ct}: AES-GCM im Browser
        "encrypted": "INTEGER DEFAULT 0",
        "pinned": "INTEGER DEFAULT 0",
        "updated": "TEXT",
    },
    # ---------------------------------------------------------------- Mitglieder und Beiträge (Bereich mitglieder)
    "members": {
        "first_name": "TEXT",
        "last_name": "TEXT",
        "status": "TEXT DEFAULT 'Aktiv'",          # Aktiv / Passiv / Probezeit / Warteliste / Ausgetreten
        "fee_type_id": "INTEGER",                  # Beitragsart (fee_types)
        "family": "TEXT",                          # Familienname der Familienmitgliedschaft (gleicher Text = eine Familie)
        "birthdate": "TEXT",
        "email": "TEXT",
        "phone": "TEXT",
        "street": "TEXT",
        "zip": "TEXT",
        "city": "TEXT",
        "guardian": "TEXT",                        # Erziehungsberechtigte bei Minderjährigen
        "emergency_contact": "TEXT",
        "member_since": "TEXT",
        "exit_date": "TEXT",
        "payment": "TEXT DEFAULT 'Lastschrift'",   # Lastschrift / Überweisung / Bar
        "mandate_ref": "TEXT",                     # SEPA-Mandatsreferenz (Bankdaten selbst stehen nicht im Hub)
        "work_hours_due": "REAL",                  # Pflicht-Arbeitsstunden im Jahr (leer = Einstellung work_hours_default)
        "consent_photo": "INTEGER DEFAULT 0",
        "user_id": "INTEGER",                      # Konto im Hub (falls vorhanden)
        "notes": "TEXT",
    },
    "fee_types": {                              # Beitragsarten, z. B. Kinder, Jugend, Erwachsene, Familie, passiv
        "name": "TEXT",
        "amount": "REAL",                          # Betrag je Zeitraum
        "interval": "TEXT DEFAULT 'jährlich'",     # jährlich / halbjährlich / vierteljährlich / monatlich
        "admission_fee": "REAL",                   # Aufnahmegebühr (einmalig)
        "per_family": "INTEGER DEFAULT 0",         # Familienbeitrag: eine Forderung je Familie (members.family), nicht je Person
        "age_from": "INTEGER",
        "age_to": "INTEGER",
        "description": "TEXT",
        "public": "INTEGER DEFAULT 1",             # auf der Webseite unter Mitgliedschaft zeigen
        "active": "INTEGER DEFAULT 1",
        "sort": "INTEGER DEFAULT 0",
    },
    "fee_payments": {                           # Beitragsforderungen je Mitglied und Zeitraum
        "member_id": "INTEGER",
        "year": "TEXT",
        "period": "TEXT",                          # z. B. 2027, 2027 H1, 2027-03
        "amount": "REAL",
        "due": "TEXT",
        "paid": "INTEGER DEFAULT 0",
        "paid_on": "TEXT",
        "booked_tx": "INTEGER",                    # nur Server: Buchung in den Finanzen
        "notes": "TEXT",
    },
    "duties": {                                 # Arbeits- und Stalldienste: geplant und geleistet (Stunden)
        "date": "TEXT",
        "start": "TEXT",
        "end": "TEXT",
        "kind": "TEXT DEFAULT 'Arbeitsdienst'",    # Stalldienst / Arbeitsdienst / Turnierhelfer / Sonstiges
        "task": "TEXT",
        "member_id": "INTEGER",
        "name": "TEXT",                            # falls kein Mitglied verknüpft
        "hours": "REAL",
        "status": "TEXT DEFAULT 'geplant'",        # geplant / erledigt / abgesagt
        "confirmed": "INTEGER DEFAULT 0",          # Stunden bestätigt (zählen für die Pflichtstunden)
        "notes": "TEXT",
    },
    "contact_requests": {                       # Anfragen über das Kontaktformular der Webseite
        "name": "TEXT",
        "email": "TEXT",
        "phone": "TEXT",
        "message": "TEXT",
        "source": "TEXT",                          # Anliegen (CONTACT_TOPICS)
        "age": "TEXT",                             # Alter der reitenden Person (Probestunde, Mitgliedschaft)
        "experience": "TEXT",                      # Reiterfahrung
        "status": "TEXT DEFAULT 'Neu'",            # Neu / In Bearbeitung / Erledigt
        "created": "TEXT",
        "notes": "TEXT",
    },
    # ---------------------------------------------------------------- Stall (Bereich stall)
    "horses": {
        "name": "TEXT",
        "kind": "TEXT DEFAULT 'Schulpferd'",       # Schulpferd / Einsteller / Vereinspferd / Pflegepferd
        "breed": "TEXT",
        "sex": "TEXT",                             # Stute / Wallach / Hengst
        "birth_year": "TEXT",
        "height": "TEXT",                          # Stockmaß
        "color": "TEXT",
        "ueln": "TEXT",                            # Lebensnummer / Pass
        "owner": "TEXT",
        "owner_member_id": "INTEGER",
        "box_id": "INTEGER",
        "suited_for": "TEXT",                      # z. B. Anfänger, Fortgeschrittene, Voltigieren
        "lessons_max": "INTEGER",                  # höchstens so viele Einheiten pro Tag
        "photo": "TEXT",
        "public": "INTEGER DEFAULT 0",             # auf der Webseite unter Schulpferde zeigen
        "public_text": "TEXT",                     # kurzer Text für die Webseite
        "feeding": "TEXT",                         # Fütterung und Besonderheiten
        "active": "INTEGER DEFAULT 1",
        "notes": "TEXT",
    },
    "horse_health": {                           # Impfungen, Hufschmied, Tierarzt, Wurmkur, Zahnarzt
        "horse_id": "INTEGER",
        "kind": "TEXT",                            # Impfung / Hufschmied / Tierarzt / Wurmkur / Zahnarzt / Sonstiges
        "date": "TEXT",
        "next_due": "TEXT",                        # nächster Termin (Erinnerung 7 Tage vorher und am Tag)
        "by": "TEXT",
        "details": "TEXT",
        "cost": "REAL",
        "notes": "TEXT",
    },
    "boxes": {
        "name": "TEXT",
        "kind": "TEXT DEFAULT 'Box'",              # Box / Paddockbox / Offenstall / Weide
        "location": "TEXT",                        # Stallgasse, Gebäude
        "size": "TEXT",
        "rent": "REAL",                            # Monatsmiete
        "horse_id": "INTEGER",
        "active": "INTEGER DEFAULT 1",
        "notes": "TEXT",
    },
    "boarding_contracts": {                     # Einstellerverträge
        "horse_id": "INTEGER",
        "box_id": "INTEGER",
        "boarder": "TEXT",
        "member_id": "INTEGER",
        "email": "TEXT",
        "phone": "TEXT",
        "start": "TEXT",
        "end": "TEXT",
        "rent": "REAL",                            # Monatsmiete laut Vertrag
        "services": "TEXT",                        # Leistungen, eine pro Zeile
        "status": "TEXT DEFAULT 'Aktiv'",          # Angefragt / Aktiv / Gekündigt / Beendet
        "files": "TEXT",                           # JSON-Liste [{name, path, size, added}]
        "notes": "TEXT",
    },
    # ---------------------------------------------------------------- Unterricht und Belegung (Bereich unterricht)
    "lesson_groups": {                          # Reitstunden und Kurse
        "name": "TEXT",
        "kind": "TEXT DEFAULT 'Gruppenstunde'",    # Gruppenstunde / Einzelstunde / Longe / Voltigieren / Kurs
        "weekday": "TEXT",                         # 1 = Montag ... 7 = Sonntag (leer bei Kursen)
        "time": "TEXT",
        "duration": "INTEGER DEFAULT 60",          # Minuten
        "place": "TEXT",                           # aus Einstellung arena_places
        "instructor": "TEXT",
        "level": "TEXT",
        "max_riders": "INTEGER",
        "price": "TEXT",
        "start_date": "TEXT",
        "end_date": "TEXT",
        "active": "INTEGER DEFAULT 1",
        "notes": "TEXT",
    },
    "lesson_participants": {
        "group_id": "INTEGER",
        "member_id": "INTEGER",
        "name": "TEXT",                            # falls (noch) kein Mitglied, z. B. Probestunde
        "horse_id": "INTEGER",                     # Stammpferd
        "since": "TEXT",
        "active": "INTEGER DEFAULT 1",
        "notes": "TEXT",
    },
    "lesson_attendance": {
        "group_id": "INTEGER",
        "date": "TEXT",
        "participant_id": "INTEGER",
        "status": "TEXT DEFAULT 'da'",             # da / entschuldigt / fehlt
        "horse_id": "INTEGER",
        "notes": "TEXT",
    },
    "arena_bookings": {                         # Hallen- und Platzbelegung
        "place": "TEXT",
        "date": "TEXT",
        "start": "TEXT",
        "end": "TEXT",
        "kind": "TEXT DEFAULT 'Buchung'",          # Unterricht / Buchung / Sperrzeit / Veranstaltung / Pflege
        "title": "TEXT",
        "booked_by": "TEXT",
        "user_id": "INTEGER",                      # nur Server: wer gebucht hat
        "repeat_until": "TEXT",                    # wöchentlich wiederholen bis (leer = einmalig)
        "public": "INTEGER DEFAULT 1",             # im öffentlichen Hallenplan (nur Art und Titel, nie Namen)
        "notes": "TEXT",
    },
    # ---------------------------------------------------------------- Veranstaltungen (Bereich veranstaltungen)
    "events": {                                 # Turniere, Lehrgänge, Abzeichen, Vereinsleben
        "title": "TEXT",
        "kind": "TEXT DEFAULT 'Turnier'",          # Turnier / Lehrgang / Abzeichen / Vereinsleben / Versammlung / Sonstiges
        "date": "TEXT",
        "end_date": "TEXT",
        "time": "TEXT",
        "place": "TEXT",
        "description": "TEXT",
        "entry_deadline": "TEXT",                  # Nennungs- bzw. Anmeldeschluss
        "max_entries": "INTEGER",
        "price": "TEXT",
        "contact": "TEXT",
        "file": "TEXT",                            # Ausschreibung aus der Mediathek (/media/...)
        "public": "INTEGER DEFAULT 1",             # auf der Webseite unter Termine
        "notes": "TEXT",
    },
    "event_entries": {                          # Nennungen und Teilnehmende mit Ergebnis
        "event_id": "INTEGER",
        "member_id": "INTEGER",
        "name": "TEXT",
        "horse_id": "INTEGER",
        "horse_name": "TEXT",
        "class": "TEXT",                           # Prüfung bzw. Gruppe
        "status": "TEXT DEFAULT 'gemeldet'",       # gemeldet / bestätigt / abgesagt
        "fee": "REAL",
        "paid": "INTEGER DEFAULT 0",
        "result": "TEXT",                          # Platzierung
        "score": "TEXT",                           # Wertnote, Punkte, Zeit
        "notes": "TEXT",
    },
    "event_helpers": {                          # Helferplan einer Veranstaltung
        "event_id": "INTEGER",
        "task": "TEXT",
        "date": "TEXT",
        "start": "TEXT",
        "end": "TEXT",
        "slots": "INTEGER DEFAULT 1",
        "helpers": "TEXT",                         # Namen, einer pro Zeile
        "sort": "INTEGER DEFAULT 0",
        "notes": "TEXT",
    },
    # ---------------------------------------------------------------- Finanzen, Medien, Postfach
    "transactions": {                           # Einnahmen und Ausgaben
        "date": "TEXT",
        "kind": "TEXT DEFAULT 'Ausgabe'",          # Einnahme / Ausgabe
        "category": "TEXT",
        "description": "TEXT",
        "amount": "REAL",
        "member_id": "INTEGER",
        "receipt": "TEXT",
        "paid_by": "TEXT",
        "status": "TEXT DEFAULT 'Erledigt'",       # Offen / Erledigt
        "notes": "TEXT",
    },
    "media": {
        "path": "TEXT",
        "name": "TEXT",
        "tags": "TEXT",
        "created": "TEXT",
    },
    "articles": {                               # News auf der Webseite (/news), im Hub unter Medien > News
        "title": "TEXT",
        "teaser": "TEXT",
        "body": "TEXT",
        "format": "TEXT DEFAULT 'Blöcke'",         # Blöcke (JSON aus dem Editor) / Text / HTML (gefiltert: website.clean_html)
        "image": "TEXT",
        "image_alt": "TEXT",
        "photo_by": "TEXT",
        "photo_url": "TEXT",
        "date": "TEXT",
        "status": "TEXT DEFAULT 'Entwurf'",        # Entwurf / Veröffentlicht
        "notes": "TEXT",                           # intern, nie öffentlich
    },
    "nl_groups": {                              # Newsletter-Gruppen, public = auf der Webseite zur Auswahl
        "name": "TEXT",
        "description": "TEXT",
        "public": "INTEGER DEFAULT 1",
        "active": "INTEGER DEFAULT 1",
        "sort": "INTEGER DEFAULT 0",
    },
    "mail_lists": {                             # Verteiler für Mails (Postfach > Verteiler)
        "name": "TEXT",
        "addresses": "TEXT",
        "notes": "TEXT",
    },
}

# Rechtstexte der Webseite (im Hub unter Webseite > Texte > Rechtliches änderbar).
# Format: "## " Überschrift, Leerzeile = Absatz, "- " Liste, [eckige Klammern] = noch auszufüllen (auf der Webseite gelb markiert).
# {E-Mail} und {Instagram} werden durch die Einstellungen ersetzt.
LEGAL_IMPRESSUM = """## Angaben gemäß § 5 Digitale-Dienste-Gesetz (DDG)

RSC Frya Fresena e. V.
[Straße und Hausnummer]
[Postleitzahl Ort]

## Vertreten durch

Den vertretungsberechtigten Vorstand gemäß § 26 BGB: [Vorname Name (Funktion), Vorname Name (Funktion)]

## Kontakt

E-Mail: {E-Mail}
Telefon: [Telefonnummer]
Kontaktformular: /kontakt

## Registereintrag

Eingetragen im Vereinsregister.
Registergericht: [Amtsgericht]
Registernummer: [VR ...]

## Umsatzsteuer

[Umsatzsteuer-Identifikationsnummer nach § 27a UStG, falls der Verein eine hat. Sonst diesen Abschnitt löschen.]

## Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV

[Vorname Name]
[Anschrift wie oben oder eigene Anschrift]

## Verbraucherstreitbeilegung

Wir sind nicht bereit und nicht verpflichtet, an Streitbeilegungsverfahren vor einer Verbraucherschlichtungsstelle teilzunehmen.

## Haftung für Inhalte und Links

Die Inhalte dieser Webseite erstellen wir mit Sorgfalt. Für die Richtigkeit, Vollständigkeit und Aktualität können wir jedoch keine Gewähr übernehmen. Unsere Webseite enthält Links zu Webseiten Dritter. Auf deren Inhalte haben wir keinen Einfluss, dafür ist der jeweilige Anbieter verantwortlich. Bei Bekanntwerden von Rechtsverletzungen entfernen wir solche Links umgehend.

## Bildnachweis

[Fotos: Name der Fotografin oder des Fotografen, sofern nicht anders angegeben.]"""

LEGAL_DATENSCHUTZ = """## Verantwortlicher

RSC Frya Fresena e. V.
[Straße und Hausnummer], [Postleitzahl Ort]
Vertreten durch den Vorstand: [Vorname Name]
E-Mail: {E-Mail}

[Einen Datenschutzbeauftragten haben wir nicht benannt, weil wir nicht dazu verpflichtet sind. Bitte prüfen: Pflicht ab in der Regel 20 Personen, die ständig mit personenbezogenen Daten arbeiten.]

## Das Wichtigste in Kürze

- Wir setzen keine Analyse- oder Werbedienste ein, keine Tracker und keine Cookies auf der öffentlichen Webseite.
- Bilder und Programmteile liefern wir vom eigenen Server aus. Beim Besuch werden keine Daten an Google, Meta oder andere Dienste übertragen.
- Daten, die du uns über ein Formular schickst, nutzen wir nur für dein Anliegen.

## Hosting und Aufruf der Webseite

Die Webseite läuft auf einem Server bei [Hosting-Anbieter, Anschrift], Standort Deutschland. Mit dem Anbieter besteht ein Vertrag zur Auftragsverarbeitung nach Art. 28 DSGVO.

Beim Aufruf überträgt dein Browser technisch notwendige Daten wie IP-Adresse, Datum und Uhrzeit, aufgerufene Adresse und Browsertyp. Wir speichern dafür keine Zugriffsprotokolle. Zum Schutz vor Missbrauch der Formulare (zu viele Anfragen in kurzer Zeit) halten wir die IP-Adresse höchstens eine Stunde lang im Arbeitsspeicher des Servers, danach wird sie verworfen. Die Verbindung ist per HTTPS verschlüsselt.

Rechtsgrundlage ist unser berechtigtes Interesse an einer sicheren und funktionsfähigen Webseite (Art. 6 Abs. 1 lit. f DSGVO).

## Cookies

Die öffentliche Webseite setzt keine Cookies. Nur der geschützte Bereich für Vereinsmitglieder mit Zugang (/hub) setzt nach der Anmeldung ein technisch notwendiges Sitzungs-Cookie (§ 25 Abs. 2 TDDDG).

## Kontaktformular und E-Mail

Wenn du uns über das Kontaktformular schreibst, verarbeiten wir dein Anliegen (zum Beispiel Probestunde, Mitgliedschaft oder Einstellplatz), deinen Namen, deine Nachricht, freiwillig Alter und Reiterfahrung sowie E-Mail-Adresse und/oder Telefonnummer, damit wir dir antworten können. Die Anfrage speichern wir in unserer internen Vereinsverwaltung und leiten sie per E-Mail an die zuständigen Personen im Verein weiter. Wenn du eine E-Mail-Adresse angibst, schicken wir dir eine automatische Eingangsbestätigung (ohne den Inhalt deiner Nachricht). Wenn du uns direkt eine E-Mail schreibst, gilt das entsprechend.

Rechtsgrundlage ist deine Einwilligung, die du im Formular erteilst (Art. 6 Abs. 1 lit. a DSGVO), bei Anfragen zu Mitgliedschaft, Reitunterricht oder Einstellplatz außerdem die Anbahnung eines Vertragsverhältnisses (Art. 6 Abs. 1 lit. b DSGVO). Du kannst die Einwilligung jederzeit per E-Mail widerrufen. Wir löschen die Anfrage, wenn sie erledigt ist, [spätestens nach ... Monaten], soweit keine gesetzlichen Aufbewahrungspflichten bestehen.

## Versand von E-Mails

Unsere E-Mail-Adressen und den Versand von Bestätigungen betreibt [E-Mail-Anbieter, Anschrift], Serverstandort [Land]. Dabei verarbeitet der Anbieter Absender, Empfänger und Inhalt der E-Mails. Mit dem Anbieter besteht ein Vertrag zur Auftragsverarbeitung nach Art. 28 DSGVO.

## Newsletter

Auf unserer Webseite kannst du unseren Newsletter abonnieren und dabei Themen auswählen. Dafür verarbeiten wir deine E-Mail-Adresse, freiwillig deinen Namen, die gewählten Themen sowie Zeitpunkt und Wortlaut deiner Einwilligung. Die Anmeldung wird erst wirksam, wenn du den Link in unserer Bestätigungsmail anklickst (Double-Opt-In). Ohne Bestätigung löschen wir die Angaben nach spätestens 30 Tagen.

Rechtsgrundlage ist deine Einwilligung (Art. 6 Abs. 1 lit. a DSGVO). Du kannst sie jederzeit über den Link in jeder Ausgabe oder per E-Mail an uns widerrufen. Wir verschicken den Newsletter selbst über unser E-Mail-Postfach und messen nicht, ob oder wann du ihn öffnest oder auf Links klickst. Nach der Abmeldung speichern wir die Adresse mit Zeitpunkt der Anmeldung und Abmeldung bis zu drei Jahre, um nachweisen zu können, dass wir nur mit Einwilligung geschrieben haben (Art. 6 Abs. 1 lit. f DSGVO); auf Wunsch löschen wir sie früher.

## Schulpferde, Termine und Fotos auf der Webseite

Auf der Webseite zeigen wir unsere Schulpferde, Termine und Berichte aus dem Vereinsleben. Fotos, auf denen Personen erkennbar sind, veröffentlichen wir nur mit deren Einwilligung bzw. bei Minderjährigen mit Einwilligung der Erziehungsberechtigten (Art. 6 Abs. 1 lit. a DSGVO), die jederzeit widerrufen werden kann. Im öffentlichen Hallenplan stehen nur Zeiten und die Art der Belegung, keine Namen.

## Links zu anderen Angeboten

Unsere Webseite enthält Links, zum Beispiel zu Kartendiensten oder zu Ausschreibungen. Erst beim Anklicken wirst du zum jeweiligen Anbieter weitergeleitet, dort gelten dessen Datenschutzbestimmungen.

## Schriften

Die Webseite nutzt die Schriften, die auf deinem Gerät vorhanden sind. Es wird keine Verbindung zu Google Fonts oder anderen Schriftdiensten aufgebaut.

## Empfänger und Pflichtangaben

Deine Daten erhalten nur die Personen im Verein, die für dein Anliegen zuständig sind, sowie unsere Dienstleister für Hosting und E-Mail-Versand im Rahmen ihres Auftrags. Wir verkaufen keine Daten und geben sie nicht zu Werbezwecken weiter.

In den Formularen sind nur die Angaben Pflicht, die wir zur Bearbeitung brauchen (sie sind dort gekennzeichnet). Ohne sie können wir dir nicht antworten. Eine automatisierte Entscheidungsfindung oder ein Profiling (Art. 22 DSGVO) findet nicht statt.

## Deine Rechte

Du hast das Recht auf Auskunft (Art. 15 DSGVO), Berichtigung (Art. 16), Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18), Datenübertragbarkeit (Art. 20) und Widerspruch gegen Verarbeitungen auf Grundlage berechtigter Interessen (Art. 21). Eine Einwilligung kannst du jederzeit mit Wirkung für die Zukunft widerrufen. Schreib uns dazu einfach an {E-Mail}.

Du kannst dich außerdem bei einer Datenschutz-Aufsichtsbehörde beschweren. Für uns zuständig ist: [Name und Anschrift der Landesdatenschutzbehörde am Vereinssitz]

## Stand

[Monat Jahr]"""

DEFAULT_SETTINGS = {
    "club_name": "RSC Frya Fresena e. V.",
    "club_short": "RSC Frya Fresena",
    "instagram": "",
    # Öffentliche Webseite (Hub > Webseite > Texte). Leere Texte erscheinen dort als gelb markierter Platzhalter.
    "site_noindex": "1",                       # 1 = Suchmaschinen ausschließen (vorläufige Domain), Hub > Webseite > Suchmaschinen
    "site_hero_title": "Willkommen beim RSC Frya Fresena",
    "site_hero_text": "",
    "site_about": "",
    "site_history": "",
    "site_board": "",                          # Vorstand: eine Zeile pro Person, „Funktion | Name“
    "site_lessons_intro": "",
    "site_lessons_offers": "",                 # Angebote: eine Zeile pro Angebot, „Titel | Beschreibung“
    "site_lessons_prices": "",                 # Preise: eine Zeile pro Preis, „Leistung | Preis“
    "site_volti_on": "",                       # 1 = Abschnitt Voltigieren zeigen
    "site_volti_text": "",
    "site_horses_intro": "",
    "site_facility_text": "",
    "site_facility_facts": "",                 # Eckdaten der Anlage, eine pro Zeile
    "site_boarding_text": "",                  # Einstellplätze (Pensionspferde)
    "site_events_intro": "",
    "site_arena_public": "",                   # 1 = öffentlicher Hallenplan unter /hallenplan
    "site_membership_text": "",
    "site_membership_form": "",                # Beitrittsantrag: Datei aus der Mediathek (/media/...), PDF
    "site_address": "",
    "site_maps_url": "",                       # Link für Anfahrt (nur https)
    "site_phone": "",
    "site_contact_email": "",
    "site_contact_confirm": "vielen Dank für deine Nachricht! Sie ist bei uns angekommen und wir melden uns so schnell wie möglich bei dir.\n\n"
                            "Wenn du noch etwas ergänzen möchtest, antworte einfach auf diese E-Mail.",
    "site_news_cta_text": "",
    "site_news_cta_button": "Probestunde anfragen",
    "site_impressum": LEGAL_IMPRESSUM,
    "site_datenschutz": LEGAL_DATENSCHUTZ,
    "site_social_instagram": "",
    "site_social_facebook": "",
    "site_social_tiktok": "",
    "site_notice_text": "",
    "site_notice_link": "",
    "site_notice_until": "",
    # Suchmaschinen: Beschreibung unter dem Link in den Suchergebnissen
    "site_seo_title": "Reitverein",
    "site_seo_start": "",
    "site_seo_verein": "",
    "site_seo_reitunterricht": "",
    "site_seo_pferde": "",
    "site_seo_anlage": "",
    "site_seo_termine": "",
    "site_seo_news": "",
    "site_seo_mitgliedschaft": "",
    "site_seo_kontakt": "",
    "site_verify_google": "",
    "site_verify_bing": "",
    # Webseite im Hub (Bereich Medien): eigene Bilder und Aufbau der Startseite als JSON, nicht öffentlich
    "web_images": "{}",
    "web_layout": "",
    # Wartungsmodus der Webseite (nur über /api/maintenance, Recht Wartung)
    "maint_on": "",
    "maint_title": "Wir sind gleich wieder da",
    "maint_text": "Unsere Webseite wird gerade überarbeitet und ist in Kürze wieder für euch da.",
    "maint_until": "",
    # Postfach: wer bei neuen Mails benachrichtigt wird (Gruppennamen, JSON), gemeinsame Signatur
    "pf_notify_groups": '["Vorstand"]',
    "mail_signature": "Viele Grüße\n{Name}\n{Rolle}\n\nRSC Frya Fresena e. V.",
    # Reitbetrieb
    "arena_places": "Reithalle\nReitplatz",    # Orte für Unterricht und Belegung, einer pro Zeile (Platzhalter, bitte anpassen)
    "work_hours_default": "",                  # Pflicht-Arbeitsstunden pro Mitglied und Jahr (leer = keine Pflicht)
    "health_remind_days": "7",                 # Erinnerung an Impfung, Hufschmied, Wurmkur so viele Tage vorher
}
SITE_TEXTS_VERSION = "1"   # erhöhen, wenn neue Standardtexte in leere Felder bestehender Server übernommen werden sollen
# SHA-256 früherer Fassungen von LEGAL_DATENSCHUTZ. Steht eine davon unverändert im Server, wird sie beim Start ersetzt.
OLD_LEGAL_DATENSCHUTZ = set()


DB_LOCK = threading.RLock()   # exklusiv beim Einspielen eines Backups


# ---------------------------------------------------------------------------
# Bereiche und Rechte
# Stufen: 0 = kein Zugriff, 1 = Lesen, 2 = Bearbeiten. Admins dürfen alles.
# ---------------------------------------------------------------------------
AREAS = {"verein": "Verein", "mitglieder": "Mitglieder", "finanzen": "Finanzen", "unterricht": "Unterricht", "stall": "Stall",
         "veranstaltungen": "Veranstaltungen", "medien": "Medien", "postfach": "Postfach", "wartung": "Wartung"}
TABLE_AREA = {
    "members": "mitglieder", "fee_types": "mitglieder", "fee_payments": "mitglieder", "duties": "mitglieder", "contact_requests": "mitglieder",
    "horses": "stall", "horse_health": "stall", "boxes": "stall", "boarding_contracts": "stall",
    "lesson_groups": "unterricht", "lesson_participants": "unterricht", "lesson_attendance": "unterricht", "arena_bookings": "unterricht",
    "events": "veranstaltungen", "event_entries": "veranstaltungen", "event_helpers": "veranstaltungen",
    "transactions": "finanzen",
    "media": "medien", "articles": "medien", "nl_groups": "medien",
    "mail_lists": "postfach",
    "hub_plans": "admin", "admin_notes": "admin",   # kein Bereich: nur Admins (siehe ADMIN_TABLES)
}
ADMIN_TABLES = {"hub_plans", "admin_notes"}   # nur für Admins lesbar und änderbar, unabhängig von Gruppenrechten
# Tabellen, die mehrere Bereiche verwalten dürfen: es zählt das höchste Recht dieser Bereiche
TABLE_AREAS_ANY = {
    "fee_payments": ("mitglieder", "finanzen"),
    "boarding_contracts": ("stall", "finanzen"),
    "duties": ("mitglieder", "stall", "veranstaltungen"),
    "contact_requests": ("mitglieder", "verein", "unterricht", "stall"),
    "arena_bookings": ("unterricht", "stall", "veranstaltungen"),
}
# Nachschlage-Tabellen: lesbar für alle, die irgendeinen Bereich sehen (Auswahllisten: Pferd, Mitglied, Veranstaltung)
SHARED_READ = {"members", "horses", "boxes", "events", "lesson_groups", "fee_types", "media", "arena_bookings"}
# Felder, die nur mit Finanz-Leserecht sichtbar sind
FINANCE_FIELDS = {"boxes": ("rent",)}
# Felder, die nur mit Leserecht im eigenen Bereich sichtbar sind (nicht über SHARED_READ): Kontaktdaten der Mitglieder
AREA_FIELDS = {"members": ("birthdate", "email", "phone", "street", "zip", "city", "guardian", "emergency_contact", "payment", "mandate_ref",
                           "fee_type_id", "family", "notes", "work_hours_due"),
               "horses": ("ueln", "owner", "owner_member_id", "notes")}
# Kontaktdaten: nur mit Schreibrecht im Bereich der Tabelle sichtbar
WRITE_FIELDS = {"boarding_contracts": ("email", "phone")}
# Tabellen, die überhaupt nur mit Schreibrecht im Bereich lesbar sind
WRITE_ONLY_TABLES = set()
# Felder, die nur der Server setzt (beim Speichern über die API verworfen)
SERVER_FIELDS = {"fee_payments": ("booked_tx",), "arena_bookings": ("user_id",)}
# Dateien aus data/media, die der Browser direkt anzeigen darf. Alles andere wird als Download ausgeliefert.
MEDIA_INLINE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".mp4", ".mov", ".webm"}

# Seiten, die auch im Wartungsmodus erreichbar bleiben (Pflichtangaben, Newsletter-Abmeldung)
MAINT_OPEN_PAGES = ("impressum", "datenschutz", "newsletter")
MAINT_GROUP_DESC = "Wartungsmodus: sieht die Webseite trotz Wartung und darf den Modus, Text und Bild ändern"
_ALL0 = {a: 0 for a in AREAS}
DEFAULT_GROUPS = [
    ("Vorstand", "Geschäftsführender und erweiterter Vorstand", {a: 2 for a in AREAS}),
    ("Kasse", "Kassenwart und Beitragsverwaltung", {**_ALL0, "finanzen": 2, "mitglieder": 2, "stall": 1, "veranstaltungen": 1, "verein": 1}),
    ("Reitlehrer", "Reitlehrerinnen, Reitlehrer und Voltigierwarte", {**_ALL0, "unterricht": 2, "stall": 1, "veranstaltungen": 1, "verein": 1}),
    ("Stall", "Stallteam und Pferdepflege", {**_ALL0, "stall": 2, "unterricht": 1, "veranstaltungen": 1, "verein": 1}),
    ("Turnierteam", "Turniere, Lehrgänge und Helferplanung", {**_ALL0, "veranstaltungen": 2, "unterricht": 1, "medien": 1, "verein": 1}),
    ("Medien", "Webseite, News, Newsletter und Fotos", {**_ALL0, "medien": 2, "veranstaltungen": 1, "verein": 1}),
    ("Wartung", MAINT_GROUP_DESC, {**_ALL0, "wartung": 2}),
]


@contextlib.contextmanager
def db():
    """Verbindung öffnen, am Ende committen (bzw. bei Fehler zurückrollen) und schließen."""
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    MEDIA.mkdir(exist_ok=True)
    BACKUPS.mkdir(exist_ok=True)
    with db() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        # Benutzer und Sitzungen: bewusst NICHT in TABLES, damit sie nie über die allgemeine API erreichbar sind
        conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE COLLATE NOCASE, "
                     "pw_hash TEXT, role TEXT DEFAULT 'user', created_at TEXT, last_login TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER, expires REAL, "
                     "created_at TEXT, ip TEXT, user_agent TEXT)")
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        for c, t in (("display_name", "TEXT"), ("email", "TEXT"), ("is_admin", "INTEGER DEFAULT 0"), ("active", "INTEGER DEFAULT 1"),
                     ("member_id", "INTEGER"), ("invite_hash", "TEXT"), ("invite_expires", "REAL"), ("notify", "TEXT"),
                     ("reset_hash", "TEXT"), ("reset_expires", "REAL"),
                     ("login_fails", "INTEGER DEFAULT 0"), ("login_fail_at", "REAL"), ("locked_until", "REAL"),
                     ("phone", "TEXT"),                 # Telefon (bei der Einladung angegeben, nur für Admins sichtbar)
                     ("invite_profile", "TEXT"),        # Einladung: JSON mit Vorgaben für die Person
                     ("tabbar", "TEXT"),                # eigene Tab-Leiste unten am Handy: JSON-Liste mit bis zu 4 Seitennamen
                     ("dash_layout", "TEXT"),           # eigene Übersicht: JSON-Liste [{k: Widget, w: 1 oder 2}] in Reihenfolge
                     ("mail_role", "TEXT"),             # Rolle in der Mail-Signatur, legt der Admin fest
                     ("sig_phone", "TEXT")):            # Telefon in der eigenen Mail-Signatur (Mein Profil), leer = keine
            if c not in ucols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {c} {t}")
        conn.execute("CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE COLLATE NOCASE, "
                     "description TEXT, sort INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS group_perms (group_id INTEGER, area TEXT, level INTEGER, PRIMARY KEY (group_id, area))")
        conn.execute("CREATE TABLE IF NOT EXISTS user_groups (user_id INTEGER, group_id INTEGER, PRIMARY KEY (user_id, group_id))")
        conn.execute("CREATE TABLE IF NOT EXISTS user_perms (user_id INTEGER, area TEXT, level INTEGER, PRIMARY KEY (user_id, area))")
        # Geheime Schlüssel (SMTP-Passwort, Signaturschlüssel): nie über die API lesbar, nicht im Backup-Download
        conn.execute("CREATE TABLE IF NOT EXISTS secrets (key TEXT PRIMARY KEY, value TEXT)")
        # Zwei-Faktor-Anmeldung: Geheimnis und Hashes der Wiederherstellungscodes, wie users nie über die API, nicht im Backup-Download
        conn.execute("CREATE TABLE IF NOT EXISTS user_2fa (user_id INTEGER PRIMARY KEY, secret TEXT, enabled INTEGER DEFAULT 0, "
                     "pending_secret TEXT, pending_expires REAL, last_step INTEGER DEFAULT 0, recovery TEXT, enabled_at TEXT)")
        # Änderungsprotokoll: nur für Admins lesbar, nicht über die allgemeine API
        conn.execute("CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, user_id INTEGER, user_name TEXT, "
                     "action TEXT, table_name TEXT, record_id INTEGER, label TEXT, details TEXT)")
        conn.execute("CREATE INDEX IF NOT EXISTS audit_time ON audit_log (time)")
        # Bereits verschickte Erinnerungen (jede nur einmal)
        conn.execute("CREATE TABLE IF NOT EXISTS notify_sent (key TEXT PRIMARY KEY, time TEXT)")
        # Push-Abos je Gerät (Web Push): wie Konten nie über die API, nicht im Backup-Download
        conn.execute("CREATE TABLE IF NOT EXISTS push_subs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, endpoint TEXT UNIQUE, "
                     "p256dh TEXT, auth TEXT, ua TEXT, created TEXT, last_ok TEXT)")
        # Mail-Konten (Admin > E-Mails), Passwort nie über die API lesbar; Zuordnung zu Personen in mail_links
        conn.execute("CREATE TABLE IF NOT EXISTS mail_accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, address TEXT, smtp_host TEXT, "
                     "smtp_port TEXT, smtp_security TEXT, imap_host TEXT, username TEXT, password TEXT, updated TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS mail_links (account_id INTEGER, user_id INTEGER, PRIMARY KEY (account_id, user_id))")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        # Neue Mails im Posteingang (Benachrichtigung an die Gruppen aus pf_notify_groups, bis jemand sie erledigt)
        conn.execute("CREATE TABLE IF NOT EXISTS pf_inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, folder TEXT, uid INTEGER, uidvalidity TEXT, message_id TEXT, "
                     "subject TEXT, sender TEXT, received TEXT, status TEXT DEFAULT 'neu', handled_by INTEGER, handled_at TEXT, action TEXT, notified TEXT)")
        # Newsletter: Abonnenten mit Einwilligung (Double-Opt-In) und verschickte Ausgaben; nur über /api/newsletter
        conn.execute("CREATE TABLE IF NOT EXISTS nl_subscribers (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE COLLATE NOCASE, name TEXT, "
                     "status TEXT DEFAULT 'offen', groups TEXT DEFAULT '[]', pending_groups TEXT DEFAULT '[]', confirm_hash TEXT, confirm_expires REAL, "
                     "consent_at TEXT, consent_text TEXT, source TEXT, created TEXT, unsubscribed_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS nl_issues (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, body TEXT, groups TEXT DEFAULT '[]', "
                     "status TEXT DEFAULT 'entwurf', created TEXT, created_by INTEGER, sent_at TEXT, sent_count INTEGER DEFAULT 0, failed INTEGER DEFAULT 0)")
        # Mail-Aufgaben (Postfach): wer soll welche Mail bearbeiten
        conn.execute("CREATE TABLE IF NOT EXISTS mail_tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, folder TEXT, uid INTEGER, message_id TEXT, "
                     "subject TEXT, sender TEXT, user_id INTEGER, assigned_by INTEGER, note TEXT, status TEXT DEFAULT 'offen', created TEXT, "
                     "done_at TEXT, banner_seen INTEGER DEFAULT 0)")
        if conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 0:
            for i, (name, desc, perms) in enumerate(DEFAULT_GROUPS):
                gid = conn.execute("INSERT INTO groups (name, description, sort) VALUES (?, ?, ?)", (name, desc, i)).lastrowid
                for area, lvl in perms.items():
                    conn.execute("INSERT INTO group_perms (group_id, area, level) VALUES (?, ?, ?)", (gid, area, lvl))
        for table, cols in TABLES.items():
            col_sql = ", ".join(f'"{c}" {t}' for c, t in cols.items())
            conn.execute(
                f'CREATE TABLE IF NOT EXISTS "{table}" '
                f"(id INTEGER PRIMARY KEY AUTOINCREMENT, {col_sql}, "
                f"created_at TEXT, updated_at TEXT)"
            )
            existing = {r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            for c, t in cols.items():
                if c not in existing:
                    conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" {t}')
        for k, v in DEFAULT_SETTINGS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        # Webseiten-Texte: neue Standardtexte einmalig in noch leere Felder übernehmen (eigene Texte bleiben unberührt)
        if get_setting(conn, "site_texts_version") != SITE_TEXTS_VERSION:
            for k, v in DEFAULT_SETTINGS.items():
                if k.startswith("site_") and v:
                    conn.execute("UPDATE settings SET value=? WHERE key=? AND TRIM(IFNULL(value,''))=''", (v, k))
            set_setting(conn, "site_texts_version", SITE_TEXTS_VERSION)
        # Unveränderte alte Vorlage der Datenschutzerklärung durch die aktuelle ersetzen (eigene Änderungen bleiben unberührt)
        r = conn.execute("SELECT value FROM settings WHERE key='site_datenschutz'").fetchone()
        if r and r["value"] and hashlib.sha256(r["value"].encode()).hexdigest() in OLD_LEGAL_DATENSCHUTZ:
            conn.execute("UPDATE settings SET value=? WHERE key='site_datenschutz'", (LEGAL_DATENSCHUTZ,))

def row_dict(r):
    return {k: r[k] for k in r.keys()}


# Links mit gefährlichem Schema (javascript:, data: ...) werden nie gespeichert, auch nicht versteckt in Dateilisten (JSON).
# Steuerzeichen und Leerzeichen dazwischen zählen mit, so wie Browser sie beim Öffnen eines Links ignorieren.
BAD_LINK = re.compile(r"^[\x00-\x20]*(?:j[\s\x00-\x1f]*a[\s\x00-\x1f]*v[\s\x00-\x1f]*a[\s\x00-\x1f]*s[\s\x00-\x1f]*c[\s\x00-\x1f]*r[\s\x00-\x1f]*i"
                      r"[\s\x00-\x1f]*p[\s\x00-\x1f]*t|vbscript|data|file|blob)[\s\x00-\x1f]*:", re.I)


def has_bad_link(v):
    if isinstance(v, str):
        if BAD_LINK.match(v):
            return True
        if v[:1] in "[{":   # Dateilisten werden als JSON-Text gespeichert
            try:
                return has_bad_link(json.loads(v))
            except ValueError:
                return False
        return False
    if isinstance(v, dict):
        return any(has_bad_link(x) for x in v.values())
    if isinstance(v, list):
        return any(has_bad_link(x) for x in v)
    return False


def clean_values(table, data):
    cols = TABLES[table]
    out = {}
    for k, v in data.items():
        if k not in cols:
            continue
        if isinstance(v, bool):
            v = int(v)
        if v == "" and ("INTEGER" in cols[k] or "REAL" in cols[k]):
            v = None
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Benutzer, Passwörter, Sitzungen
# ---------------------------------------------------------------------------
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_pw(pw, stored):
    try:
        algo, salt, h = (stored or "").split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(pw.encode("utf-8"), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:  # noqa
        return False


def pw_problem(pw):
    if len(pw or "") < 10:
        return "Das Passwort muss mindestens 10 Zeichen lang sein."
    return ""


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def user_count():
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def check_username(username):
    username = (username or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._@-]{2,40}", username):
        raise ValueError("Benutzername: 2 bis 40 Zeichen, nur Buchstaben, Zahlen und . _ - @")
    return username


def create_user(username, password=None, is_admin=False, display_name="", email="", member_id=None):
    """Legt ein Konto an. Ohne Passwort entsteht ein Einladungslink (Rückgabe: Token)."""
    username = check_username(username)
    if password is not None:
        prob = pw_problem(password)
        if prob:
            raise ValueError(prob)
    now = datetime.now().isoformat(timespec="seconds")
    invite = None if password is not None else secrets.token_urlsafe(24)
    with db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            raise ValueError("Diesen Benutzernamen gibt es schon.")
        uid = conn.execute(
            "INSERT INTO users (username, pw_hash, is_admin, active, display_name, email, member_id, invite_hash, invite_expires, created_at) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (username, hash_pw(password) if password is not None else "", 1 if is_admin else 0, display_name or username,
             email or "", member_id, token_hash(invite) if invite else None,
             time.time() + INVITE_DAYS * 86400 if invite else None, now)).lastrowid
        if member_id:
            conn.execute("UPDATE members SET user_id=? WHERE id=?", (uid, member_id))
    return uid, invite


INVITE_DAYS = 14


def new_invite(conn, uid):
    tok = secrets.token_urlsafe(24)
    conn.execute("UPDATE users SET invite_hash=?, invite_expires=? WHERE id=?", (token_hash(tok), time.time() + INVITE_DAYS * 86400, uid))
    return tok


def effective_perms(conn, user):
    """Rechte je Bereich: Ausnahme der Person, sonst das Maximum ihrer Gruppen."""
    if user.get("is_admin"):
        return {a: 2 for a in AREAS}
    perms = {a: 0 for a in AREAS}
    for r in conn.execute("SELECT gp.area, MAX(gp.level) AS lvl FROM user_groups ug JOIN group_perms gp ON gp.group_id = ug.group_id "
                          "WHERE ug.user_id=? GROUP BY gp.area", (user["id"],)):
        if r["area"] in perms:
            perms[r["area"]] = int(r["lvl"] or 0)
    for r in conn.execute("SELECT area, level FROM user_perms WHERE user_id=?", (user["id"],)):
        if r["area"] in perms and r["level"] is not None:
            perms[r["area"]] = int(r["level"])
    return perms


def admin_count(conn):
    return conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=1 AND active=1").fetchone()[0]


def bootstrap_admin():
    user = (ENV("TMH_ADMIN_USER") or "").strip()
    pw = ENV("TMH_ADMIN_PASSWORD") or ""
    if not user or not pw:
        return
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username=?", (user,)).fetchone()
        if row and ENV("TMH_RESET_PASSWORD") == "1":
            conn.execute("UPDATE users SET pw_hash=?, is_admin=1, active=1 WHERE id=?", (hash_pw(pw), row["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
            print(f"Passwort für {user} wurde zurückgesetzt.")
            return
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            return
    try:
        create_user(user, pw, is_admin=True)
        print(f"Admin-Benutzer {user} wurde angelegt.")
    except ValueError as e:
        print("Admin-Benutzer konnte nicht angelegt werden:", e)


FAILED_LOGINS = {}
FAILED_LOCK = threading.Lock()


def login_blocked(ip):
    with FAILED_LOCK:
        now = time.time()
        hits = [t for t in FAILED_LOGINS.get(ip, []) if now - t < 600]
        FAILED_LOGINS[ip] = hits
        return len(hits) >= 8


def login_failed(ip):
    with FAILED_LOCK:
        FAILED_LOGINS.setdefault(ip, []).append(time.time())


# ---------------------------------------------------------------------------
# Sperre pro Konto: nach ACCOUNT_FAIL_MAX Fehlversuchen (Passwort oder Code) ACCOUNT_LOCK_MINUTES gesperrt, mit Info-Mail.
# Unbekannte Benutzernamen werden im Speicher genauso gezählt und gesperrt, damit die Antwort nichts über Konten verrät.
# ---------------------------------------------------------------------------
ACCOUNT_FAIL_MAX = 5
ACCOUNT_LOCK_MINUTES = 15
GHOST_FAILS = {}          # Benutzername (klein) -> [Anzahl, erster Fehlversuch, gesperrt bis]
DUMMY_PW_HASH = None      # gleiche Rechenzeit für unbekannte Benutzernamen


def dummy_check_pw(pw):
    global DUMMY_PW_HASH
    if DUMMY_PW_HASH is None:
        DUMMY_PW_HASH = hash_pw(secrets.token_hex(8))
    check_pw(pw, DUMMY_PW_HASH)


def account_locked(conn, u, username):
    """True, wenn das Konto (oder der unbekannte Benutzername) gerade gesperrt ist."""
    now = time.time()
    if u is None:
        with FAILED_LOCK:
            g = GHOST_FAILS.get((username or "").lower())
            return bool(g and g[2] > now)
    return bool(u["locked_until"] and u["locked_until"] > now)


def account_failed(conn, u, username, ip):
    """Fehlversuch zählen. Beim Erreichen der Grenze sperren und (nur bei echten Konten) die Person per Mail informieren."""
    now = time.time()
    window = ACCOUNT_LOCK_MINUTES * 60
    if u is None:
        with FAILED_LOCK:
            if len(GHOST_FAILS) > 5000:
                for k in [k for k, g in GHOST_FAILS.items() if now - g[1] > window and g[2] < now]:
                    GHOST_FAILS.pop(k, None)
            g = GHOST_FAILS.get((username or "").lower())
            if not g or now - g[1] > window:
                g = [0, now, 0]
            g[0] += 1
            if g[0] >= ACCOUNT_FAIL_MAX:
                g = [0, now, now + window]
            GHOST_FAILS[(username or "").lower()] = g
        return
    count = int(u["login_fails"] or 0)
    if not u["login_fail_at"] or now - u["login_fail_at"] > window:
        count = 0
    count += 1
    if count >= ACCOUNT_FAIL_MAX:
        conn.execute("UPDATE users SET login_fails=0, login_fail_at=NULL, locked_until=? WHERE id=?", (now + window, u["id"]))
        audit(conn, "Anmeldung", "Konto vorübergehend gesperrt (Fehlversuche)", "users", u["id"], u["display_name"] or u["username"], {"IP": ["", ip]})
        email = account_email(conn, u)
        cfg = smtp_config()
        if email and cfg["smtp_host"]:
            body = (f"Hallo {u['display_name'] or u['username']},\n\nbei deinem Konto im RSC Frya Fresena Hub (Benutzername: {u['username']}) gab es gerade "
                    f"{ACCOUNT_FAIL_MAX} fehlgeschlagene Anmeldeversuche ({datetime.now():%d.%m.%Y %H:%M}, IP-Adresse {ip}).\n"
                    f"Zum Schutz ist die Anmeldung für {ACCOUNT_LOCK_MINUTES} Minuten gesperrt. Danach geht es automatisch wieder.\n\n"
                    "Warst du das nicht? Dann ändere bitte dein Passwort, richte unter „Mein Profil“ die Zwei-Faktor-Anmeldung ein "
                    "und melde dich beim Vorstand bzw. Admin.")
            threading.Thread(target=lambda: send_mail(cfg, email, "RSC Frya Fresena Hub: Anmeldung vorübergehend gesperrt", body, footer=False), daemon=True).start()
    else:
        conn.execute("UPDATE users SET login_fails=?, login_fail_at=? WHERE id=?",
                     (count, u["login_fail_at"] if count > 1 else now, u["id"]))


def account_ok(conn, uid):
    conn.execute("UPDATE users SET login_fails=0, login_fail_at=NULL, locked_until=NULL WHERE id=?", (uid,))


# ---------------------------------------------------------------------------
# Zwei-Faktor-Anmeldung (TOTP nach RFC 6238: 30 Sekunden, 6 Ziffern, SHA-1, wie alle gängigen Authenticator-Apps)
# Geheimnis in user_2fa (nicht in TABLES, nie über die API lesbar, nicht im Backup-Download, beim Serverumzug dabei).
# Wiederherstellungscodes nur als SHA-256-Hash, jeder nur einmal gültig.
# ---------------------------------------------------------------------------
TOTP_ISSUER = "RSC Frya Fresena Hub"
RECOVERY_COUNT = 10
RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
TWOFA_TICKET_MINUTES = 5
TWOFA_POLICY_KEYS = ("twofa_admins", "twofa_finance")   # Einstellungen: Pflicht für Admins bzw. Finanz-Schreibrecht ("1")
TWOFA_TICKETS = {}        # Hash des Zwischen-Tickets -> {"uid", "expires", "tries"} (nach richtigem Passwort, vor dem Code)


def totp_code(secret_b32, step):
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8), casefold=True)
    mac = hmac.new(key, step.to_bytes(8, "big"), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    return f"{(int.from_bytes(mac[off:off + 4], 'big') & 0x7FFFFFFF) % 1000000:06d}"


def totp_match(secret_b32, code, last_step=0, now=None):
    """Passenden Zeitschritt zurückgeben (eine Abweichung von 30 Sekunden erlaubt), sonst 0. Schon benutzte Codes zählen nicht."""
    code = re.sub(r"\s", "", code or "")
    if not re.fullmatch(r"\d{6}", code) or not secret_b32:
        return 0
    t = int((now or time.time()) // 30)
    for step in (t - 1, t, t + 1):
        if step > (last_step or 0) and hmac.compare_digest(totp_code(secret_b32, step), code):
            return step
    return 0


def totp_uri(secret_b32, username):
    from urllib.parse import quote
    label = quote(f"{TOTP_ISSUER}:{username}")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(TOTP_ISSUER)}&algorithm=SHA1&digits=6&period=30"


def recovery_norm(code):
    return re.sub(r"[^a-z0-9]", "", (code or "").lower())


def recovery_new():
    """Neue Codes (Klartext für die einmalige Anzeige) und ihre Hashes (zum Speichern)."""
    codes = []
    for _ in range(RECOVERY_COUNT):
        c = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{c[:5]}-{c[5:]}")
    return codes, [token_hash(recovery_norm(c)) for c in codes]


def twofa_row(conn, uid):
    return conn.execute("SELECT * FROM user_2fa WHERE user_id=?", (uid,)).fetchone()


def twofa_enabled(conn, uid):
    r = twofa_row(conn, uid)
    return bool(r and r["enabled"] and r["secret"])


def twofa_required(conn, user, perms=None):
    """Pflicht für Admins bzw. Konten mit Finanz-Schreibrecht, je nach Einstellung."""
    perms = perms if perms is not None else effective_perms(conn, user)
    if user.get("is_admin") and get_setting(conn, "twofa_admins") == "1":
        return True
    return perms.get("finanzen", 0) >= 2 and get_setting(conn, "twofa_finance") == "1"


def twofa_check(conn, uid, code):
    """Code aus der App oder Wiederherstellungscode prüfen. Rückgabe: "totp", "recovery" oder ""."""
    r = twofa_row(conn, uid)
    if not r or not r["enabled"]:
        return ""
    step = totp_match(r["secret"], code, r["last_step"] or 0)
    if step:
        conn.execute("UPDATE user_2fa SET last_step=? WHERE user_id=?", (step, uid))
        return "totp"
    norm = recovery_norm(code)
    if len(norm) == 10:
        hashes = json.loads(r["recovery"] or "[]")
        h = token_hash(norm)
        for x in hashes:
            if hmac.compare_digest(x, h):
                hashes.remove(x)
                conn.execute("UPDATE user_2fa SET recovery=? WHERE user_id=?", (json.dumps(hashes), uid))
                return "recovery"
    return ""


def twofa_ticket_new(uid):
    tok = secrets.token_urlsafe(32)
    with FAILED_LOCK:
        now = time.time()
        for k in [k for k, v in TWOFA_TICKETS.items() if v["expires"] < now]:
            TWOFA_TICKETS.pop(k, None)
        TWOFA_TICKETS[token_hash(tok)] = {"uid": uid, "expires": now + TWOFA_TICKET_MINUTES * 60, "tries": 0}
    return tok


# ---------------------------------------------------------------------------
# Passwort vergessen: Link per E-Mail (60 Minuten gültig, nur einmal, nur der Hash wird gespeichert)
# ---------------------------------------------------------------------------
RESET_MINUTES = 60
RESET_HITS = {}          # "ip:..." bzw. "user:..." -> Zeitpunkte der Anfragen
RESET_LIMITS = {"ip": 10, "user": 3, "form": 60, "chat": 400, "cal": 300}   # pro Stunde


def reset_rate_ok(kind, key):
    with FAILED_LOCK:
        now = time.time()
        k = f"{kind}:{key}"
        hits = [t for t in RESET_HITS.get(k, []) if now - t < 3600]
        ok = len(hits) < RESET_LIMITS[kind]
        if ok:
            hits.append(now)
        RESET_HITS[k] = hits
        return ok


def account_email(conn, u):
    """E-Mail des Kontos, sonst die des verknüpften Mitglieds."""
    if u["email"]:
        return u["email"]
    if u["member_id"]:
        m = conn.execute("SELECT email FROM members WHERE id=?", (u["member_id"],)).fetchone()
        return (m["email"] or "").strip() if m else ""
    return ""


def request_password_reset(login, base_url):
    """Sucht das Konto zu Benutzername oder E-Mail und schickt den Link. Gibt nach außen nie preis, ob es das Konto gibt."""
    login = (login or "").strip()
    if not login:
        return
    with db() as conn:
        u = conn.execute("SELECT * FROM users WHERE active=1 AND username=? COLLATE NOCASE", (login,)).fetchone()
        if not u and "@" in login:
            u = conn.execute("SELECT * FROM users WHERE active=1 AND lower(email)=lower(?)", (login,)).fetchone() or conn.execute(
                "SELECT u.* FROM users u JOIN members m ON m.id = u.member_id WHERE u.active=1 AND lower(m.email)=lower(?)", (login,)).fetchone()
        if not u:
            return
        email = account_email(conn, u)
        if not email or not reset_rate_ok("user", u["id"]):
            return
        tok = secrets.token_urlsafe(32)
        conn.execute("UPDATE users SET reset_hash=?, reset_expires=? WHERE id=?", (token_hash(tok), time.time() + RESET_MINUTES * 60, u["id"]))
        audit(conn, "Passwort vergessen", "Link angefordert", "users", u["id"], u["display_name"] or u["username"])
    cfg = smtp_config()
    if not cfg["smtp_host"]:
        return
    link = f"{cfg['hub_url'] or base_url}/passwort-neu/{tok}"
    body = (f"Hallo {u['display_name'] or u['username']},\n\nfür dein Konto im RSC Frya Fresena Hub (Benutzername: {u['username']}) wurde ein neues Passwort angefordert.\n"
            f"Über diesen Link legst du es fest. Er gilt {RESET_MINUTES} Minuten und funktioniert nur einmal:\n\n{link}\n\n"
            "Wenn du das nicht warst, ignoriere diese Mail einfach. Dein bisheriges Passwort bleibt dann gültig.")
    threading.Thread(target=lambda: send_mail(cfg, email, "RSC Frya Fresena Hub: Passwort zurücksetzen", body, footer=False), daemon=True).start()




































# ---------------------------------------------------------------------------
# Dateitypen, geheime Schlüssel (Tabelle secrets: nie über die API lesbar, nicht im Backup-Download)
# ---------------------------------------------------------------------------
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/webp", ".webp")   # fehlt in manchen Systemen
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}




def secret_get(conn, key):
    r = conn.execute("SELECT value FROM secrets WHERE key=?", (key,)).fetchone()
    return r["value"] if r and r["value"] is not None else ""


def secret_set(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO secrets (key, value) VALUES (?, ?)", (key, value))


def share_key():
    """Schlüssel für signierte Bild- und Dateilinks der Webseite (/bild/<sig>/<datei>), einmal erzeugt, nie über die API."""
    with db() as conn:
        k = secret_get(conn, "share_key")
        if not k:
            k = secrets.token_hex(32)
            secret_set(conn, "share_key", k)
    return k


# ---------------------------------------------------------------------------
# Öffentliche Daten für die Webseite (/api/public und die Seiten unter /).
# Nur hier definierte Felder gehen raus, jede Antwort wird Feld für Feld neu gebaut.
# Nie: Finanzen, Mitglieder, Kontakte, Konten, Notizen, interne Dateien.
# ---------------------------------------------------------------------------
PUBLIC_SETTINGS = ("club_name", "club_short", "instagram", *(k for k in DEFAULT_SETTINGS if k.startswith("site_")))
PUBLIC_FILE_EXT = IMAGE_EXT | {".pdf"}


def public_image(path, key):
    """Signierter Link auf ein einzelnes Bild aus data/media (sonst leer)."""
    name = (path or "").rsplit("/", 1)[-1]
    if not (path or "").startswith("/media/") or Path(name).suffix.lower() not in IMAGE_EXT or not (MEDIA / name).is_file():
        return ""
    return f"/bild/{share_sig(key, name)}/{name}"


def public_file(path, key):
    """Wie public_image, aber auch PDF (Ausschreibung, Beitrittsantrag)."""
    name = (path or "").rsplit("/", 1)[-1]
    if not (path or "").startswith("/media/") or Path(name).suffix.lower() not in PUBLIC_FILE_EXT or not (MEDIA / name).is_file():
        return ""
    return f"/bild/{share_sig(key, name)}/{name}"






























MM = 2.835   # Punkte je Millimeter
LETTER_ML, LETTER_MR = 25 * MM, 20 * MM   # Ränder wie die Briefvorlage (links 25 mm, rechts 20 mm)
LETTER = {   # Absenderangaben für Mails und Newsletter, überschreibbar über Einstellungen letter_title, letter_sub, letter_return, letter_footer
    "title": "RSC Frya Fresena e. V.", "sub": "",
    "return": "RSC Frya Fresena e. V.",
    "footer": "RSC Frya Fresena e. V."}


def letter_text(conn, key):
    return (get_setting(conn, "letter_" + key) or "").strip() or LETTER[key]






































def public_info():
    with db() as conn:
        return {k: get_setting(conn, k) for k in PUBLIC_SETTINGS}
















def article_slug(a):
    """Adresse eines Artikels: /news/<id>-<titel-ohne-sonderzeichen>. Die Zahl vorne zählt, der Rest ist nur für Leser und Google."""
    t = (a["title"] or "").lower()
    for k, v in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(k, v)
    return f"{a['id']}-" + (re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:60].strip("-") or "artikel")


ARTICLE_BLOCKS = {   # Blocktyp -> erlaubte Felder (alles andere wird verworfen)
    "p": ("text",), "h": ("text",), "quote": ("text", "by"), "list": ("items",), "highlight": ("text",),
    "cta": ("text", "label", "link"), "image": ("src", "alt", "caption"), "note": ("text",),
}


def article_blocks(raw, key):
    """Blöcke aus dem Editor prüfen: nur bekannte Typen und Felder, Längen begrenzt, Bilder nur aus der Mediathek (signiert), Links geprüft."""
    try:
        data = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for b in data if isinstance(data, list) else []:
        if not isinstance(b, dict) or b.get("t") not in ARTICLE_BLOCKS:
            continue
        row = {"t": b["t"]}
        for f in ARTICLE_BLOCKS[b["t"]]:
            v = b.get(f)
            if f == "items":
                row[f] = [str(x)[:500] for x in (v if isinstance(v, list) else str(v or "").splitlines()) if str(x).strip()][:30]
            else:
                row[f] = str(v or "").strip()[:4000]
        if row["t"] == "image":
            row["src"] = public_image(row["src"], key)
            if not row["src"]:
                continue
        if row["t"] == "cta":
            link = row["link"]
            row["link"] = link if re.fullmatch(r"(https?://[^\s\"'<>]+|/(?!/)[^\s\"'<>]*|mailto:[^\s\"'<>]+)", link) else ""
        if any(row[f] for f in ARTICLE_BLOCKS[row["t"]]):
            out.append(row)
        if len(out) >= 80:
            break
    return out


def public_articles():
    """Veröffentlichte Artikel ab ihrem Datum, neueste zuerst. Nur feste Felder, keine Notizen."""
    key = share_key()
    today = datetime.now().strftime("%Y-%m-%d")
    with db() as conn:
        rows = conn.execute("SELECT * FROM articles WHERE status='Veröffentlicht' AND IFNULL(date,'') != '' AND date <= ? "
                            "AND IFNULL(title,'') != '' ORDER BY date DESC, id DESC", (today,)).fetchall()
    out = []
    for a in rows:
        fmt = a["format"] if a["format"] in ("Blöcke", "HTML") else "Text"
        blocks = article_blocks(a["body"], key) if fmt == "Blöcke" else []
        body = "\n\n".join(b.get("text", "") for b in blocks if b["t"] in ("p", "h")) if fmt == "Blöcke" else (a["body"] or "")
        out.append(article_public(a, key, fmt, body, blocks))
    return out


def photo_link(v):
    """Link zum Fotografen: https-Adresse oder @instagram-Name, sonst leer."""
    v = (v or "").strip()
    m = re.fullmatch(r"@([A-Za-z0-9._]{1,30})", v)
    if m:
        return f"https://www.instagram.com/{m.group(1)}/"
    if re.fullmatch(r"[A-Za-z0-9.-]+\.[a-z]{2,}(/\S*)?", v):   # z. B. beispiel.de ohne https://
        v = "https://" + v
    return v if re.fullmatch(r"https://[^\s\"'<>]+", v) else ""


def article_public(a, key, fmt, body, blocks):
    return {"id": a["id"], "slug": article_slug(a), "title": a["title"], "teaser": a["teaser"] or "", "body": body,
            "format": fmt, "blocks": blocks,
            "date": a["date"], "image": public_image(a["image"], key), "image_alt": a["image_alt"] or "",
            "photo_by": (a["photo_by"] or "").strip()[:80], "photo_url": photo_link(a["photo_url"]),
}


def public_horses():
    """Schulpferde mit Freigabe für die Webseite: nur Name, Rasse, Geschlecht, Jahrgang, Größe, Text und signiertes Foto."""
    key = share_key()
    with db() as conn:
        rows = conn.execute("SELECT * FROM horses WHERE active=1 AND public=1 ORDER BY name COLLATE NOCASE").fetchall()
    return [{"name": (h["name"] or "").strip()[:60], "kind": h["kind"] or "", "breed": (h["breed"] or "")[:60], "sex": (h["sex"] or "")[:20],
             "birth_year": (h["birth_year"] or "")[:4], "height": (h["height"] or "")[:20], "suited_for": (h["suited_for"] or "")[:120],
             "text": (h["public_text"] or "")[:1500], "photo": public_image(h["photo"], key)} for h in rows if (h["name"] or "").strip()]


def public_fees():
    """Beitragsarten mit Freigabe (Webseite > Mitgliedschaft)."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM fee_types WHERE active=1 AND public=1 ORDER BY IFNULL(sort,0), name COLLATE NOCASE").fetchall()
    return [{"name": r["name"] or "", "amount": r["amount"], "interval": r["interval"] or "", "admission_fee": r["admission_fee"],
             "age_from": r["age_from"], "age_to": r["age_to"], "description": (r["description"] or "")[:500]} for r in rows if r["name"]]


def public_events():
    """Kommende Veranstaltungen mit Freigabe (ab gestern), ohne Notizen, Teilnehmende oder Helfer."""
    key = share_key()
    since = (date.today() - timedelta(days=1)).isoformat()
    with db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE public=1 AND IFNULL(date,'') != '' AND IFNULL(end_date, date) >= ? "
                            "ORDER BY date, time", (since,)).fetchall()
    return [{"id": r["id"], "title": r["title"] or "", "kind": r["kind"] or "", "date": r["date"], "end_date": r["end_date"] or "",
             "time": r["time"] or "", "place": r["place"] or "", "description": (r["description"] or "")[:3000],
             "entry_deadline": r["entry_deadline"] or "", "price": (r["price"] or "")[:60], "file": public_file(r["file"], key)}
            for r in rows if r["title"]]


def arena_expand(rows, start, end):
    """Belegungen (auch wöchentliche Wiederholungen) im Zeitraum start..end (ISO-Daten) als Einzeltermine."""
    out = []
    for r in rows:
        d0 = r["date"] or ""
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d0):
            continue
        until = r["repeat_until"] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["repeat_until"] or "") else d0
        d = date.fromisoformat(d0)
        while d.isoformat() <= min(until, end):
            if d.isoformat() >= start:
                out.append((d.isoformat(), r))
            d += timedelta(days=7)
    return sorted(out, key=lambda x: (x[0], x[1]["start"] or ""))


def public_arena(days=14):
    """Öffentlicher Hallenplan (nur wenn site_arena_public = 1): Ort, Datum, Zeit, Art und Titel, nie Namen."""
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    with db() as conn:
        if get_setting(conn, "site_arena_public") != "1":
            return []
        rows = conn.execute("SELECT * FROM arena_bookings WHERE public=1 AND date <= ? AND IFNULL(NULLIF(repeat_until,''), date) >= ?", (end, start)).fetchall()
    return [{"date": d, "place": r["place"] or "", "start": r["start"] or "", "end": r["end"] or "", "kind": r["kind"] or "",
             "title": (r["title"] or "")[:60] if r["kind"] != "Buchung" else ""} for d, r in arena_expand(rows, start, end)]


def arena_conflict(conn, data, rid=None):
    """Überschneidung einer Belegung am selben Ort (auch mit Wiederholungen) -> Text des Konflikts, sonst leer."""
    place, d0, s, e = data.get("place") or "", data.get("date") or "", data.get("start") or "", data.get("end") or ""
    if not (place and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d0) and re.fullmatch(r"\d{2}:\d{2}", s) and re.fullmatch(r"\d{2}:\d{2}", e)):
        return ""
    if e <= s:
        return "Das Ende muss nach dem Beginn liegen."
    until = data.get("repeat_until") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", data.get("repeat_until") or "") else d0
    mine = {d for d, _ in arena_expand([{"date": d0, "repeat_until": until, "start": s}], d0, until)}
    rows = conn.execute("SELECT * FROM arena_bookings WHERE place=? AND id != ? AND date <= ? AND IFNULL(NULLIF(repeat_until,''), date) >= ?",
                        (place, rid or 0, until, d0)).fetchall()
    for d, r in arena_expand(rows, d0, until):
        if d in mine and (r["start"] or "") < e and s < (r["end"] or ""):
            return f"{place} ist am {d[8:]}.{d[5:7]}. von {r['start']} bis {r['end']} schon belegt ({r['kind'] or 'Buchung'}{': ' + r['title'] if r['title'] else ''})."
    return ""


def fee_plan(members, fee_types, existing):
    """Welche Beitragsforderungen fehlen? members: Zeilen mit id, status, fee_type_id, family; fee_types: {id: Zeile};
    existing: Mitglieds-IDs, die für den Zeitraum schon eine Forderung haben. Liefert [(member_id, betrag)].
    Familienbeitrag (per_family): nur ein Mitglied je Familie (das mit der kleinsten ID), gleiche Familie = gleicher Text in members.family."""
    fam_done = {(m["family"] or "").strip().lower() for m in members if m["id"] in existing and (m["family"] or "").strip()}
    out = []
    for m in sorted(members, key=lambda x: x["id"]):
        ft = fee_types.get(m["fee_type_id"])
        if m["status"] not in ("Aktiv", "Passiv", "Probezeit") or not ft or ft["amount"] is None or m["id"] in existing:
            continue
        fam = (m["family"] or "").strip().lower()
        if ft["per_family"] and fam:
            if fam in fam_done:
                continue
            fam_done.add(fam)
        out.append((m["id"], ft["amount"]))
    return out


def fee_generate(period, due, user):
    with db() as conn:
        members = conn.execute("SELECT id, status, fee_type_id, family FROM members").fetchall()
        fts = {r["id"]: r for r in conn.execute("SELECT * FROM fee_types WHERE active=1")}
        existing = {r["member_id"] for r in conn.execute("SELECT member_id FROM fee_payments WHERE period=?", (period,))}
        plan = fee_plan(members, fts, existing)
        now = datetime.now().isoformat(timespec="seconds")
        for mid, amount in plan:
            conn.execute("INSERT INTO fee_payments (member_id, year, period, amount, due, paid, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                         (mid, period[:4], period, amount, due, now, now))
        audit(conn, user, "Beiträge angelegt", "fee_payments", None, period, {"neu": ["", len(plan)]})
    return len(plan), len(members) - len(plan)


def share_sig(key, name):
    return hmac.new(key.encode(), name.encode(), hashlib.sha256).hexdigest()[:40]












































# ---------------------------------------------------------------------------
# Webseite im Hub verwalten: Bilder (vom Browser als WebP in 640/1280/1920 erzeugt), Aufbau der Startseite
# ---------------------------------------------------------------------------
SITE_DIR = DATA / "site"
SITE_WIDTHS = (640, 960, 1280, 1920)
SITE_IMAGE_MAX = 4 * 1024 * 1024


def web_images(conn):
    try:
        v = json.loads(get_setting(conn, "web_images") or "{}")
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def web_sitemap(conn, raw=None):
    """Sitemap-Einstellung {schlüssel: [an, priorität]} für alle website.SITEMAP_PAGES, Unbekanntes und Ungültiges verworfen.
    raw: neue Werte aus dem Hub (sonst die gespeicherten)."""
    if raw is None:
        try:
            raw = json.loads(get_setting(conn, "web_sitemap") or "{}")
        except ValueError:
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k, _, prio in website.SITEMAP_PAGES:
        v = raw.get(k)
        ok = isinstance(v, list) and len(v) == 2 and str(v[1]) in website.SITEMAP_PRIOS
        out[k] = [bool(v[0]), str(v[1])] if ok else [True, prio]
    return out


def web_layout(conn):
    """Reihenfolge und Sichtbarkeit der Startseiten-Abschnitte: [[schlüssel, sichtbar], ...], unbekannte verworfen, neue ergänzt."""
    try:
        raw = json.loads(get_setting(conn, "web_layout") or "[]")
    except ValueError:
        raw = []
    out, seen = [], set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, list) and len(item) == 2 and item[0] in website.START_SECTIONS and item[0] not in seen:
            out.append([item[0], bool(item[1])])
            seen.add(item[0])
    keys = list(website.START_SECTIONS)
    for i, k in enumerate(keys):   # neue Abschnitte an ihren Standardplatz, hinter den Abschnitt davor
        if k not in seen:
            prev = next((j for j, (x, _) in enumerate(out) if i and x == keys[i - 1]), None)
            out.insert(prev + 1 if prev is not None else len(out), [k, True])
            seen.add(k)
    return out


# ---------------------------------------------------------------- Menü der Webseite (Hub > Webseite > Menü)
NAV_BUILTIN = [("", "Start"), ("verein", "Verein"), ("reitunterricht", "Reitunterricht"), ("pferde", "Schulpferde"), ("anlage", "Anlage"),
               ("termine", "Termine"), ("hallenplan", "Hallenplan"), ("news", "News"), ("mitgliedschaft", "Mitgliedschaft"), ("kontakt", "Kontakt")]
NAV_HIDDEN_DEFAULT = set()   # ohne Einstellung nicht im Menü (Hallenplan erscheint ohnehin nur mit site_arena_public = 1, siehe public_nav)
NAV_BUTTON_DEFAULT = {"kontakt"}
SOCIAL_HOSTS = {"site_social_instagram": ("instagram.com",), "site_social_facebook": ("facebook.com", "fb.com"), "site_social_tiktok": ("tiktok.com",)}


def web_nav(conn):
    """Alle Seiten des Menüs in gespeicherter Reihenfolge, neue hinten: [{key, title, label, show, button}]."""
    try:
        raw = json.loads(get_setting(conn, "web_nav") or "[]")
    except ValueError:
        raw = []
    titles = dict(NAV_BUILTIN)
    out, seen = [], set()
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, dict) and it.get("key") in titles and it["key"] not in seen:
            out.append({"key": it["key"], "title": titles[it["key"]], "show": bool(it.get("show")), "label": str(it.get("label") or "")[:40], "button": bool(it.get("button"))})
            seen.add(it["key"])
    for k, t in NAV_BUILTIN:
        if k not in seen:
            out.append({"key": k, "title": t, "show": k not in NAV_HIDDEN_DEFAULT, "label": "", "button": k in NAV_BUTTON_DEFAULT})
    return out


LINKS_MAX = 20   # Link-Seite /links (z. B. für die Instagram-Bio): höchstens so viele Knöpfe
LINKS_DEFAULT = [{"label": "Zur Webseite", "url": "/", "hot": True}, {"label": "Termine", "url": "/termine", "hot": False},
                 {"label": "Probestunde anfragen", "url": "/kontakt?anliegen=probestunde#formular", "hot": False}, {"label": "News", "url": "/news", "hot": False}]


def link_url_ok(url):
    """Ziel eines Knopfs auf der Link-Seite: https-Adresse oder eigene Seite (/...), ohne Leer- und Steuerzeichen."""
    url = (url or "").strip()
    if len(url) > 500 or any(c in url for c in ' "\'<>`\\') or any(ord(c) < 32 for c in url):
        return ""
    return url if re.match(r"https://[a-z0-9-]+(\.[a-z0-9-]+)+(/|\?|#|$)", url, re.I) or re.match(r"/(?!/)", url) else ""


def web_links(conn):
    """Knöpfe der Link-Seite: [{label, url, hot}]. Ohne Einstellung die Standardliste."""
    raw = get_setting(conn, "web_links")
    if not raw:
        return [dict(x) for x in LINKS_DEFAULT]
    try:
        items = json.loads(raw)
    except ValueError:
        items = []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and str(it.get("label") or "").strip() and link_url_ok(it.get("url")):
            out.append({"label": str(it["label"]).strip()[:60], "url": link_url_ok(it["url"]), "hot": bool(it.get("hot"))})
    return out[:LINKS_MAX]


def social_url(key, url):
    """Nur https-Adressen der passenden Plattform, ohne Leer- und Steuerzeichen."""
    url = (url or "").strip()
    host = re.match(r"https://(?:[a-z0-9-]+\.)*([a-z0-9-]+\.[a-z]+)(?:/|$)", url, re.I)
    if not host or any(c in url for c in ' "\'<>`') or host.group(1).lower() not in SOCIAL_HOSTS.get(key, ()):
        return ""
    return url[:300]


def public_nav():
    """Menü für website.layout: sichtbare Einträge (Hallenplan nur, wenn er öffentlich ist) und die Social-Media-Symbole."""
    with db() as conn:
        items = web_nav(conn)
        arena = get_setting(conn, "site_arena_public") == "1"
        social = [(k[12:], social_url(k, get_setting(conn, k))) for k in SOCIAL_HOSTS]
    out = [{"href": "/" + it["key"], "label": it["label"] or it["title"], "button": it["button"], "slug": it["key"]}
           for it in items if it["show"] and (it["key"] != "hallenplan" or arena)]
    return {"items": out, "social": [{"kind": k, "url": u} for k, u in social if u]}


def public_site():
    """Eigene Bilder (nur Dateiname, Maße, Alt-Text) und Aufbau der Startseite für website.py."""
    with db() as conn:
        imgs, layout = web_images(conn), web_layout(conn)
    out = {}
    for slot, m in imgs.items():
        if slot not in website.IMAGE_SLOTS or not isinstance(m, dict):
            continue
        files = {int(w): f for w, f in (m.get("files") or {}).items() if str(w).isdigit() and (SITE_DIR / str(f)).is_file()}
        out[slot] = {"alt": str(m.get("alt") or ""), "w": int(m.get("w") or 0), "h": int(m.get("h") or 0),
                     "src": {w: f"/web/site/{f}" for w, f in sorted(files.items())}}
    return {"images": out, "layout": layout}


def website_overview(text_keys):
    """Alles für die Seite „Webseite“ im Hub: Texte, Bilder, Aufbau und eine Checkliste offener Punkte."""
    std = website.image_meta(standard=True)
    site = public_site()
    with db() as conn:
        texts = {k: get_setting(conn, k) for k in text_keys}
        imgs = web_images(conn)
        today = datetime.now().date().isoformat()
        horses = conn.execute("SELECT name, photo, public_text FROM horses WHERE active=1 AND public=1").fetchall()
        fees = conn.execute("SELECT COUNT(*) FROM fee_types WHERE active=1 AND public=1").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM events WHERE public=1 AND IFNULL(date,'') >= ?", (today,)).fetchone()[0]
        noindex = get_setting(conn, "site_noindex") == "1"
    images = []
    for slot, (label, where) in website.IMAGE_SLOTS.items():
        if slot == website.MAINT_SLOT:   # eigenes Bild der Wartungsseite, gepflegt unter Verwaltung > Wartungsmodus
            continue
        own = site["images"].get(slot)
        s = std.get(slot) or {}
        src = (own or {}).get("src") or {}
        preview = src.get(640) or src.get(min(src)) if src else (f"/web/assets/img/{slot}-640.webp" if s else "")
        images.append({"slot": slot, "label": label, "where": where, "own": bool(own), "preview": preview,
                       "alt": (imgs.get(slot) or {}).get("alt") or s.get("alt", ""), "standard_alt": s.get("alt", ""),
                       "w": (own or s).get("w", 0), "h": (own or s).get("h", 0)})
    checks = []
    def add(level, text, link=""):
        checks.append({"level": level, "text": text, "link": link})
    for k, label in website.REQUIRED_TEXTS.items():
        if not (texts.get(k) or "").strip():
            add("fehlt", f"Text fehlt: {label} (erscheint als gelber Platzhalter)", "#/website/texte")
    for k, label in (("site_impressum", "Impressum"), ("site_datenschutz", "Datenschutzerklärung")):
        open_marks = re.findall(r"\[[^\]\n]{1,200}\]", texts.get(k) or "")
        if open_marks:
            add("fehlt", f"{label}: {len(open_marks)} Angaben in [eckigen Klammern] noch ausfüllen (z. B. {open_marks[0]})", "#/website/texte")
    if not horses:
        add("hinweis", "Noch keine Schulpferde für die Webseite freigegeben (Stall > Pferde, Haken „auf der Webseite“).", "#/horses")
    for h in horses:
        if not h["photo"]:
            add("hinweis", f"Schulpferd {h['name']} ohne Foto.", "#/horses")
    if not fees:
        add("hinweis", "Keine Beitragsarten für die Webseite freigegeben: Die Seite Mitgliedschaft zeigt einen Platzhalter.", "#/fees")
    if not events:
        add("hinweis", "Keine kommenden öffentlichen Termine: Die Seite Termine zeigt „Termine folgen“.", "#/events")
    if noindex:
        add("hinweis", "Suchmaschinen sind ausgeschlossen (noindex). Abschalten unter Webseite > Suchmaschinen, sobald die endgültige Domain läuft.", "#/website/texte")
    notice_until = texts.get("site_notice_until") or ""
    if texts.get("site_notice_text") and notice_until and notice_until < today:
        add("hinweis", "Die Hinweis-Leiste ist abgelaufen und wird nicht mehr angezeigt.", "#/website/hinweis")
    with db() as conn:
        sm = web_sitemap(conn)
        nav = web_nav(conn)
        links, links_default = web_links(conn), not get_setting(conn, "web_links")
    return {"texts": texts, "images": images, "checks": checks, "site_url": SITE_URL, "nav": nav, "links": links, "links_default": links_default, "links_max": LINKS_MAX,
            "layout": [{"key": k, "label": website.START_SECTIONS[k], "visible": v} for k, v in site["layout"]],
            "sitemap": [{"key": k, "label": label, "on": sm[k][0], "prio": sm[k][1], "default": d} for k, label, d in website.SITEMAP_PAGES]}






NOTIFY_KINDS = {
    "contact_request": ("Neue Anfrage über das Kontaktformular der Webseite", ("mitglieder", "verein", "unterricht", "stall")),
    "horse_health": ("Pferde: Impfung, Hufschmied, Wurmkur oder Tierarzt steht an", ("stall",)),
    "event_deadline": ("Veranstaltungen: Nennungsschluss und Termin naht (7 und 1 Tag vorher)", ("veranstaltungen",)),
    "contract_end": ("Stall: Einstellervertrag endet in 30 Tagen", ("stall",)),
}
# Gelten automatisch, solange jemand unter „Mein Profil“ noch nie Benachrichtigungen gespeichert hat
NOTIFY_DEFAULT = ()


def notify_kinds_of(u):
    """Abonnierte Arten eines Kontos; nie gespeichert = Standard."""
    if u["notify"] is None:
        return list(NOTIFY_DEFAULT)
    try:
        return json.loads(u["notify"] or "[]")
    except ValueError:
        return []


def notify_users(user_ids, kind, subject, body, once_prefix=None):
    """Mail an bestimmte Konten (z. B. Eingeladene eines Termins), nur wer die Art abonniert hat und die Rechte dafür hat.
    once_prefix: je Konto nur einmal (Schlüssel once_prefix:konto)."""
    cfg = smtp_config()
    if not cfg["smtp_host"] or not user_ids:
        return
    to = []
    with db() as conn:
        for uid in set(user_ids):
            u = conn.execute("SELECT id, is_admin, email, notify, member_id, active FROM users WHERE id=?", (uid,)).fetchone()
            if not u or not u["active"] or kind not in notify_kinds_of(u) or not notify_allowed(effective_perms(conn, row_dict(u)), kind):
                continue
            addr = account_email(conn, u)
            if not addr:
                continue
            if once_prefix:
                key = f"{once_prefix}:{uid}"
                if conn.execute("SELECT 1 FROM notify_sent WHERE key=?", (key,)).fetchone():
                    continue
                conn.execute("INSERT INTO notify_sent (key, time) VALUES (?, ?)", (key, datetime.now().isoformat(timespec="seconds")))
            to.append(addr)
    for addr in to:
        send_mail(cfg, addr, f"{APP_NAME}: {subject}", body)


def notify_users_async(*args, **kw):
    threading.Thread(target=lambda: notify_users(*args, **kw), daemon=True).start()
SMTP_KEYS = ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_from", "hub_url", "imap_host")
NOTIFY_HOUR = 8   # Erinnerungen frühestens ab 8 Uhr
NOTIFY_INTERVAL = int(ENV("TMH_NOTIFY_INTERVAL") or 300)   # Sekunden zwischen zwei Durchläufen (Variable nur für Tests)
DIGEST_SECONDS = int(ENV("TMH_DIGEST_SECONDS") or 3600)    # neue Bestellungen höchstens so oft gesammelt melden


def notify_allowed(perms, kind):
    need = NOTIFY_KINDS[kind][2] if len(NOTIFY_KINDS[kind]) > 2 else 1
    return any(perms.get(a, 0) >= need for a in NOTIFY_KINDS[kind][1])


# ---------------------------------------------------------------------------
# Postfach (IMAP): Mails des Vereinspostfachs im Hub lesen und beantworten (Bereich „postfach“).
# Zugang wie beim Versand (smtp_user/smtp_password), Server aus imap_host oder smtp.* -> imap.*.
# Nichts wird im Hub gespeichert, jede Anfrage holt live vom Mailserver. Inhalte nur als Text (HTML wird zu Text), Anhänge nur als Download.
# ---------------------------------------------------------------------------
PF_PAGE = 40
PF_DONE_FOLDER = "Erledigt"   # „Erledigt“ verschiebt Mails hierher (wird bei Bedarf angelegt)
PF_FOLDER_LABELS = {"inbox": "Posteingang", "done": "Erledigt", "sent": "Gesendet", "drafts": "Entwürfe", "trash": "Papierkorb", "junk": "Spam", "archive": "Archiv"}
PF_ROLE_NAMES = {"sent": ("sent", "gesendet", "gesendete objekte", "sent items", "sent messages"), "trash": ("trash", "papierkorb", "deleted items", "gelöschte objekte"),
                 "drafts": ("drafts", "entwürfe"), "junk": ("junk", "spam", "unerwünscht"), "archive": ("archive", "archiv"), "done": ("erledigt",)}


class PostfachError(Exception):
    pass


def imap_utf7_decode(name):
    out = []
    for part in re.split(r"(&[^-]*-)", name):
        if part.startswith("&") and part.endswith("-"):
            if part == "&-":
                out.append("&")
            else:
                b = part[1:-1].replace(",", "/")
                try:
                    out.append(base64.b64decode(b + "=" * (-len(b) % 4)).decode("utf-16-be"))
                except (ValueError, UnicodeDecodeError):
                    out.append(part)
        else:
            out.append(part)
    return "".join(out)


def imap_quote(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def postfach_config():
    cfg = smtp_config()
    host = (cfg.get("imap_host") or "").strip() or re.sub(r"^smtp\.", "imap.", (cfg.get("smtp_host") or "").strip())
    cfg["imap"] = host if re.fullmatch(r"[A-Za-z0-9.-]{3,200}", host or "") else ""
    return cfg


def postfach_open(cfg=None):
    import imaplib
    import ssl
    cfg = cfg or postfach_config()
    if not (cfg["imap"] and cfg["smtp_user"] and cfg["smtp_password"]):
        raise PostfachError("Das Postfach ist noch nicht eingerichtet (Verwaltung > Einstellungen > E-Mail-Versand: Benutzer, Passwort und Server).")
    try:
        m = imaplib.IMAP4_SSL(cfg["imap"], 993, ssl_context=ssl.create_default_context(), timeout=20)
    except OSError as e:
        raise PostfachError(f"Der Mailserver {cfg['imap']} ist nicht erreichbar ({type(e).__name__}).")
    try:
        m.login(cfg["smtp_user"], cfg["smtp_password"])
    except imaplib.IMAP4.error:
        raise PostfachError("Anmeldung am Postfach fehlgeschlagen. Benutzername oder Passwort stimmen nicht.")
    return m, cfg


def postfach_folders(m):
    typ, data = m.list()
    out = []
    for line in data or []:
        if not isinstance(line, bytes):
            continue
        mm = re.match(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
        if not mm:
            continue
        raw = mm.group("name").decode("utf-8", "replace").strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        flags = mm.group("flags").decode().lower()
        if "\\noselect" in flags:
            continue
        label = imap_utf7_decode(raw)
        short = label.split("/")[-1].split(".")[-1].strip().lower()
        role = "inbox" if raw.upper() == "INBOX" else next((r for r in ("sent", "trash", "drafts", "junk", "archive") if f"\\{r}" in flags), "")
        if not role:
            role = next((r for r, names in PF_ROLE_NAMES.items() if short in names), "")
        out.append({"name": raw, "label": PF_FOLDER_LABELS.get(role, label), "role": role})
    order = ["inbox", "done", "sent", "drafts", "archive", "junk", "trash", ""]
    out.sort(key=lambda f: (order.index(f["role"]), f["label"].lower()))
    return out


def pf_done_folder(m, create=True):
    """Name des Ordners „Erledigt“ im Postfach; legt ihn an, wenn er fehlt (create=True)."""
    names = [f["name"] for f in postfach_folders(m)]
    target = next((n for n in names if n.split(".")[-1].split("/")[-1].strip().lower() == PF_DONE_FOLDER.lower()), "")
    if not target and create:
        names = [PF_DONE_FOLDER]
        with contextlib.suppress(Exception):   # manche Anbieter erlauben Ordner nur unterhalb von „INBOX.“ (persönlicher Namensraum)
            typ, ns = m.namespace()
            mm = re.match(rb'\(\("([^"]*)" "([^"]*)"\)', (ns or [b""])[0] or b"")
            if mm and mm.group(1):
                names.append(mm.group(1).decode() + PF_DONE_FOLDER)
        for name in names:
            with contextlib.suppress(Exception):
                typ, _d = m.create(imap_quote(name))
                if typ == "OK":
                    target = name
                    with contextlib.suppress(Exception):
                        m.subscribe(imap_quote(name))
                    break
    return target


def pf_move_to_done(folder, uid, cfg=None):
    """Mail in den Ordner „Erledigt“ verschieben (anlegen, falls nötig). Zuweisungen und Erledigt-Vermerk ziehen mit.
    cfg = eigenes Postfach eines Kontos (dann ohne Zuweisungen). Gibt (Ordner, neue UID) zurück oder None."""
    m, _ = postfach_open(cfg)
    try:
        target = pf_done_folder(m)
        if not target:
            raise PostfachError("Ordner „Erledigt“ konnte nicht angelegt werden.")
        if folder == target:
            return None
        pf_select(m, folder, readonly=False)
        typ, data = m.uid("FETCH", str(uid), "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
        hdr = next((x[1] for x in data or [] if isinstance(x, tuple)), b"")
        if not hdr:
            return None
        mid = re.search(rb"Message-ID:\s*(<[^>]+>)", hdr, re.I)
        m.untagged_responses.pop("COPYUID", None)
        if "MOVE" in m.capabilities:
            typ, data = m.uid("MOVE", str(uid), imap_quote(target))
        else:
            typ, data = m.uid("COPY", str(uid), imap_quote(target))
            if typ == "OK":
                m.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
                m.expunge()
        if typ != "OK":
            raise PostfachError("Verschieben nach „Erledigt“ fehlgeschlagen.")
        new_uid = None
        texts = [x for x in (m.untagged_responses.pop("COPYUID", None) or []) + list(data or []) if isinstance(x, (bytes, str))]
        for t in texts:
            mm = re.search(r"(?:COPYUID )?\d+ (\d+) (\d+)", t.decode() if isinstance(t, bytes) else t)
            if mm and mm.group(1) == str(uid):
                new_uid = int(mm.group(2))
        if new_uid is None and mid:   # ohne Rückmeldung: über die Message-ID im Zielordner suchen
            pf_select(m, target)
            typ, d2 = m.uid("SEARCH", None, "HEADER", "Message-ID", mid.group(1).decode())
            found = (d2[0] or b"").split()
            new_uid = int(found[-1]) if found else None
    finally:
        with contextlib.suppress(Exception):
            m.logout()
    if new_uid and cfg is None:
        with db() as conn:
            conn.execute("UPDATE pf_inbox SET folder=?, uid=? WHERE folder=? AND uid=?", (target, new_uid, folder, int(uid)))
            conn.execute("UPDATE mail_tasks SET folder=?, uid=? WHERE folder=? AND uid=?", (target, new_uid, folder, int(uid)))
    return target, new_uid


def pf_forward_to_person(cfg, msg, to, name, by, note=""):
    """Mail aus dem Vereinspostfach an eine Person ohne Zugriff weiterleiten (Text, Anhänge, Antworten gehen an den Absender)."""
    frm, subj = pf_head(msg.get("From")), pf_head(msg.get("Subject")) or "(ohne Betreff)"
    atts, total = [], 0
    for i, part in enumerate(msg.iter_attachments()):
        data = part.get_payload(decode=True) or b""
        total += len(data)
        if total > 20 * 1024 * 1024:
            break
        ctype = part.get_content_type() if "/" in part.get_content_type() else "application/octet-stream"
        atts.append((pf_head(part.get_filename() or "") or f"anhang-{i + 1}", data, *ctype.split("/", 1)))
    text = (f"Hallo {name},\n\n{by.get('display_name') or by.get('username')} hat dir diese Mail aus dem Vereinspostfach {cfg.get('smtp_user', '')} "
            f"zum Bearbeiten weitergeleitet.\n" + (f"Hinweis: {note}\n" if note else "") +
            "Mit „Antworten“ geht deine Antwort direkt an den Absender.\n\n"
            f"---------- Weitergeleitete Nachricht ----------\nVon: {frm}\nDatum: {msg.get('Date') or ''}\nBetreff: {subj}\nAn: {pf_head(msg.get('To'))}\n"
            + (f"Cc: {pf_head(msg.get('Cc'))}\n" if msg.get("Cc") else "") + "\n" + pf_text(msg))
    reply_to = pf_head(msg.get("Reply-To")) or frm
    return send_mail(cfg, to, subj if re.match(r"(wg|fwd)\s*:", subj, re.I) else f"WG: {subj}", text, footer=False,
                     attachments=atts, headers={"Reply-To": reply_to.replace("\n", " ")[:300]})


def pf_role_folder(m, role):
    return next((f["name"] for f in postfach_folders(m) if f["role"] == role), "")


def pf_select(m, folder, readonly=True):
    typ, data = m.select(imap_quote(folder or "INBOX"), readonly=readonly)
    if typ != "OK":
        raise PostfachError("Diesen Ordner gibt es nicht.")


def pf_name(f):
    from email.utils import parseaddr
    n, a = parseaddr(f or "")
    return n or a or (f or "")


def pf_head(text):
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(text or "")))
    except Exception:  # noqa
        return text or ""


class _HtmlText(HTMLParser):
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip, self.href = [], 0, ""

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")
        if tag == "a":
            self.href = dict(attrs).get("href") or ""

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "a" and self.href.startswith(("http://", "https://")):
            self.out.append(f" [{self.href}]")
            self.href = ""

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_to_text(html):
    p = _HtmlText()
    try:
        p.feed(html or "")
        p.close()
    except Exception:  # noqa
        return re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def pf_message(m, folder, uid, mark_seen=True):
    from email import policy
    from email.parser import BytesParser
    pf_select(m, folder, readonly=not mark_seen)
    typ, data = m.uid("FETCH", uid, "(FLAGS BODY.PEEK[])")
    raw = next((x[1] for x in data or [] if isinstance(x, tuple)), None)
    if not raw:
        raise PostfachError("Diese Mail gibt es nicht mehr.")
    flags = next((x[0] for x in data if isinstance(x, tuple)), b"")
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    if mark_seen and b"\\Seen" not in flags:
        m.uid("STORE", uid, "+FLAGS", "(\\Seen)")
    return msg, flags


def pf_text(msg):
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        content = part.get_payload(decode=True).decode("utf-8", "replace")
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return str(content)[:200000]


def pf_attachments(msg):
    out = []
    for i, part in enumerate(msg.iter_attachments()):
        name = pf_head(part.get_filename() or "") or f"anhang-{i + 1}"
        payload = part.get_payload(decode=True) or b""
        out.append({"i": i, "name": name[:150], "type": part.get_content_type(), "size": len(payload)})
    return out


def pf_unseen(cfg=None):
    m, _ = postfach_open(cfg)
    try:
        typ, data = m.status("INBOX", "(UNSEEN)")
        mm = re.search(rb"UNSEEN (\d+)", data[0] if data else b"")
        return int(mm.group(1)) if mm else 0
    finally:
        with contextlib.suppress(Exception):
            m.logout()


def pf_append_sent(raw):
    """Kopie einer aus dem Hub verschickten Mail im Ordner „Gesendet“ ablegen (wie bei einem Mailprogramm)."""
    import imaplib
    m, _ = postfach_open()
    try:
        sent = pf_role_folder(m, "sent")
        if sent:
            m.append(imap_quote(sent), "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw)
    finally:
        with contextlib.suppress(Exception):
            m.logout()


# ---------------------------------------------------------------------------
# Web Push (RFC 8030/8291/8292) nur mit der Standardbibliothek: P-256 (ECDH, ECDSA für VAPID), HKDF, AES-128-GCM.
# Geprüft gegen die Testwerte aus RFC 8291 Anhang A und NIST-GCM. Nachrichten sind Ende-zu-Ende verschlüsselt,
# der Push-Dienst (Google, Apple, Mozilla) sieht nur Absender-Schlüssel, Größe und Zeitpunkt.
# ---------------------------------------------------------------------------
_EC_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_EC_A = _EC_P - 3
_EC_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_EC_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_EC_G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
         0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)


def _ec_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % _EC_P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + _EC_A) * pow(2 * y1, -1, _EC_P) % _EC_P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, _EC_P) % _EC_P
    x3 = (lam * lam - x1 - x2) % _EC_P
    return x3, (lam * (x1 - x3) - y1) % _EC_P


def _ec_mul(k, pt):
    r0, r1 = None, pt   # Montgomery-Leiter: gleiche Schrittfolge für jedes Bit
    for bit in bin(k)[2:]:
        if bit == "1":
            r0, r1 = _ec_add(r0, r1), _ec_add(r1, r1)
        else:
            r0, r1 = _ec_add(r0, r0), _ec_add(r0, r1)
    return r0


def _ec_on_curve(pt):
    x, y = pt
    return 0 <= x < _EC_P and 0 <= y < _EC_P and (y * y - (x * x * x + _EC_A * x + _EC_B)) % _EC_P == 0


def ec_pub_bytes(pt):
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def ec_pub_point(raw):
    if len(raw) != 65 or raw[0] != 4:
        raise ValueError("Ungültiger öffentlicher Schlüssel")
    pt = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big"))
    if not _ec_on_curve(pt):
        raise ValueError("Schlüssel liegt nicht auf der Kurve")
    return pt


def ec_keypair(priv=None):
    d = priv if priv is not None else secrets.randbelow(_EC_N - 1) + 1
    return d, _ec_mul(d, _EC_G)


def ec_sign(d, data):
    """ECDSA P-256 mit SHA-256, Ergebnis r||s (64 Bytes) wie für JWT ES256 nötig."""
    z = int.from_bytes(hashlib.sha256(data).digest(), "big")
    while True:
        k = secrets.randbelow(_EC_N - 1) + 1
        r = _ec_mul(k, _EC_G)[0] % _EC_N
        if not r:
            continue
        s = pow(k, -1, _EC_N) * (z + r * d) % _EC_N
        if s:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def ec_verify(pub, data, sig):
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if not (0 < r < _EC_N and 0 < s < _EC_N):
        return False
    z = int.from_bytes(hashlib.sha256(data).digest(), "big")
    w = pow(s, -1, _EC_N)
    pt = _ec_add(_ec_mul(z * w % _EC_N, _EC_G), _ec_mul(r * w % _EC_N, pub))
    return pt is not None and pt[0] % _EC_N == r


# AES-128 (nur Verschlüsseln) und GCM
_AES_SBOX = bytes.fromhex(
    "637c777bf26b6fc53001672bfed7ab76ca82c97dfa5947f0add4a2af9ca472c0b7fd9326363ff7cc34a5e5f171d8311504c723c31896059a071280e2eb27b275"
    "09832c1a1b6e5aa0523bd6b329e32f8453d100ed20fcb15b6acbbe394a4c58cfd0efaafb434d338545f9027f503c9fa851a3408f929d38f5bcb6da2110fff3d2"
    "cd0c13ec5f974417c4a77e3d645d197360814fdc222a908846eeb814de5e0bdbe0323a0a4906245cc2d3ac629195e479e7c8376d8dd54ea96c56f4ea657aae08"
    "ba78252e1ca6b4c6e8dd741f4bbd8b8a703eb5664803f60e613557b986c11d9ee1f8981169d98e949b1e87e9ce5528df8ca1890dbfe6426841992d0fb054bb16")
_AES_RCON = (1, 2, 4, 8, 16, 32, 64, 128, 27, 54)


def _aes_expand(key):
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [_AES_SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= _AES_RCON[i // 4 - 1]
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _aes_block(rk, block):
    s = [b ^ k for b, k in zip(block, rk[0])]
    for rnd in range(1, 11):
        s = [_AES_SBOX[b] for b in s]
        s = [s[(i + 4 * (i % 4)) % 16] for i in range(16)]   # ShiftRows (spaltenweise Ablage)
        if rnd != 10:
            t = []
            for c in range(4):
                a = s[4 * c:4 * c + 4]
                x = a[0] ^ a[1] ^ a[2] ^ a[3]
                t += [a[i] ^ x ^ _xt(a[i] ^ a[(i + 1) % 4]) for i in range(4)]
            s = t
        s = [b ^ k for b, k in zip(s, rk[rnd])]
    return bytes(s)


def _gf_mul(x, y):
    r, R = 0, 0xE1 << 120
    for i in range(127, -1, -1):
        if (y >> i) & 1:
            r ^= x
        x = (x >> 1) ^ R if x & 1 else x >> 1
    return r


def aes128_gcm_encrypt(key, iv, plaintext, aad=b""):
    rk = _aes_expand(key)
    h = int.from_bytes(_aes_block(rk, bytes(16)), "big")
    j0 = iv + b"\x00\x00\x00\x01"
    out = bytearray()
    ctr = int.from_bytes(j0, "big")
    for i in range(0, len(plaintext), 16):
        ctr = (ctr & ~0xFFFFFFFF) | ((ctr + 1) & 0xFFFFFFFF)
        ks = _aes_block(rk, ctr.to_bytes(16, "big"))
        out += bytes(a ^ b for a, b in zip(plaintext[i:i + 16], ks))
    def blocks(b):
        b = b + bytes(-len(b) % 16)
        return [int.from_bytes(b[i:i + 16], "big") for i in range(0, len(b), 16)]
    g = 0
    for blk in blocks(aad) + blocks(bytes(out)) + [((len(aad) * 8) << 64) | (len(out) * 8)]:
        g = _gf_mul(g ^ blk, h)
    tag = bytes(a ^ b for a, b in zip(g.to_bytes(16, "big"), _aes_block(rk, j0)))
    return bytes(out) + tag


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64u_dec(s):
    s = str(s or "")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def webpush_encrypt(ua_public, auth_secret, plaintext, as_priv=None, salt=None):
    """RFC 8291 (aes128gcm): ein Datensatz, Ergebnis ist der fertige Nachrichtenkörper."""
    ua_pt = ec_pub_point(ua_public)
    d, as_pt = ec_keypair(as_priv)
    as_public = ec_pub_bytes(as_pt)
    shared = _ec_mul(d, ua_pt)[0].to_bytes(32, "big")
    salt = salt or secrets.token_bytes(16)
    hm = lambda k, m: hmac.new(k, m, hashlib.sha256).digest()
    ikm = hm(hm(auth_secret, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = hm(salt, ikm)
    cek = hm(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = hm(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    header = salt + (4096).to_bytes(4, "big") + bytes([len(as_public)]) + as_public
    return header + aes128_gcm_encrypt(cek, nonce, plaintext + b"\x02")


PUSH_HOSTS = (".googleapis.com", ".push.apple.com", ".push.services.mozilla.com", ".notify.windows.com", ".windows.com")


def push_endpoint_ok(url):
    """Nur https-Adressen der bekannten Push-Dienste (sonst könnte jemand den Server an beliebige Adressen schicken lassen)."""
    u = urlparse(str(url or ""))
    host = (u.hostname or "").lower()
    return u.scheme == "https" and len(url) < 1000 and any(host.endswith(h) or host == h.lstrip(".") for h in PUSH_HOSTS)


def vapid_keys(conn):
    raw = secret_get(conn, "vapid_d")
    if not raw:
        d, _ = ec_keypair()
        raw = format(d, "064x")
        secret_set(conn, "vapid_d", raw)
    d = int(raw, 16)
    return d, ec_pub_bytes(_ec_mul(d, _EC_G))


def push_send_one(sub_row, payload, d, pub, contact):
    u = urlparse(sub_row["endpoint"])
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600, "sub": contact}
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." + b64u(json.dumps(claims, separators=(",", ":")).encode())
    jwt = head + "." + b64u(ec_sign(d, head.encode()))
    body = webpush_encrypt(b64u_dec(sub_row["p256dh"]), b64u_dec(sub_row["auth"]), json.dumps(payload, ensure_ascii=False).encode())
    req = urllib.request.Request(sub_row["endpoint"], data=body, method="POST", headers={
        "Authorization": f"vapid t={jwt}, k={b64u(pub)}", "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "TTL": "86400", "Urgency": "high"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa
        return 0


def send_push(user_ids, title, body, url="/hub", tag="hub", kind="show"):
    """Push an alle Geräte der Personen. Abgelaufene Abos (404/410) werden entfernt. Gibt die Zahl erfolgreicher Zustellungen zurück."""
    ids = [int(i) for i in user_ids if i]
    if not ids:
        return 0
    with db() as conn:
        subs = conn.execute(f"SELECT * FROM push_subs WHERE user_id IN ({','.join('?' for _ in ids)})", ids).fetchall()
        if not subs:
            return 0
        d, pub = vapid_keys(conn)
        user = secret_get(conn, "smtp_user") or ""
    contact = f"mailto:{user}" if "@" in user else (SITE_URL if SITE_URL.startswith("https://") else "mailto:hub@example.org")
    payload = {"title": title[:120], "body": body[:300], "url": url if url.startswith("/") else "/hub", "tag": tag[:60], "type": kind}
    with db() as conn:
        badges = {i: badge_for(conn, i) for i in ids}
    ok = 0
    for row in subs:
        st = push_send_one(row, {**payload, "badge": badges.get(row["user_id"], 0)}, d, pub, contact)
        with db() as conn:
            if st in (404, 410):
                conn.execute("DELETE FROM push_subs WHERE id=?", (row["id"],))
            elif 200 <= st < 300:
                ok += 1
                conn.execute("UPDATE push_subs SET last_ok=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), row["id"]))
    return ok


def send_push_async(*a, **kw):
    threading.Thread(target=lambda: send_push(*a, **kw), daemon=True).start()


# ---------------------------------------------------------------------------
# Mail-Aufgaben: Mails aus dem Postfach Personen zuweisen (Push, Mail, Hinweis im Hub). Die Person darf genau diese Mail lesen und beantworten.
# Signatur: eine Vorlage für alle (Einstellung mail_signature), Name aus dem Konto, Rolle aus users.mail_role.
# ---------------------------------------------------------------------------
# Signatur nach dem Signatur-Paket des Vereins (web/assets/signatur: signatur.html, signatur.txt, Bilder, README.md).
# Platzhalter {{NAME}} (aus dem Konto) und {{ROLLE}} (Gruppen ohne „Wartung“ oder „Rolle in der Signatur“ unter Konten).
# Bilder liegen öffentlich unter <DOMAIN>/signatur/... (Route /signatur/<datei>.png), keine eingebetteten Bilder.
SIG_DIR = website.WEB / "assets" / "signatur"
SIG_URL = SITE_URL + "/signatur/"   # Bilder der Signatur und des Newsletters, öffentlich unter /signatur/<datei>.png
SIG_SKIP_GROUPS = {"wartung"}   # technische Gruppen nicht als Rolle zeigen


def signature_role(conn, uid):
    u = conn.execute("SELECT mail_role FROM users WHERE id=?", (uid,)).fetchone()
    if u and (u["mail_role"] or "").strip():
        return u["mail_role"].strip()
    groups = [r["name"] for r in conn.execute("SELECT g.name FROM user_groups ug JOIN groups g ON g.id = ug.group_id WHERE ug.user_id=? ORDER BY g.sort, g.name", (uid,))
              if (r["name"] or "").strip().lower() not in SIG_SKIP_GROUPS]
    return " | ".join(groups)


def signature_parts(conn, uid, email="", preview=False):
    """(Text, HTML) der Signatur. preview=True: Bilder relativ (/signatur/...), damit die Vorschau auch ohne Domain lädt."""
    try:
        tpl_html = (SIG_DIR / "signatur.html").read_text(encoding="utf-8")
        tpl_text = (SIG_DIR / "signatur.txt").read_text(encoding="utf-8")
    except OSError:
        return "", ""
    u = conn.execute("SELECT display_name, username, sig_phone FROM users WHERE id=?", (uid,)).fetchone()
    name = ((u["display_name"] or u["username"]) if u else "").strip()
    role = signature_role(conn, uid)
    phone = ((u["sig_phone"] or "") if u else "").strip()
    tel = re.sub(r"[^\d+]", "", phone)
    tel = "+49" + tel[1:] if tel.startswith("0") and not tel.startswith("00") else tel   # Link zum Anrufen im internationalen Format
    club = get_setting(conn, "club_name") or "RSC Frya Fresena e. V."
    mail = (get_setting(conn, "site_contact_email") or "").strip()
    web = SITE_URL

    def sig_row(href, icon, text):   # eine Zeile mit Symbol, entfällt ohne Text
        return (f'<tr><td valign="middle" style="padding:0 8px 6px 0;"><a href="{html_esc(href)}" style="text-decoration:none;"><img src="{SIG_URL}{icon}" width="18" height="18" alt="" style="display:block;border:0;"></a></td>'
                f'<td valign="middle" style="padding:0 0 6px 0;font-size:13px;line-height:18px;"><a href="{html_esc(href)}" style="color:#333333;text-decoration:none;">{html_esc(text)}</a></td></tr>') if text else ""
    html = (re.sub(r"<!--.*?-->\s*", "", tpl_html, flags=re.S).replace("{{NAME}}", html_esc(name)).replace("{{ROLLE}}", html_esc(role))
            .replace("{{TELEFON_ZEILE}}", sig_row(f"tel:{tel}", "icon-phone.png", phone)).replace("{{EMAIL_ZEILE}}", sig_row(f"mailto:{mail}", "icon-mail.png", mail))
            .replace("{{WEB_ZEILE}}", sig_row(web, "icon-web.png", web.split("//", 1)[-1])).replace("{{CLUB}}", html_esc(club)).replace("{{SIG}}", SIG_URL).strip())
    if preview:
        html = html.replace(SIG_URL, "/signatur/")
    lines = []
    for line in tpl_text.replace("\r\n", "\n").split("\n"):
        if "{{ROLLE}}" in line and not role and not line.replace("{{ROLLE}}", "").strip():
            continue   # Zeile nur für die Rolle: weglassen, wenn keine Rolle da ist
        if ("{{TELEFON}}" in line and not phone) or ("{{EMAIL}}" in line and not mail) or ("{{WEB}}" in line and not web):
            continue   # ohne Angabe keine Zeile
        lines.append(line.replace("{{NAME}}", name).replace("{{ROLLE}}", role).replace("{{TELEFON}}", phone).replace("{{EMAIL}}", mail)
                     .replace("{{WEB}}", web).replace("{{CLUB}}", club))
    return "\n".join(lines).strip(), html


def mail_signature_for(conn, uid, email=""):
    """Textfassung der Signatur (Vorschau und Mailprogramme ohne HTML)."""
    return signature_parts(conn, uid, email or "")[0]


def user_mail_cfg(conn, uid, base=None, row=None):
    """Mail-Konto einer Person (oder Zeile `row` aus mail_accounts) im Format von smtp_config(), sonst None (dann gilt das Vereinspostfach)."""
    r = row or conn.execute("SELECT m.* FROM mail_accounts m JOIN mail_links a ON a.account_id=m.id WHERE a.user_id=? ORDER BY lower(m.address), m.id", (uid,)).fetchone()
    if not r or not (r["smtp_host"] and r["username"] and r["password"]):
        return None
    u = conn.execute("SELECT display_name, username FROM users WHERE id=?", (uid,)).fetchone() if uid else None
    base = base or smtp_config()
    name = (u["display_name"] or u["username"]) if u else ""
    club = get_setting(conn, "club_name") or "RSC Frya Fresena"
    if name and club.lower() not in name.lower():   # Absender: „Vorname Name | RSC Frya Fresena“
        name = f"{name} | {club}"
    cfg = {"smtp_host": r["smtp_host"], "smtp_port": r["smtp_port"] or "", "smtp_security": r["smtp_security"] or "starttls",
           "smtp_user": r["username"], "smtp_password": r["password"], "hub_url": base.get("hub_url", ""), "public_url": base.get("public_url", ""),
           "smtp_from": f"{name} <{r['address'] or r['username']}>" if name else (r["address"] or r["username"]), "imap_host": r["imap_host"] or ""}
    host = (cfg["imap_host"] or "").strip() or re.sub(r"^smtp\.", "imap.", cfg["smtp_host"])
    cfg["imap"] = host if re.fullmatch(r"[A-Za-z0-9.-]{3,200}", host or "") else ""
    return cfg


def user_mail_boxes(conn, uid, base=None):
    """Alle vollständig hinterlegten Mail-Konten einer Person als [(Schlüssel 'acc<id>', Zugang)]."""
    rows = conn.execute("SELECT m.* FROM mail_accounts m JOIN mail_links a ON a.account_id=m.id WHERE a.user_id=? ORDER BY lower(m.address), m.id", (uid,)).fetchall()
    return [(f"acc{r['id']}", c) for r in rows if (c := user_mail_cfg(conn, uid, base, row=r))]


def mail_addr(c):
    return parseaddr(mail_sender(c)[0])[1] or c["smtp_user"]


def pf_senders(conn, user, can_info, box="info"):
    """Mögliche Absender im Hub: Vereinspostfach (mit Recht) und die eigenen Mail-Konten. Liefert (Liste, Vorgabe, Zugänge)."""
    info = postfach_config()
    mine = user_mail_boxes(conn, user["id"], info)
    out, cfgs = [], {"info": info}
    if can_info:
        out.append({"key": "info", "address": mail_addr(info)})
    for k, c in mine:
        out.append({"key": k, "address": mail_addr(c)})
        cfgs[k] = c
    if not out:
        out.append({"key": "info", "address": mail_addr(info)})
    keys = [x["key"] for x in out]
    default = box if box in keys else ("info" if can_info or not mine else mine[0][0])
    return out, default, cfgs


def own_mail_address(conn, uid):
    """Adresse des eigenen Postfachs eines Kontos (leer, wenn keins vollständig hinterlegt ist)."""
    c = user_mail_cfg(conn, uid)
    return (parseaddr(mail_sender(c)[0])[1] or c["smtp_user"]) if c else ""


def mail_account_check(cfg):
    """Prüft Anmeldung an SMTP und IMAP eines Zugangs, ohne etwas zu verschicken."""
    import smtplib
    import ssl
    res = {}
    try:
        port = int(cfg["smtp_port"] or (465 if cfg["smtp_security"] == "ssl" else 587))
        sec = {465: "ssl", 587: "starttls"}.get(port, cfg["smtp_security"])
        ctx = ssl.create_default_context()
        s_ = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=20, context=ctx) if sec == "ssl" else smtplib.SMTP(cfg["smtp_host"], port, timeout=20)
        with s_:
            if sec == "starttls":
                s_.starttls(context=ctx)
            s_.login(cfg["smtp_user"], cfg["smtp_password"])
        res["smtp"] = "ok"
    except Exception as e:  # noqa
        res["smtp"] = f"{type(e).__name__}: {e}"[:200]
    try:
        m, _ = postfach_open(cfg)
        with contextlib.suppress(Exception):
            m.logout()
        res["imap"] = "ok"
    except Exception as e:  # noqa
        res["imap"] = str(e)[:200]
    res["ok"] = res["smtp"] == "ok"
    return res


# ---------------------------------------------------------------------------
# Neue Mails im Posteingang: Abruf im Hintergrund (pf_poll_loop), Push an die Gruppen aus pf_notify_groups.
# Eine Mail gilt als erledigt, sobald jemand im Hub antwortet, weiterleitet, zuweist, löscht oder „Erledigt“ klickt
# (auch Antworten aus einem anderen Mailprogramm: Kennzeichen \Answered). Dann geht an alle anderen eine Erledigt-Meldung,
# die die alte Meldung ersetzt und sich selbst schließt. Die Zahl am App-Symbol = offene eigene Aufgaben + neue Mails.
# ---------------------------------------------------------------------------
PF_POLL_SECONDS = max(30, int(ENV("TMH_PF_POLL", "120")))


def pf_notify_groups(conn):
    try:
        v = json.loads(get_setting(conn, "pf_notify_groups") or "[]")
        return [str(x) for x in v] if isinstance(v, list) else []
    except ValueError:
        return []


def pf_recipients(conn):
    names = [n.lower() for n in pf_notify_groups(conn)]
    if not names:
        return []
    rows = conn.execute(f"SELECT DISTINCT u.id FROM users u JOIN user_groups ug ON ug.user_id = u.id JOIN groups g ON g.id = ug.group_id "
                        f"WHERE u.active=1 AND lower(g.name) IN ({','.join('?' for _ in names)})", names).fetchall()
    return [r["id"] for r in rows]


def badge_for(conn, uid):
    n = conn.execute("SELECT COUNT(*) FROM mail_tasks WHERE user_id=? AND status='offen'", (uid,)).fetchone()[0]
    if uid in pf_recipients(conn):
        n += conn.execute("SELECT COUNT(*) FROM pf_inbox WHERE status='neu'").fetchone()[0]
    return n


def pf_mark_handled(folder, uid, user, action):
    """Mail als erledigt markieren und den anderen Empfängern die Erledigt-Meldung schicken."""
    with db() as conn:
        row = conn.execute("SELECT * FROM pf_inbox WHERE folder=? AND uid=? AND status='neu'", (folder, int(uid))).fetchone()
        if not row:
            return False
        conn.execute("UPDATE pf_inbox SET status='erledigt', handled_by=?, handled_at=?, action=? WHERE id=?",
                     (user["id"] if user else None, datetime.now().isoformat(timespec="seconds"), action, row["id"]))
        others = [i for i in pf_recipients(conn) if not user or i != user["id"]]
    who = (user.get("display_name") or user.get("username")) if user else "jemand"
    send_push_async(others, f"Erledigt: {row['subject'][:80]}", f"{who} hat die Mail {action}.", "/hub#/postfach", f"mail-{row['id']}", "done")
    return True


MAIL_QUOTA_BYTES = 5 * 1024 ** 3          # gebuchte Größe je Postfach (5 GB), falls der Mailserver keine Grenze meldet
MAIL_QUOTA_WARN_FREE = 0.2 * 1024 ** 3     # Warnung, wenn weniger frei ist (bei 5 GB also ab 4,8 GB belegt)


def pf_quota(m):
    """Belegter Speicher des Postfachs über IMAP QUOTA (RFC 2087). None, wenn der Server das nicht meldet."""
    try:
        typ, data = m.getquotaroot("INBOX")
    except Exception:  # noqa
        return None
    hit = re.search(r"STORAGE (\d+) (\d+)", repr(data)) if typ == "OK" else None
    if not hit:
        return None
    used, limit = int(hit.group(1)) * 1024, int(hit.group(2)) * 1024   # Angaben in KB
    limit = limit or MAIL_QUOTA_BYTES
    return {"used": used, "limit": limit, "warn": limit - used <= MAIL_QUOTA_WARN_FREE}


NL_CONFIRM_HOURS = 48   # so lange gilt der Bestätigungslink der Newsletter-Anmeldung


def nl_key(conn):
    """Geheimer Schlüssel für die Abmeldelinks (wird beim ersten Mal erzeugt)."""
    k = secret_get(conn, "nl_key")
    if not k:
        k = secrets.token_hex(32)
        secret_set(conn, "nl_key", k)
    return k


def nl_token(key, sid):
    """Abmeldelink-Token: Abonnent-Nummer plus Signatur, nichts davon muss gespeichert werden."""
    return f"{sid}-" + hmac.new(key.encode(), f"nl-abmelden:{sid}".encode(), hashlib.sha256).hexdigest()[:32]


def nl_token_id(token):
    m = re.fullmatch(r"(\d{1,9})-([0-9a-f]{32})", token or "")
    if not m:
        return None
    with db() as conn:
        key = nl_key(conn)
    return int(m.group(1)) if hmac.compare_digest(nl_token(key, int(m.group(1))), token) else None


def nl_ids(v):
    try:
        x = json.loads(v or "[]")
    except ValueError:
        return []
    return [int(i) for i in x if str(i).isdigit()] if isinstance(x, list) else []


def nl_public_groups(conn):
    return [{"id": r["id"], "name": r["name"] or "", "description": r["description"] or ""} for r in conn.execute(
        "SELECT id, name, description FROM nl_groups WHERE IFNULL(public, 1)=1 AND IFNULL(active, 1)=1 ORDER BY IFNULL(sort, 0), name")]


def nl_pending(token):
    """Gültige, noch offene Bestätigung zu diesem Link?"""
    h = hashlib.sha256((token or "").encode()).hexdigest()
    with db() as conn:
        return bool(conn.execute("SELECT 1 FROM nl_subscribers WHERE confirm_hash=? AND confirm_expires > ?", (h, time.time())).fetchone())


def nl_mail(club, foot, subject, body, link):
    """Newsletter als Text und HTML (Logo, Absätze, Fuß mit Anschrift und Abmeldelink)."""
    text = body.rstrip() + f"\n\n--\n{club}\n{foot}\nDu bekommst diesen Newsletter, weil du ihn auf unserer Webseite abonniert hast.\nAbmelden: {link}\n"
    paras = "".join('<p style="margin:0 0 14px">' + html_esc(p.strip()).replace("\n", "<br>") + "</p>" for p in re.split(r"\n\s*\n", body.strip()) if p.strip())
    html = ('<div style="background:#f2f0eb;padding:20px 0"><div style="max-width:600px;margin:0 auto;background:#ffffff;font-family:Arial,Helvetica,sans-serif;color:#1a1a1a">'
            f'<div style="padding:18px 24px;border-bottom:4px solid #173F3A"><img src="{SIG_URL}logo.png" width="120" height="74" alt="{html_esc(club)}" style="display:block;border:0"></div>'
            f'<div style="padding:24px;font-size:15px;line-height:23px"><h1 style="font-size:22px;line-height:28px;margin:0 0 16px">{html_esc(subject)}</h1>{paras}</div>'
            f'<div style="padding:16px 24px;font-size:12px;line-height:18px;color:#777777;border-top:1px solid #eeeeee">{html_esc(foot)}<br>'
            f'Du bekommst diesen Newsletter, weil du ihn auf unserer Webseite abonniert hast. <a href="{html_esc(link)}" style="color:#173F3A">Newsletter abbestellen</a></div></div></div>')
    return text, html


def nl_recipients(conn, gids):
    """Aktive Abonnenten der Gruppen (leere Liste = alle aktiven)."""
    rows = conn.execute("SELECT id, email, groups FROM nl_subscribers WHERE status='aktiv' ORDER BY id").fetchall()
    return [r for r in rows if not gids or set(nl_ids(r["groups"])) & set(gids)]


def nl_send_issue(iid, user, base):
    """Ausgabe im Hintergrund an jeden Empfänger einzeln, mit persönlichem Abmeldelink (auch als List-Unsubscribe für Mailprogramme)."""
    with db() as conn:
        it = conn.execute("SELECT * FROM nl_issues WHERE id=?", (iid,)).fetchone()
        subs = nl_recipients(conn, nl_ids(it["groups"]))
        key, club, foot = nl_key(conn), get_setting(conn, "club_name") or "RSC Frya Fresena", letter_text(conn, "footer")
    cfg, ok, failed = smtp_config(), 0, 0
    for r in subs:
        link = f"{base}/newsletter/abmelden/{nl_token(key, r['id'])}"
        text, html = nl_mail(club, foot, it["subject"], it["body"], link)
        err = send_mail(cfg, r["email"], it["subject"], text, footer=False, html=html,
                        headers={"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"})
        ok, failed = (ok, failed + 1) if err else (ok + 1, failed)
        time.sleep(1)   # Mailserver schonen (Versandgrenzen der Anbieter)
    with db() as conn:
        conn.execute("UPDATE nl_issues SET status='gesendet', sent_at=?, sent_count=?, failed=? WHERE id=?", (datetime.now().isoformat(timespec="seconds"), ok, failed, iid))
        audit(conn, user, "Newsletter verschickt", "nl_issues", iid, it["subject"][:100], {"Empfänger": ["", f"{ok} von {len(subs)}"]})
    send_push_async([user["id"]], "Newsletter verschickt", f"{it['subject'][:60]}: {ok} von {len(subs)} raus" + (f", {failed} fehlgeschlagen" if failed else ""), "/hub#/newsletter", "nl-done")


PF_BULK_MAX = 200   # höchstens so viele Empfänger beim Einzelversand (Verteiler)


def pf_send_bulk(sender, rcpts, subject, text, html, headers, user):
    """Einzelmails an einen Verteiler im Hintergrund: nacheinander mit kurzer Pause (Versandgrenzen der Anbieter),
    Kopien unter „Gesendet“, am Ende Protokoll und Push an die absendende Person."""
    import imaplib
    ok, failed, raws = 0, [], []
    for a in rcpts:
        o = {}
        if send_mail(sender, a, subject, text, footer=False, headers=headers, out=o, html=html):
            failed.append(a)
        else:
            ok += 1
            raws.append(o.get("raw"))
        time.sleep(1)
    try:
        box = postfach_open(sender)[0]
        try:
            sent = pf_role_folder(box, "sent")
            for raw in raws if sent else []:
                if raw:
                    box.append(imap_quote(sent), "(\\Seen)", imaplib.Time2Internaldate(time.time()), raw)
        finally:
            with contextlib.suppress(Exception):
                box.logout()
    except Exception:  # noqa
        pass
    with db() as conn:
        audit(conn, user, "Mail gesendet", "postfach", None, subject[:100],
              {"An": f"{ok} von {len(rcpts)} einzeln" + (f", nicht verschickt: {', '.join(failed[:10])}" if failed else "")})
    send_push_async([user["id"]], "Verteiler verschickt", f"{subject[:60]}: {ok} von {len(rcpts)} Mails raus" + (f", {len(failed)} fehlgeschlagen" if failed else ""),
                    "/hub#/postfach", "bulk-done")


def pf_drop_draft(m, folder, uid):
    """Alten Entwurf entfernen, aber nur im Ordner „Entwürfe“ (sonst nichts löschen)."""
    if not folder or folder != pf_role_folder(m, "drafts") or not str(uid).isdigit():
        return False
    pf_select(m, folder, readonly=False)
    m.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
    try:
        m.uid("EXPUNGE", str(uid))   # nur diese Mail (UIDPLUS)
    except Exception:  # noqa
        m.expunge()
    return True


def pf_mark_all_handled(folder, user):
    """Alle offenen Mails eines Ordners im Vereinspostfach als erledigt markieren (ohne Verschieben). Liefert die Anzahl."""
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        rows = conn.execute("SELECT id, subject FROM pf_inbox WHERE folder=? AND status='neu'", (folder,)).fetchall()
        conn.execute("UPDATE pf_inbox SET status='erledigt', handled_by=?, handled_at=?, action='als erledigt markiert (alle)' WHERE folder=? AND status='neu'",
                     (user["id"], now, folder))
        others = [i for i in pf_recipients(conn) if i != user["id"]]
    for r in rows[:50]:   # offene Mitteilungen auf den Handys der anderen schließen (Push-Art done)
        send_push_async(others, f"Erledigt: {r['subject'][:80]}", "als erledigt markiert", "/hub#/postfach", f"mail-{r['id']}", "done")
    return len(rows)


def pf_poll():
    """Neue ungelesene Mails im Posteingang melden, Antworten von außerhalb erkennen. Beim ersten Lauf nur den Stand merken."""
    from email import policy
    from email.parser import BytesHeaderParser
    cfg = postfach_config()
    if not (cfg["imap"] and cfg["smtp_user"] and cfg["smtp_password"]):
        return 0
    m, _ = postfach_open(cfg)
    new_rows = []
    try:
        typ, data = m.select("INBOX", readonly=True)
        uv = (m.untagged_responses.get("UIDVALIDITY") or [b""])[0]
        uv = uv.decode() if isinstance(uv, bytes) else str(uv)
        with db() as conn:
            last = int(secret_get(conn, "pf_last_uid") or 0)
            known_uv = secret_get(conn, "pf_uidvalidity") or ""
        typ, data = m.uid("SEARCH", None, "ALL")
        uids = [int(x) for x in (data[0] or b"").split()]
        if not known_uv or known_uv != uv:   # erster Lauf oder Postfach neu aufgebaut: nichts Altes melden
            with db() as conn:
                secret_set(conn, "pf_uidvalidity", uv)
                secret_set(conn, "pf_last_uid", str(max(uids or [0])))
            return 0
        fresh = [u for u in uids if u > last]
        if fresh:
            typ, data = m.uid("FETCH", ",".join(map(str, fresh[-50:])), "(UID FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID)])")
            for x in data or []:
                if not isinstance(x, tuple):
                    continue
                um = re.search(rb"UID (\d+)", x[0])
                if not um or b"\\Seen" in x[0]:
                    continue
                h = BytesHeaderParser(policy=policy.compat32).parsebytes(x[1])
                new_rows.append((int(um.group(1)), pf_head(h.get("Subject")) or "(ohne Betreff)", pf_head(h.get("From")), (h.get("Message-ID") or "")[:300]))
            with db() as conn:
                secret_set(conn, "pf_last_uid", str(max(fresh)))
        # offene Mails: von außen beantwortet oder verschwunden?
        with db() as conn:
            open_rows = conn.execute("SELECT * FROM pf_inbox WHERE folder='INBOX' AND status='neu' AND uidvalidity=?", (uv,)).fetchall()
        done = []
        if open_rows:
            typ, data = m.uid("FETCH", ",".join(str(r["uid"]) for r in open_rows), "(UID FLAGS)")
            flags = {}
            for x in data or []:
                line = x[0] if isinstance(x, tuple) else x
                if isinstance(line, bytes):
                    um = re.search(rb"UID (\d+)", line)
                    if um:
                        flags[int(um.group(1))] = line
            for r in open_rows:
                f = flags.get(r["uid"])
                if f is None:
                    done.append((r, "gelöscht oder verschoben"))
                elif b"\\Answered" in f:
                    done.append((r, "beantwortet"))
    finally:
        with contextlib.suppress(Exception):
            m.logout()
    now = datetime.now().isoformat(timespec="seconds")
    with db() as conn:
        for uid, subj, frm, mid in new_rows:
            conn.execute("INSERT INTO pf_inbox (folder, uid, uidvalidity, message_id, subject, sender, received, status, notified) VALUES ('INBOX',?,?,?,?,?,?, 'neu', ?)",
                         (uid, uv, mid, subj[:250], frm[:250], now, now))
        ids = {uid: conn.execute("SELECT id FROM pf_inbox WHERE folder='INBOX' AND uid=? ORDER BY id DESC", (uid,)).fetchone()["id"] for uid, *_ in new_rows}
        rcpt = pf_recipients(conn)
    if new_rows:
        if len(new_rows) <= 3:
            for uid, subj, frm, _ in new_rows:
                send_push(rcpt, f"Neue Mail: {subj[:80]}", f"von {pf_name(frm)}", f"/hub#/postfach/m/INBOX/{uid}", f"mail-{ids[uid]}")
        else:
            send_push(rcpt, f"{len(new_rows)} neue Mails im Postfach", ", ".join(s_[:40] for _, s_, *_ in new_rows[:3]) + " ...", "/hub#/postfach", "mail-sammel")
    for r, action in done:
        pf_mark_handled("INBOX", r["uid"], None, action)
    return len(new_rows)


def pf_poll_loop():
    while True:
        threading.Event().wait(PF_POLL_SECONDS)
        try:
            pf_poll()
        except Exception as e:  # noqa
            print("Postfach-Abruf:", repr(e)[:200])


def mail_task_row(conn, t):
    names = {r["id"]: r["display_name"] or r["username"] for r in conn.execute("SELECT id, display_name, username FROM users")}
    d = dict(t)
    d["user_name"], d["by_name"] = names.get(t["user_id"], "?"), names.get(t["assigned_by"], "?")
    return d


def mail_task_access(conn, user_id, folder, uid):
    return bool(conn.execute("SELECT 1 FROM mail_tasks WHERE user_id=? AND folder=? AND uid=?", (user_id, folder, int(uid))).fetchone())


def smtp_config():
    with db() as conn:
        cfg = {k: secret_get(conn, k) for k in (*SMTP_KEYS, "smtp_password", "public_url")}
    cfg["hub_url"] = (cfg["hub_url"] or SITE_URL or cfg["public_url"] or "").rstrip("/")
    return cfg


def mail_sender(cfg):
    """Absender-Kopfzeile und Absender-Adresse für den Mailserver. Steht unter „Absender“ nur ein Name (z. B. „RSC Frya Fresena“),
    wird die Adresse des Postfachs (Benutzername) dazugenommen: „RSC Frya Fresena <info@beispiel.de>“.
    Viele Anbieter (z. B. Strato) verlangen, dass die Absenderadresse zum angemeldeten Postfach passt."""
    from email.utils import formataddr, parseaddr
    user = (cfg.get("smtp_user") or "").strip()
    raw = (cfg.get("smtp_from") or "").strip()
    name, addr = parseaddr(raw)
    if "@" not in addr:   # nur ein Name eingetragen
        name, addr = raw, user
    if "@" not in addr:
        addr = user
    envelope = user if "@" in user else addr
    return (formataddr((name, addr)) if name and name != addr else addr), envelope


def send_mail(cfg, to, subject, body, footer=True, attachments=(), headers=None, cc=(), out=None, html=None, related=()):
    """Schickt eine Klartext-Mail (optional mit Anhängen: (Dateiname, Bytes, Haupttyp, Untertyp)). Liefert None oder eine Fehlermeldung."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid
    msg = EmailMessage()
    msg["From"], envelope = mail_sender(cfg)
    msg["To"] = to
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    for k, v in (headers or {}).items():
        if v:
            msg[k] = v
    msg["Date"] = formatdate(localtime=True)   # ohne Datum und Message-ID sortieren Yahoo, Gmail und Co. Mails gern aus
    msg["Message-ID"] = make_msgid(domain=envelope.rsplit("@", 1)[-1] if "@" in envelope else None)
    msg.set_content(body + ((f"\n\nZum Hub: {cfg['hub_url']}/hub" if cfg["hub_url"] else "") +
                            "\n\nWelche Nachrichten du bekommst, stellst du im Hub unter „Mein Profil“ ein." if footer else ""))
    if html:   # HTML-Fassung (z. B. mit Signatur), Bilder eingebettet über cid
        msg.add_alternative(html, subtype="html")
        hpart = msg.get_payload()[-1]
        for cid, data in related:
            hpart.add_related(data, maintype="image", subtype="png", cid=f"<{cid}>", disposition="inline")
    for fname, data, maintype, subtype in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)
    try:
        port = int(cfg["smtp_port"] or (465 if cfg["smtp_security"] == "ssl" else 587))
        security = cfg["smtp_security"]
        if security != "none":   # Standardports haben feste Verschlüsselung: 465 = SSL, 587 = STARTTLS (häufige Verwechslung)
            security = {465: "ssl", 587: "starttls"}.get(port, security)
        ctx = ssl.create_default_context()
        if security == "ssl":
            s = smtplib.SMTP_SSL(cfg["smtp_host"], port, timeout=30, context=ctx)
        else:
            s = smtplib.SMTP(cfg["smtp_host"], port, timeout=30)
            if security != "none":
                s.starttls(context=ctx)
        with s:
            if cfg["smtp_user"]:
                # Nur der erste Anmeldeweg, sonst verdeckt Gmail die Ablehnung mit "Connection unexpectedly closed"
                s.ehlo_or_helo_if_needed()
                s.user, s.password = cfg["smtp_user"], cfg["smtp_password"] or ""
                if cfg["smtp_host"].lower().endswith(("gmail.com", "googlemail.com")):
                    s.password = "".join(s.password.split())   # App-Passwort wird mit Leerzeichen angezeigt: "abcd efgh ijkl mnop"
                mech = next((m for m in ("PLAIN", "LOGIN") if m in s.esmtp_features.get("auth", "").upper().split()), None)
                if mech:
                    s.auth(mech, s.auth_plain if mech == "PLAIN" else s.auth_login)
                else:
                    s.login(cfg["smtp_user"], cfg["smtp_password"])
            refused = s.send_message(msg, from_addr=envelope)
        if out is not None:
            out["raw"] = msg.as_bytes()
        if refused:
            return "Empfänger abgelehnt: " + ", ".join(f"{k} ({v[0]})" for k, v in refused.items())
        return None
    except smtplib.SMTPAuthenticationError as e:
        return (f"Anmeldung abgelehnt ({e.smtp_code}). Benutzername oder Passwort stimmen nicht. "
                "Bei Gmail die komplette Adresse und ein App-Passwort verwenden (Bestätigung in zwei Schritten muss an sein).")
    except Exception as e:  # noqa
        return f"{type(e).__name__}: {e}"


def notify(kind, subject, body, once_key=None):
    """Mail an alle, die diese Nachricht abonniert haben und die Rechte dafür haben. once_key: nur einmal verschicken."""
    cfg = smtp_config()
    if not cfg["smtp_host"]:
        return
    with db() as conn:
        if once_key:
            if conn.execute("SELECT 1 FROM notify_sent WHERE key=?", (once_key,)).fetchone():
                return
            conn.execute("INSERT INTO notify_sent (key, time) VALUES (?, ?)", (once_key, datetime.now().isoformat(timespec="seconds")))
        users = conn.execute("SELECT id, is_admin, email, notify FROM users WHERE active=1 AND IFNULL(email,'') != ''").fetchall()
        to = []
        for u in users:
            kinds = notify_kinds_of(u)
            if kind in kinds and notify_allowed(effective_perms(conn, row_dict(u)), kind):
                to.append(u["email"])
    errors = [e for e in (send_mail(cfg, addr, f"{APP_NAME}: {subject}", body) for addr in to) if e]   # einzeln, keine fremden Adressen sichtbar
    with db() as conn:
        secret_set(conn, "smtp_last_error", f"{datetime.now():%d.%m.%Y %H:%M} {errors[0]}" if errors else "")


def notify_async(*args, **kw):
    threading.Thread(target=lambda: notify(*args, **kw), daemon=True).start()






def fmt_day(d):
    return f"{d[8:10]}.{d[5:7]}.{d[:4]}" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or "") else (d or "")


def notify_tick():
    """Erinnerungen ab NOTIFY_HOUR, jede nur einmal (Schlüssel in notify_sent über once_key):
    Pferdegesundheit (health_remind_days vorher und am Tag), Nennungsschluss und Termin (7 und 1 Tag vorher), Vertragsende (30 Tage vorher)."""
    now = datetime.now()
    if now.hour < NOTIFY_HOUR:
        return
    today = now.date()
    with db() as conn:
        days = get_setting(conn, "health_remind_days")
        days = int(days) if days.isdigit() else 7
        health = conn.execute("SELECT h.id, h.kind, h.next_due, h.details, p.name FROM horse_health h JOIN horses p ON p.id = h.horse_id "
                              "WHERE p.active=1 AND IFNULL(h.next_due,'') != ''").fetchall()
        # nur der jüngste Eintrag je Pferd und Art zählt (ältere Termine sind durch einen neuen ersetzt)
        latest = {}
        for r in conn.execute("SELECT id, horse_id, kind, date FROM horse_health ORDER BY date, id"):
            latest[(r["horse_id"], r["kind"])] = r["id"]
        current = set(latest.values())
        events = conn.execute("SELECT * FROM events WHERE IFNULL(date,'') >= ?", (today.isoformat(),)).fetchall()
        contracts = conn.execute("SELECT c.*, p.name AS horse FROM boarding_contracts c LEFT JOIN horses p ON p.id = c.horse_id "
                                 "WHERE IFNULL(c.end,'') != '' AND c.status='Aktiv'").fetchall()
    for r in health:
        if r["id"] not in current:
            continue
        try:
            d = (date.fromisoformat(r["next_due"]) - today).days
        except ValueError:
            continue
        if d in (days, 0):
            when = "heute" if d == 0 else f"in {d} Tagen"
            notify("horse_health", f"{r['name']}: {r['kind']} {when}",
                   f"Für {r['name']} steht {when} ({fmt_day(r['next_due'])}) an: {r['kind']}" + (f" ({r['details']})" if r["details"] else "") + ".\n\n"
                   "Im Hub unter Stall > Pferde eintragen, sobald es erledigt ist.", once_key=f"health:{r['id']}:{r['next_due']}:{d}")
    for e in events:
        for field, label in (("entry_deadline", "Nennungsschluss"), ("date", "Termin")):
            v = e[field] or ""
            try:
                d = (date.fromisoformat(v) - today).days
            except ValueError:
                continue
            if d in (7, 1):
                notify("event_deadline", f"{e['title']}: {label} in {d} Tag{'en' if d > 1 else ''}",
                       f"{label} für „{e['title']}“ ist am {fmt_day(v)}.", once_key=f"event:{e['id']}:{field}:{v}:{d}")
    for c in contracts:
        try:
            d = (date.fromisoformat(c["end"]) - today).days
        except ValueError:
            continue
        if d == 30:
            notify("contract_end", f"Einstellervertrag {c['horse'] or c['boarder'] or ''} endet in 30 Tagen",
                   f"Der Einstellervertrag für {c['horse'] or '(ohne Pferd)'} ({c['boarder'] or 'ohne Name'}) endet am {fmt_day(c['end'])}.",
                   once_key=f"contract:{c['id']}:{c['end']}")


def notify_loop():
    while True:
        try:
            notify_tick()
        except Exception as e:  # noqa
            print("Benachrichtigungen fehlgeschlagen:", e)
        threading.Event().wait(NOTIFY_INTERVAL)


# ---------------------------------------------------------------------------
# Sicherungen
# ---------------------------------------------------------------------------
def db_snapshot(target: Path):
    """Konsistente Kopie der Datenbank (auch während des Betriebs)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with db() as src:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    os.replace(tmp, target)
    return target


def daily_backup():
    name = BACKUPS / f"rsc-{datetime.now():%Y-%m-%d}.db"
    if not name.exists():
        db_snapshot(name)
        print("Tägliche Sicherung erstellt:", name.name)
    olds = sorted(BACKUPS.glob("rsc-????-??-??.db"))
    for f in olds[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
        f.unlink(missing_ok=True)


def backup_loop():
    while True:
        try:
            daily_backup()
        except Exception as e:  # noqa
            print("Sicherung fehlgeschlagen:", e)
        try:   # Newsletter: wie in der Datenschutzerklärung zugesagt
            with db() as conn:
                conn.execute("DELETE FROM nl_subscribers WHERE status='offen' AND created < ?", ((datetime.now() - timedelta(days=30)).isoformat(timespec="seconds"),))
                conn.execute("DELETE FROM nl_subscribers WHERE status='abgemeldet' AND unsubscribed_at < ?", ((datetime.now() - timedelta(days=3 * 365)).isoformat(timespec="seconds"),))
        except Exception as e:  # noqa
            print("Newsletter aufräumen fehlgeschlagen:", e)
        threading.Event().wait(3600)


AUTH_TABLES = ("users", "sessions", "groups", "group_perms", "user_groups", "user_perms", "secrets", "audit_log", "user_2fa", "push_subs", "mail_tasks", "pf_inbox", "mail_accounts", "mail_links")


# ---------------------------------------------------------------------------
# Seite „Admin“ (/api/admin/info, nur Admins): Zustand des Servers und Hinweise für Updates
# ---------------------------------------------------------------------------
UPDATE_CACHE = {"at": 0.0, "version": "", "error": ""}


def latest_version(force=False):
    """Neueste Version auf GitHub (main), höchstens einmal pro Stunde abgefragt, nur die ersten 4 KB von server.py."""
    if not force and time.time() - UPDATE_CACHE["at"] < 3600:
        return UPDATE_CACHE
    UPDATE_CACHE["at"] = time.time()
    if not UPDATE_SOURCE:
        UPDATE_CACHE.update(version="", error="Versionsprüfung nicht eingerichtet (TMH_UPDATE_URL in der .env)")
        return UPDATE_CACHE
    try:
        req = urllib.request.Request(UPDATE_SOURCE, headers={"Range": "bytes=0-4095", "User-Agent": "RSCFryaFresenaHub"})
        with urllib.request.urlopen(req, timeout=6) as r:
            head = r.read(8192).decode("utf-8", "replace")
        m = re.search(r'^VERSION = "([0-9.]+)"', head, re.M)
        UPDATE_CACHE.update(version=m.group(1) if m else "", error="" if m else "Version nicht gefunden")
    except Exception as e:  # noqa
        UPDATE_CACHE.update(version="", error=f"GitHub nicht erreichbar ({type(e).__name__})")
    return UPDATE_CACHE


UPD_DIR = DATA / "updater"   # Austausch mit dem Update-Helfer auf dem VPS (deploy/updater): Anfrage rein, Status und Protokoll raus
UPD_ACTIONS = {"check": "Jetzt prüfen", "hub": "Hub aktualisieren", "caddy": "Caddy aktualisieren", "system": "System-Updates einspielen", "reboot": "Server neu starten"}
if BEHIND_PROXY:   # hinter dem zentralen Proxy: Proxy, System und Neustart betreffen andere Programme mit (wie im Update-Helfer)
    UPD_ACTIONS = {k: v for k, v in UPD_ACTIONS.items() if k in ("check", "hub")}


def updater_state():
    """Was der Update-Helfer zuletzt gemeldet hat, dazu die Versionen, die der Hub selbst kennt."""
    import ssl
    try:
        helper = json.loads((UPD_DIR / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        helper = None
    try:
        log = (UPD_DIR / "last.log").read_text(encoding="utf-8", errors="replace")[-30000:]
    except OSError:
        log = ""
    run = (helper or {}).get("running") or None
    if run:   # hängengebliebene Meldung (Helfer abgebrochen) nach 2 Stunden nicht mehr als „läuft“ werten
        try:
            if time.time() - datetime.fromisoformat(run["since"]).timestamp() > 7200:
                run = None
        except (KeyError, TypeError, ValueError):
            run = None
    latest = latest_version()
    return {"helper": helper, "running": run, "log": log, "pending": (UPD_DIR / "request").exists(), "installed": UPD_DIR.is_dir(),
            "hub": {"version": VERSION, "latest": latest["version"], "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                    "openssl": ssl.OPENSSL_VERSION}, "actions": UPD_ACTIONS, "host_dir": HOST_DIR}


def dir_size(d):
    n = size = 0
    if d.is_dir():
        for f in d.iterdir():
            if f.is_file():
                n += 1
                size += f.stat().st_size
    return n, size


def admin_info(force_update=False):
    disk = shutil.disk_usage(DATA)
    backups = sorted(BACKUPS.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True) if BACKUPS.is_dir() else []
    media_n, media_size = dir_size(MEDIA)
    site_n, _ = dir_size(DATA / "site")
    now = time.time()
    with db() as conn:
        users = conn.execute("SELECT id, username, display_name, is_admin, active, locked_until, pw_hash FROM users").fetchall()
        tfa = {r["user_id"] for r in conn.execute("SELECT user_id FROM user_2fa WHERE enabled=1")}
        st = {k: get_setting(conn, k) for k in ("maint_on", "twofa_admins", "twofa_finance", "site_impressum", "site_datenschutz", "site_noindex")}
        smtp_ok = bool(secret_get(conn, "smtp_host") and secret_get(conn, "smtp_user"))
    active = [u for u in users if u["active"]]
    admins = [u for u in active if u["is_admin"]]
    latest = latest_version(force_update)
    newer = bool(latest["version"]) and version_tuple(latest["version"]) > version_tuple(VERSION)
    open_legal = sum(len(re.findall(r"\[[^\]\n]{1,200}\]", st[k] or "")) for k in ("site_impressum", "site_datenschutz"))
    checks = []
    def add(level, text, link=""):
        checks.append({"level": level, "text": text, "link": link})
    if newer:
        add("info", f"Neue Version {latest['version']} auf GitHub (main). Unten unter „Update einspielen“ steht, wie es geht.")
    no2fa = [u["display_name"] or u["username"] for u in admins if u["id"] not in tfa]
    if no2fa:
        add("warn", "Admins ohne Zwei-Faktor-Anmeldung: " + ", ".join(no2fa), "#/profile")
    if st["twofa_admins"] != "1":
        add("warn", "Zwei-Faktor-Pflicht für Admins ist aus.", "#/accounts")
    if not smtp_ok:
        add("warn", "E-Mail-Versand ist nicht eingerichtet (Passwort vergessen, Kontaktformular und Benachrichtigungen gehen dann nicht).", "#/settings")
    if not backups:
        add("warn", "Noch keine automatische Sicherung vorhanden.")
    elif now - backups[0].stat().st_mtime > 2 * 86400:
        add("warn", "Die letzte automatische Sicherung ist älter als 2 Tage.")
    if disk.free / disk.total < 0.1:
        add("warn", f"Nur noch {disk.free // 2**30} GB Speicher frei.")
    if open_legal:
        add("warn", f"Impressum und Datenschutz: {open_legal} Angaben in [eckigen Klammern] offen.", "#/website/texte")
    if st["maint_on"] == "1":
        add("info", "Wartungsmodus ist an, Besucher sehen nur die Wartungsseite.", "#/wartung")
    locked = [u["display_name"] or u["username"] for u in active if (u["locked_until"] or 0) > now]
    if locked:
        add("info", "Gesperrte Konten (zu viele Fehlversuche): " + ", ".join(locked), "#/accounts")
    return {
        "version": VERSION, "latest": latest["version"], "latest_error": latest["error"], "newer": newer,
        "python": sys.version.split()[0], "server": SERVER_MODE, "started": datetime.fromtimestamp(STARTED).isoformat(timespec="seconds"),
        "uptime_h": round((now - STARTED) / 3600, 1), "site_url": SITE_URL, "behind_proxy": BEHIND_PROXY,
        "host_dir": HOST_DIR, "proxy_dir": "/opt/docker/proxy" if BEHIND_PROXY else "", "ssh_user": SSH_USER, "ssh_key": SSH_KEY, "repo": REPO_URL,
        "disk": {"total": disk.total, "free": disk.free}, "db_size": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "media": {"count": media_n, "size": media_size}, "site_images": site_n,
        "backups": [{"name": f.name, "size": f.stat().st_size, "time": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")} for f in backups[:8]],
        "backup_count": len(backups), "backup_keep": BACKUP_KEEP,
        "users": {"active": len(active), "admins": len(admins), "admins_2fa": len([u for u in admins if u["id"] in tfa]),
                  "with_2fa": len([u for u in active if u["id"] in tfa]), "pending": len([u for u in active if not u["pw_hash"]])},
        "status": {"maintenance": st["maint_on"] == "1", "smtp": smtp_ok, "noindex": st["site_noindex"] == "1",
                   "twofa_admins": st["twofa_admins"] == "1", "twofa_finance": st["twofa_finance"] == "1"},
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Änderungsprotokoll
# ---------------------------------------------------------------------------
AUDIT_SKIP = {"updated_at", "created_at", "public_token"}
AUDIT_KEEP_DAYS = 400
LABEL_KEYS = ("title", "name", "description", "holder", "section", "author", "date")


def audit_short(v):
    s = "" if v is None else str(v)
    return s if len(s) <= 120 else s[:117] + "..."


def audit_label(row):
    d = row_dict(row) if row is not None and not isinstance(row, dict) else (row or {})
    if d.get("first_name") or d.get("last_name"):
        return f"{d.get('first_name') or ''} {d.get('last_name') or ''}".strip()
    return next((audit_short(d[k]) for k in LABEL_KEYS if d.get(k)), "")


def audit(conn, user, action, table="", rid=None, label="", details=None):
    """user: angemeldeter Benutzer (dict) oder ein Name wie „Bestell-Link“ für Aktionen ohne Login."""
    if isinstance(user, dict):
        uid, name = user.get("id"), user.get("display_name") or user.get("username") or "?"
    else:
        uid, name = None, user or "System"
    conn.execute("INSERT INTO audit_log (time, user_id, user_name, action, table_name, record_id, label, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (datetime.now().isoformat(timespec="seconds"), uid, name, action, table, rid, (label or "")[:120],
                  json.dumps(details, ensure_ascii=False)[:6000] if details else ""))


def db_check_import(path, broken_msg):
    """Eingespielte Datenbank prüfen und entschärfen: muss intakt sein; Trigger und Views legt der Hub nie an,
    eine präparierte Datei könnte darüber aber bei jedem Speichern eigene SQL-Befehle ausführen. Darum werden alle entfernt."""
    test = sqlite3.connect(path)
    try:
        if test.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError(broken_msg)
        extras = test.execute("SELECT type, name FROM sqlite_master WHERE type IN ('trigger', 'view')").fetchall()
        for typ, name in extras:
            test.execute(f'DROP {typ.upper()} IF EXISTS "{name.replace(chr(34), chr(34) * 2)}"')
        test.commit()
        return len(extras)
    finally:
        test.close()


def restore_backup(raw: bytes):
    """Spielt ein Backup-ZIP (aus "Backup herunterladen") oder eine rsc.db ein.
    Benutzer und Sitzungen des Servers bleiben erhalten."""
    db_bytes, media = None, {}
    if raw[:16] == b"SQLite format 3\x00":
        db_bytes = raw
    else:
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile:
            raise ValueError("Das ist weder ein Backup-ZIP noch eine Datenbankdatei.")
        for info in z.infolist():
            n = info.filename.replace("\\", "/")
            if n.endswith("rsc.db") and db_bytes is None:
                db_bytes = z.read(info)
            elif "/media/" in "/" + n and not n.endswith("/"):
                base = n.rsplit("/", 1)[-1]
                if re.fullmatch(r"[A-Za-z0-9._-]{1,120}", base):
                    media[base] = info
        if db_bytes is None:
            raise ValueError("Im ZIP wurde keine rsc.db gefunden.")
        if db_bytes[:16] != b"SQLite format 3\x00":
            raise ValueError("Die rsc.db im ZIP ist keine gültige Datenbank.")

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    db_snapshot(BACKUPS / f"vor-wiederherstellung-{stamp}.db")
    new_path = DATA / "_restore.db"
    new_path.write_bytes(db_bytes)
    db_check_import(new_path, "Die Datenbank im Backup ist beschädigt.")   # Gültigkeit prüfen, Trigger und Views entfernen

    with SYNC_LOCK, DB_LOCK:
        keep = {}
        with db() as conn:
            for t in AUTH_TABLES:
                keep[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for suffix in ("-wal", "-shm"):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
        os.replace(new_path, DB_PATH)
        init_db()
        with db() as conn:
            for t in AUTH_TABLES:
                conn.execute(f"DELETE FROM {t}")
                cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
                for row in keep[t]:
                    row = {k: v for k, v in row.items() if k in cols}
                    conn.execute(f"INSERT INTO {t} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})", list(row.values()))
    count = 0
    if media:
        z = zipfile.ZipFile(io.BytesIO(raw))
        for base, info in media.items():
            target = MEDIA / base
            if not target.exists():
                target.write_bytes(z.read(info))
                count += 1
    return {"ok": True, "media": count}


# ---------------------------------------------------------------------------
# Serverumzug: Komplett-Export und -Import. Enthält ALLES (Konten, Gruppen, Rechte, Zugangsdaten,
# Protokoll, Vereinsdaten, Bilder), nur Sitzungen nicht. Die Datei ist mit einem Passwort verschlüsselt,
# nur mit der Standardbibliothek: Schlüssel per scrypt, Schlüsselstrom per SHAKE-256, Echtheit per
# HMAC-SHA256 über die ganze Datei (erst prüfen, dann entschlüsseln).
# Aufbau: MAGIC | Salz 16 | Nonce 16 | verschlüsseltes ZIP | HMAC 32
# ---------------------------------------------------------------------------
MIGRATE_MAGIC = b"RSCHUB-UMZUG-1\n"
MIGRATE_MAX = 2 * 1024 ** 3        # 2 GB
MIGRATE_CHUNK = 1024 * 1024


def migrate_keys(password, salt):
    k = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 15, r=8, p=1, maxmem=128 * 1024 * 1024, dklen=64)
    return k[:32], k[32:]


def migrate_xor(data, key, nonce, index):
    stream = hashlib.shake_256(key + nonce + index.to_bytes(8, "big")).digest(len(data))
    return (int.from_bytes(data, "big") ^ int.from_bytes(stream, "big")).to_bytes(len(data), "big")


def migrate_encrypt(src: Path, dst: Path, password):
    salt, nonce = os.urandom(16), os.urandom(16)
    enc_key, mac_key = migrate_keys(password, salt)
    mac = hmac.new(mac_key, digestmod=hashlib.sha256)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        head = MIGRATE_MAGIC + salt + nonce
        fo.write(head)
        mac.update(head)
        i = 0
        while block := fi.read(MIGRATE_CHUNK):
            c = migrate_xor(block, enc_key, nonce, i)
            fo.write(c)
            mac.update(c)
            i += 1
        fo.write(mac.digest())


def migrate_decrypt(src: Path, dst: Path, password):
    size = src.stat().st_size
    hl = len(MIGRATE_MAGIC) + 32
    with open(src, "rb") as fi:
        head = fi.read(hl)
        if size < hl + 32 or not head.startswith(MIGRATE_MAGIC):
            raise ValueError("Das ist keine Umzugsdatei des Hubs.")
        salt, nonce = head[len(MIGRATE_MAGIC):len(MIGRATE_MAGIC) + 16], head[len(MIGRATE_MAGIC) + 16:]
        enc_key, mac_key = migrate_keys(password, salt)
        mac = hmac.new(mac_key, head, hashlib.sha256)
        left = size - hl - 32
        while left > 0:
            block = fi.read(min(MIGRATE_CHUNK, left))
            mac.update(block)
            left -= len(block)
        if not hmac.compare_digest(mac.digest(), fi.read(32)):
            raise ValueError("Das Passwort ist falsch oder die Datei ist beschädigt.")
        fi.seek(hl)
        left, i = size - hl - 32, 0
        with open(dst, "wb") as fo:
            while left > 0:
                block = fi.read(min(MIGRATE_CHUNK, left))
                fo.write(migrate_xor(block, enc_key, nonce, i))
                left -= len(block)
                i += 1


MIGRATE_DIRS = {"site": SITE_DIR}   # außer data/media beim Serverumzug mitnehmen (neue Datenordner hier eintragen)


def migrate_export(password) -> Path:
    """Baut die verschlüsselte Umzugsdatei und liefert ihren Pfad (wird nach dem Herunterladen gelöscht)."""
    stamp = secrets.token_hex(16)
    tmpdb, tmpzip, out = DATA / f"_umzug_{stamp}.db", DATA / f"_umzug_{stamp}.zip", DATA / f"_umzug_{stamp}.rschub"
    try:
        db_snapshot(tmpdb)
        c = sqlite3.connect(tmpdb)
        try:
            c.execute("DELETE FROM sessions")   # Anmeldungen ziehen nicht mit um
            c.commit()
            users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        finally:
            c.close()
        files = [f for f in MEDIA.iterdir() if f.is_file()]
        # Dazu eigene Webseitenbilder (data/site) und ausgestellte Rechnungen (data/rechnungen)
        extra = [(folder, f) for folder, d in MIGRATE_DIRS.items() if d.is_dir() for f in d.iterdir() if f.is_file()]
        with zipfile.ZipFile(tmpzip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("umzug.json", json.dumps({"app": APP_NAME, "version": VERSION, "created": datetime.now().isoformat(timespec="seconds"),
                                                 "users": users, "media": len(files), "files": len(extra)}, ensure_ascii=False))
            z.write(tmpdb, "data/rsc.db")
            for f in files:   # Bilder und Videos sind schon komprimiert
                z.write(f, f"data/media/{f.name}", compress_type=zipfile.ZIP_STORED)
            for folder, f in extra:
                z.write(f, f"data/{folder}/{f.name}", compress_type=zipfile.ZIP_STORED)
        migrate_encrypt(tmpzip, out, password)
        return out
    finally:
        tmpdb.unlink(missing_ok=True)
        tmpzip.unlink(missing_ok=True)


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def migrate_import(src: Path, password):
    """Ersetzt den kompletten Stand durch die Umzugsdatei. Vorher wird der eigene Stand gesichert."""
    tmpzip, newdb = DATA / "_umzug_import.zip", DATA / "_umzug_import.db"
    try:
        migrate_decrypt(src, tmpzip, password)
        try:
            z = zipfile.ZipFile(tmpzip)
        except zipfile.BadZipFile:
            raise ValueError("Die Umzugsdatei ist beschädigt.")
        with z:
            meta = json.loads(z.read("umzug.json").decode("utf-8"))
            if version_tuple(meta.get("version")) > version_tuple(VERSION):
                raise ValueError(f"Die Datei stammt von Version {meta.get('version')}. Bitte diesen Hub zuerst auf mindestens diese Version bringen.")
            newdb.write_bytes(z.read("data/rsc.db"))
            db_check_import(newdb, "Die Datenbank in der Umzugsdatei ist beschädigt.")
            db_snapshot(BACKUPS / f"vor-umzug-import-{datetime.now():%Y-%m-%d_%H%M%S}.db")
            with SYNC_LOCK, DB_LOCK:
                with db() as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for suffix in ("-wal", "-shm"):
                    Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
                os.replace(newdb, DB_PATH)
                init_db()
            count = 0
            targets = {"media": MEDIA, **MIGRATE_DIRS}
            for info in z.infolist():
                parts = info.filename.split("/")
                if len(parts) != 3 or parts[0] != "data" or parts[1] not in targets or not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", parts[2]) \
                        or parts[2].startswith("."):
                    continue
                d = targets[parts[1]]
                d.mkdir(parents=True, exist_ok=True)
                with z.open(info) as fi, open(d / parts[2], "wb") as fo:
                    shutil.copyfileobj(fi, fo, MIGRATE_CHUNK)
                count += parts[1] == "media"
        return {"ok": True, "version": meta.get("version"), "created": meta.get("created"), "users": meta.get("users"), "media": count}
    finally:
        tmpzip.unlink(missing_ok=True)
        newdb.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Einstellungen und kleine Helfer
# ---------------------------------------------------------------------------
SYNC_LOCK = threading.RLock()   # sperrt Restore und Serverumzug gegen gleichzeitige Schreibvorgänge



def get_setting(conn, key):
    r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else DEFAULT_SETTINGS.get(key, "")


def set_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))






# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class PayloadTooLarge(Exception):
    pass



class _NoBody:
    """Ziel für den Inhalt bei HEAD-Anfragen: nimmt alles an und verwirft es."""
    def write(self, b):
        return len(b)

    def flush(self):
        pass

class Handler(BaseHTTPRequestHandler):
    server_version = "RSCFryaFresenaHub/" + VERSION
    timeout = 60   # Sekunden ohne Daten, dann wird die Verbindung getrennt (halb geschickte Anfragen blockieren sonst einen Thread)

    def log_message(self, fmt, *args):  # ruhige Konsole
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # -- Antworten --------------------------------------------------------
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json_cors(self, obj, status=200):
        """Öffentliche, rein lesende Daten dürfen von der Vereinswebseite abgerufen werden."""
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, download_name=None, cache=False):
        if not path.is_file():
            return self.send_error(404)
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
        self.send_header("Content-Length", str(len(data)))
        # Dateien der Webseite im Browser behalten: mit Versionsstempel (?v=), Schriften und Dateien mit eindeutigem Namen ein Jahr,
        # übrige Bilder eine Woche; alles andere immer frisch prüfen
        forever = cache and (cache == "long" or path.suffix == ".woff2" or "v=" in urlparse(self.path).query)
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if forever else "public, max-age=604800" if cache else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        self.wfile.write(data)

    def send_stream(self, path: Path, download_name):
        """Große Datei in Stücken senden (nicht komplett in den Speicher laden)."""
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile, MIGRATE_CHUNK)

    def save_body(self, target: Path, limit):
        """Großen Upload direkt in eine Datei schreiben."""
        length = int(self.headers.get("Content-Length") or 0)
        if length > limit:
            raise PayloadTooLarge()
        left = length
        with open(target, "wb") as f:
            while left > 0:
                block = self.rfile.read(min(MIGRATE_CHUNK, left))
                if not block:
                    break
                f.write(block)
                left -= len(block)

    def read_body(self, limit=MAX_BODY):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise PayloadTooLarge()
        if length > limit or length < 0:   # negative Länge würde bis zum Verbindungsende lesen und den Thread blockieren
            raise PayloadTooLarge()
        return self.rfile.read(length) if length else b""

    def read_json(self):
        raw = self.read_body(MAX_JSON)
        return json.loads(raw.decode("utf-8")) if raw else {}

    def parts(self):
        return [unquote(p) for p in urlparse(self.path).path.strip("/").split("/") if p]

    def redirect(self, to, status=302, cookie=None):
        self.send_response(status)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def send_html(self, html, status=200, headers=None):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        # Grundlegende Sicherheits-Header (Caddy ergänzt HSTS)
        self.send_header("X-Content-Type-Options", "nosniff")
        # Öffentliche Seiten darf nur der Hub selbst einbetten (Vorschau unter Webseite), alles andere nie
        self.send_header("X-Frame-Options", "SAMEORIGIN" if getattr(self, "frame_ok", False) else "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        # Content-Security-Policy: Die Webseite lädt nur eigene Skripte, Schriften und Bilder. Der Hub nutzt noch Inline-Handler,
        # dort sperrt die Regel Plugins, fremde <base>-Adressen, Formulare an fremde Ziele und das Einbetten.
        if getattr(self, "frame_ok", False):
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                                        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'")
        else:
            self.send_header("Content-Security-Policy", "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
        super().end_headers()
        if self.command == "HEAD":   # Kopfzeilen sind raus, den Inhalt verwerfen
            self.wfile = _NoBody()

    # -- Anfragende Person ------------------------------------------------
    def client_ip(self):
        if TRUST_PROXY:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[0].strip()
        return self.client_address[0]

    def is_local_direct(self):
        return (self.client_address[0] in ("127.0.0.1", "::1")
                and not self.headers.get("X-Forwarded-For") and not SERVER_MODE)

    def session_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    def current_user(self):
        if hasattr(self, "_user"):
            return self._user
        self._user = None
        tok = self.session_token()
        if tok:
            with db() as conn:
                r = conn.execute("SELECT u.id, u.username, u.display_name, u.is_admin, u.active, u.member_id, s.expires "
                                 "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash=?", (token_hash(tok),)).fetchone()
                if r and r["expires"] > time.time() and r["active"]:
                    u = {"id": r["id"], "username": r["username"], "display_name": r["display_name"] or r["username"],
                         "is_admin": bool(r["is_admin"]), "member_id": r["member_id"]}
                    u["perms"] = effective_perms(conn, u)
                    u["twofa"] = twofa_enabled(conn, u["id"])
                    # Pflicht, aber noch nicht eingerichtet: nur das eigene Profil zum Einrichten ist erreichbar
                    u["must_2fa"] = not u["twofa"] and twofa_required(conn, u, u["perms"])
                    self._user = u
                elif r:
                    conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(tok),))
        return self._user

    def cookie_header(self, value, max_age):
        parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if SECURE_COOKIES:
            parts.append("Secure")
        return "; ".join(parts)

    def start_session(self, user_id):
        tok = secrets.token_urlsafe(32)
        now = datetime.now().isoformat(timespec="seconds")
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            conn.execute("INSERT INTO sessions (token_hash, user_id, expires, created_at, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
                         (token_hash(tok), user_id, time.time() + SESSION_DAYS * 86400, now, self.client_ip(),
                          (self.headers.get("User-Agent") or "")[:200]))
            conn.execute("UPDATE users SET last_login=? WHERE id=?", (now, user_id))
        return tok

    def send_session(self, user_id, extra=None):
        """Neue Sitzung anlegen und das Cookie mit der Antwort setzen."""
        tok = self.start_session(user_id)
        body = json.dumps({"ok": True, **(extra or {})}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", self.cookie_header(tok, SESSION_DAYS * 86400))
        self.end_headers()
        self.wfile.write(body)

    # -- Routing ----------------------------------------------------------
    def do_GET(self):
        self.handle_request("GET")

    def do_HEAD(self):
        """Wie GET, aber ohne Inhalt (Suchmaschinen, Linkprüfer und Monitoring fragen oft nur den Kopf ab; vorher kam 501)."""
        real = self.wfile
        try:
            self.handle_request("GET")
        finally:
            self.wfile = real   # die Verbindung bleibt für weitere Anfragen offen

    def do_POST(self):
        self.handle_request("POST")

    def do_PUT(self):
        self.handle_request("PUT")

    def do_DELETE(self):
        self.handle_request("DELETE")

    def handle_request(self, method):
        try:
            self.route(method)
        except PayloadTooLarge:
            self.send_json({"error": "Datei zu groß"}, 413)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def route(self, method):
        p = self.parts()
        head = p[0] if p else ""

        # Öffentlich erreichbar
        if method == "GET" and head == "healthz":
            return self.send_json({"ok": True, "version": VERSION})
        if method == "GET" and p in (["static", "style.css"], ["static", "logo.png"], ["static", "icon.png"],
                                     ["static", "icon-192.png"], ["static", "icon-512.png"],
                                     ["static", "apple-touch-icon.png"], ["static", "icon-maskable-512.png"]):
            return self.serve_under(STATIC, p[1:])
        if method == "GET" and p == ["favicon.ico"]:   # Suchmaschinen und Browser fragen das ohne Link-Angabe (PNG wird überall angenommen)
            return self.serve_under(STATIC, ["icon-192.png"])
        if method == "GET" and p in (["apple-touch-icon.png"], ["apple-touch-icon-precomposed.png"]):   # iPhone fragt das ohne Link-Angabe
            return self.serve_under(STATIC, ["apple-touch-icon.png"])
        if method == "GET" and p in (["manifest.webmanifest"], ["sw.js"]):   # App auf dem Startbildschirm
            return self.send_file(STATIC / p[0])
        if head == "login" and method == "GET":
            if self.current_user():
                return self.redirect("/hub")
            if user_count() == 0:
                return self.redirect("/setup")
            return self.send_html(LOGIN_PAGE)
        if head == "setup" and method == "GET":
            if user_count() > 0:
                return self.redirect("/login")
            return self.send_html(SETUP_PAGE if self.is_local_direct() else NO_USER_PAGE)
        if head == "invite" and method == "GET" and len(p) == 2:
            with db() as conn:
                u = conn.execute("SELECT username, display_name, email, invite_profile FROM users WHERE invite_hash=? AND invite_expires > ? AND active=1",
                                 (token_hash(p[1]), time.time())).fetchone()
            if not u:
                return self.send_html(INVALID_INVITE_PAGE, 404)
            return self.send_html(invite_page(u, p[1]))
        if head == "auth":
            return self.api_auth(method, p[1:])
        if head == "passwort-vergessen" and method == "GET":
            return self.send_html(FORGOT_SENT_PAGE if p[1:] == ["gesendet"] else FORGOT_PAGE)
        if head == "passwort-neu" and method == "GET" and len(p) == 2:
            with db() as conn:
                u = conn.execute("SELECT username, display_name FROM users WHERE reset_hash=? AND reset_expires > ? AND active=1",
                                 (token_hash(p[1]), time.time())).fetchone()
            if not u:
                return self.send_html(INVALID_RESET_PAGE, 404)
            return self.send_html(RESET_PAGE.replace("%%NAME%%", html_esc(u["display_name"] or u["username"]))
                                  .replace("%%USER%%", html_esc(u["username"])).replace("%%TOKEN%%", html_esc(p[1])))
        if head == "bild" and method == "GET" and len(p) == 3:
            # Einzelnes Bild oder PDF aus der Mediathek für die Webseite: nur mit gültiger Signatur (public_image, public_file)
            name = p[2]
            if re.fullmatch(r"[A-Za-z0-9._-]{1,120}", name) and Path(name).suffix.lower() in PUBLIC_FILE_EXT \
                    and hmac.compare_digest(p[1], share_sig(share_key(), name)):
                return self.serve_under(MEDIA, [name], inline=MEDIA_INLINE, cache=True)
            return self.send_error(404)
        if head == "api" and p[1:2] == ["public"]:
            return self.api_public(method, p[2:])
        if head == "newsletter":   # Newsletter ohne Konto: anmelden (Double-Opt-In), bestätigen, abmelden
            q = parse_qs(urlparse(self.path).query)
            arg = lambda k, n: (q.get(k) or [""])[0][:n]
            if len(p) == 1 and method == "GET":
                with db() as conn:
                    groups = nl_public_groups(conn)
                return self.send_site("newsletter", nl={"mode": "form", "sent": arg("gesendet", 20), "email": arg("email", 150), "groups": groups})
            if len(p) == 1 and method == "POST":
                if self.maint_blocks():
                    with contextlib.suppress(PayloadTooLarge):
                        self.read_body(20000)
                    return self.redirect("/newsletter")
                return self.public_newsletter()
            if len(p) == 3 and p[1] == "bestaetigen":
                if method == "POST":
                    return self.public_newsletter_confirm(p[2])
                return self.send_site("newsletter", nl={"mode": "confirm", "token": p[2][:80], "valid": nl_pending(p[2])})
            if len(p) == 3 and p[1] == "abmelden":
                sid = nl_token_id(p[2])
                if method == "POST":   # Knopf auf der Seite oder Ein-Klick-Abmeldung aus dem Mailprogramm (RFC 8058)
                    with contextlib.suppress(PayloadTooLarge):
                        self.read_body(2000)
                    if sid:
                        with db() as conn:
                            n = conn.execute("UPDATE nl_subscribers SET status='abgemeldet', unsubscribed_at=?, confirm_hash=NULL WHERE id=? AND status!='abgemeldet'",
                                             (datetime.now().isoformat(timespec="seconds"), sid)).rowcount
                            if n:
                                audit(conn, "Webseite", "Newsletter abgemeldet", "nl_subscribers", sid, "")
                    return self.redirect("/newsletter?gesendet=abgemeldet")
                return self.send_site("newsletter", nl={"mode": "unsub", "token": p[2][:80], "valid": bool(sid)})
            return self.send_site("404", 404)
        if head == "kontakt" and method == "POST" and len(p) == 1 and self.maint_blocks():
            try:   # Wartungsmodus: Formulare der Webseite nehmen nichts an
                self.read_body(20000)
            except PayloadTooLarge:
                pass
            return self.redirect("/")
        if head == "kontakt" and method == "POST":
            return self.public_contact()
        if method == "GET" and p in (["sitemap.xml"], ["robots.txt"]):
            with db() as conn:
                noindex = get_setting(conn, "site_noindex") == "1"
                sm = web_sitemap(conn)
            if head == "sitemap.xml":
                body = website.sitemap(self.base_url(), public_articles(), sm, noindex).encode("utf-8")
            else:
                body = website.robots(self.base_url(), noindex).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/xml; charset=utf-8" if head == "sitemap.xml" else "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            return self.wfile.write(body)
        # Öffentliche Webseite: Seiten, Dateien unter /web/assets, sonst 404-Seite (Hub-Pfade brauchen weiter Login)
        if method == "GET" and len(p) == 1 and head in website.REDIRECTS:   # alte Adressen der ersten Fassung
            q = urlparse(self.path).query
            return self.redirect(website.REDIRECTS[head] + ("?" + q if q else ""), 301)
        if method == "GET" and p == ["links"]:   # Link-Seite für die Instagram-Bio, nirgends verlinkt
            return self.send_site("links")
        if method == "GET" and (not p or (len(p) == 1 and head in website.PAGES)):
            return self.send_site(head)
        if method == "GET" and head == "news" and len(p) == 2:   # einzelner Artikel, nur veröffentlichte
            m = re.match(r"(\d{1,9})(?:-|$)", p[1])
            a = next((x for x in public_articles() if m and x["id"] == int(m.group(1))), None)
            if not a:
                return self.send_site("404", 404)
            if p[1] != a["slug"]:   # alte oder gekürzte Adresse: auf die aktuelle weiterleiten
                return self.redirect(f"/news/{a['slug']}", 301)
            return self.send_site("news-artikel", article=a)
        if method == "GET" and head == "signatur" and len(p) == 2 and re.fullmatch(r"[a-z0-9-]{1,40}\.png", p[1]):   # Bilder der Mail-Signatur
            return self.serve_under(SIG_DIR, p[1:], cache=True)
        if method == "GET" and p[:2] == ["web", "assets"]:
            return self.serve_under(website.WEB / "assets", p[2:], cache=True)
        if method == "GET" and p[:2] == ["web", "site"] and len(p) == 3 and p[2].endswith(".webp"):
            return self.serve_under(SITE_DIR, p[2:], cache="long")    # Name mit Zufallsstempel, neues Bild = neuer Name
        if method == "GET" and head not in ("hub", "static", "media", "api"):
            return self.send_site("404", 404)

        # Ab hier nur mit Anmeldung
        user = self.current_user()
        if not user:
            if head == "api":
                return self.send_json({"error": "Nicht angemeldet"}, 401)
            return self.redirect("/login")

        if method != "GET" and self.headers.get("X-TMH") != "1":
            return self.send_json({"error": "Ungültige Anfrage"}, 403)

        if p == ["hub"] and method == "GET":   # der Hub (Mitgliederbereich)
            return self.send_file(STATIC / "index.html")
        if head == "static" and method == "GET":
            return self.serve_under(STATIC, p[1:])
        if head == "media" and method == "GET":
            if user["must_2fa"]:
                return self.send_error(403)
            return self.serve_under(MEDIA, p[1:], inline=MEDIA_INLINE)
        if head == "api":
            return self.api(method, p[1:], user)
        return self.send_error(404)

    def serve_under(self, root: Path, rest, inline=None, cache=False):
        target = (root / "/".join(rest)).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            return self.send_error(403)
        # Hochgeladene Dokumente (HTML, SVG, Office ...) nie im Hub-Kontext öffnen, sondern herunterladen
        download = target.name if inline is not None and target.suffix.lower() not in inline else None
        return self.send_file(target, download, cache)

    def base_url(self):
        """Eigene Adresse für Links (Sitemap, Mails, Teilen): SITE_URL aus der .env, sonst der Host-Header (nur gültige Zeichen).
        X-Forwarded-Host wird bewusst nie übernommen, sonst könnte eine Anfrage fremde Adressen in Links und Mails schreiben."""
        if SITE_URL:
            return SITE_URL
        host = self.headers.get("Host") or ""
        if not re.fullmatch(r"[A-Za-z0-9.\-]+(:\d{1,5})?", host):
            host = f"127.0.0.1:{PORT}"
        proto = self.headers.get("X-Forwarded-Proto") if TRUST_PROXY else None
        if proto not in ("http", "https"):
            proto = "https" if SECURE_COOKIES else "http"
        return f"{proto}://{host}"



    def maint_view(self):
        """Wartungsmodus: (Einstellungen, darf die Webseite trotzdem sehen). Vorbei nur angemeldet mit Recht Wartung (Admins immer)."""
        with db() as conn:
            st = {k: get_setting(conn, k) for k in ("maint_on", "maint_title", "maint_text", "maint_until")}
        if st["maint_on"] != "1" and "wartung-vorschau" not in (self.path or ""):
            return st, False   # aus: keine Sitzung nachschlagen
        u = self.current_user()
        return st, bool(u and not u["must_2fa"] and u["perms"].get("wartung", 0) >= 1)

    def maint_blocks(self):
        """True, wenn Besucher gerade nichts über die Webseite einreichen dürfen (Kontakt, Kartenbestellung)."""
        st, staff = self.maint_view()
        return st["maint_on"] == "1" and not staff

    def send_site(self, slug, status=200, article=None, nl=None):
        """Seite der öffentlichen Webseite aus den öffentlichen Daten bauen."""
        st, staff = self.maint_view()
        on = st["maint_on"] == "1"
        preview = staff and (parse_qs(urlparse(self.path).query).get("wartung-vorschau") or [""])[0] == "1"
        if (on and not staff and slug not in MAINT_OPEN_PAGES) or preview:
            html = website.render("wartung", {"info": public_info(), "site": public_site(), "base": self.base_url(), "nav": public_nav(),
                                              "maint": {"title": st["maint_title"], "text": st["maint_text"], "until": st["maint_until"], "preview": preview}})
            self.frame_ok = True
            try:
                return self.send_html(html, 200 if preview else 503, None if preview else {"Retry-After": "3600"})
            finally:
                self.frame_ok = False
        info = public_info()
        if slug == "hallenplan" and info.get("site_arena_public") != "1":   # nicht öffentlich: website.py zeigt die 404-Seite
            status = 404
        data = {"info": info, "site": public_site(), "base": self.base_url(), "nav": public_nav(),
                "articles": public_articles() if slug in ("", "news") else [], "article": article, "nl": nl,
                "horses": public_horses() if slug in ("", "pferde", "reitunterricht") else [],
                "fees": public_fees() if slug == "mitgliedschaft" else [],
                "events": public_events() if slug in ("", "termine") else [],
                "arena": public_arena() if slug == "hallenplan" else [],
                "membership_form": public_file(info.get("site_membership_form"), share_key()) if slug == "mitgliedschaft" else ""}
        if slug == "links":
            with db() as conn:
                data["links"] = web_links(conn)
        q = parse_qs(urlparse(self.path).query)
        sent = (q.get("gesendet") or [None])[0]
        data["preselect"] = (q.get("anliegen") or [""])[0]   # Vorauswahl im Kontaktformular (website.py prüft den Wert)
        data["maint_bar"] = on and staff
        html = website.render(slug, data, sent)
        self.frame_ok = True
        try:
            return self.send_html(html, status)
        finally:
            self.frame_ok = False

    # -- Anmeldung --------------------------------------------------------
    def api_auth(self, method, p):
        name = p[0] if p else ""
        if method != "POST" or self.headers.get("X-TMH") != "1":
            return self.send_json({"error": "Ungültige Anfrage"}, 403)
        if name == "login":
            ip = self.client_ip()
            if login_blocked(ip):
                return self.send_json({"error": "Zu viele Fehlversuche. Bitte in 10 Minuten erneut versuchen."}, 429)
            data = self.read_json()
            username = (data.get("username") or "").strip()[:80]
            locked_msg = f"Zu viele Fehlversuche mit diesem Benutzernamen. Bitte in {ACCOUNT_LOCK_MINUTES} Minuten erneut versuchen."
            with db() as conn:
                u = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                if u and not u["active"]:
                    u = None
                if account_locked(conn, u, username):
                    login_failed(ip)
                    time.sleep(0.6)
                    return self.send_json({"error": locked_msg}, 429)
                if u:
                    ok = check_pw(data.get("password") or "", u["pw_hash"])
                else:
                    dummy_check_pw(data.get("password") or "")
                    ok = False
                if not ok:
                    login_failed(ip)
                    account_failed(conn, u, username, ip)
                    locked = account_locked(conn, conn.execute("SELECT * FROM users WHERE id=?", (u["id"],)).fetchone() if u else None, username)
                if ok:
                    need = twofa_enabled(conn, u["id"])
                    if not need:
                        account_ok(conn, u["id"])
            if not ok:
                time.sleep(0.6)
                return self.send_json({"error": locked_msg if locked else "Benutzername oder Passwort falsch."}, 429 if locked else 401)
            if need:   # Passwort stimmt, jetzt noch der Code aus der App (Sitzung erst danach)
                return self.send_json({"need_2fa": True, "ticket": twofa_ticket_new(u["id"])})
            return self.send_session(u["id"])
        if name == "login2":
            ip = self.client_ip()
            if login_blocked(ip):
                return self.send_json({"error": "Zu viele Fehlversuche. Bitte in 10 Minuten erneut versuchen."}, 429)
            data = self.read_json()
            th = token_hash(data.get("ticket") or "")
            with FAILED_LOCK:
                t = TWOFA_TICKETS.get(th)
                if t and (t["expires"] < time.time() or t["tries"] >= ACCOUNT_FAIL_MAX):
                    TWOFA_TICKETS.pop(th, None)
                    t = None
            if not t:
                return self.send_json({"error": "Die Anmeldung ist abgelaufen. Bitte noch einmal mit Benutzername und Passwort anmelden.", "restart": True}, 400)
            with db() as conn:
                u = conn.execute("SELECT * FROM users WHERE id=? AND active=1", (t["uid"],)).fetchone()
                if not u or account_locked(conn, u, ""):
                    with FAILED_LOCK:
                        TWOFA_TICKETS.pop(th, None)
                    return self.send_json({"error": f"Zu viele Fehlversuche mit diesem Benutzernamen. Bitte in {ACCOUNT_LOCK_MINUTES} Minuten erneut versuchen.",
                                           "restart": True}, 429)
                kind = twofa_check(conn, u["id"], data.get("code") or "")
                if not kind:
                    login_failed(ip)
                    account_failed(conn, u, u["username"], ip)
                    with FAILED_LOCK:
                        t["tries"] += 1
                else:
                    account_ok(conn, u["id"])
                    with FAILED_LOCK:
                        TWOFA_TICKETS.pop(th, None)
                    left = len(json.loads(twofa_row(conn, u["id"])["recovery"] or "[]"))
                    if kind == "recovery":
                        audit(conn, "Anmeldung", "Wiederherstellungscode benutzt", "users", u["id"], u["display_name"] or u["username"], {"übrig": ["", left]})
                        email = account_email(conn, u)
            if not kind:
                time.sleep(0.6)
                return self.send_json({"error": "Der Code stimmt nicht. Bitte den aktuellen Code aus der App eingeben."}, 401)
            if kind == "recovery":
                cfg = smtp_config()
                if email and cfg["smtp_host"]:
                    body = (f"Hallo {u['display_name'] or u['username']},\n\nbei deinem Konto im RSC Frya Fresena Hub (Benutzername: {u['username']}) wurde gerade "
                            f"ein Wiederherstellungscode für die Anmeldung benutzt ({datetime.now():%d.%m.%Y %H:%M}). Übrig sind noch {left}.\n\n"
                            "Warst du das nicht? Dann ändere bitte sofort dein Passwort und melde dich beim Vorstand bzw. Admin.")
                    threading.Thread(target=lambda: send_mail(cfg, email, "RSC Frya Fresena Hub: Wiederherstellungscode benutzt", body, footer=False), daemon=True).start()
            return self.send_session(u["id"], {"recovery_left": left, "next": "/hub#/profile"} if kind == "recovery" else None)
        if name == "logout":
            tok = self.session_token()
            if tok:
                with db() as conn:
                    conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(tok),))
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Set-Cookie", self.cookie_header("", 0))
            self.end_headers()
            self.wfile.write(body)
            return
        if name == "invite":
            data = self.read_json()
            tok = data.get("token") or ""
            with db() as conn:
                u = conn.execute("SELECT * FROM users WHERE invite_hash=? AND invite_expires > ? AND active=1",
                                 (token_hash(tok), time.time())).fetchone()
            if not u:
                return self.send_json({"error": "Der Einladungslink ist ungültig oder abgelaufen."}, 400)
            prob = pw_problem(data.get("password") or "")
            if prob:
                return self.send_json({"error": prob}, 400)
            f = {k: str(v or "").strip() for k, v in data.items() if isinstance(v, (str, int))}
            # Benutzername: frei wählbar, solange er gültig und frei ist
            try:
                uname = check_username(f.get("username") or u["username"])
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            first, last = f.get("first_name", "")[:60], f.get("last_name", "")[:60]
            email, phone = f.get("email", "")[:150], f.get("phone", "")[:40]
            if not (first and last):
                return self.send_json({"error": "Bitte Vor- und Nachnamen angeben."}, 400)
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                return self.send_json({"error": "Bitte eine gültige E-Mail-Adresse angeben."}, 400)
            if phone and not re.fullmatch(r"[0-9 +()/.-]{6,40}", phone):
                return self.send_json({"error": "Bitte eine gültige Telefonnummer angeben."}, 400)
            with db() as conn:
                if uname.lower() != (u["username"] or "").lower() and conn.execute("SELECT 1 FROM users WHERE username=? AND id != ?", (uname, u["id"])).fetchone():
                    return self.send_json({"error": "Diesen Benutzernamen gibt es schon. Bitte einen anderen wählen."}, 400)
                conn.execute("UPDATE users SET username=?, pw_hash=?, display_name=?, email=?, phone=?, invite_hash=NULL, invite_expires=NULL, invite_profile=NULL WHERE id=?",
                             (uname, hash_pw(data["password"]), f"{first} {last}", email, phone, u["id"]))
                conn.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))
                audit(conn, {"id": u["id"], "username": uname, "display_name": f"{first} {last}"}, "Einladung angenommen", "users", u["id"], f"{first} {last}",
                      {"Benutzername": ["", uname]})
                need = twofa_enabled(conn, u["id"])
            if need:   # mit Zwei-Faktor-Anmeldung nie direkt anmelden, der Code gehört dazu
                return self.send_json({"ok": True, "next": "/login"})
            return self.send_session(u["id"])
        if name == "forgot":
            # Immer dieselbe Antwort, egal ob es das Konto gibt (keine Auskunft über Konten)
            if reset_rate_ok("ip", self.client_ip()):
                request_password_reset(self.read_json().get("login"), self.base_url())
            time.sleep(0.4)
            return self.send_json({"ok": True})
        if name == "reset":
            data = self.read_json()
            tok = data.get("token") or ""
            with db() as conn:
                u = conn.execute("SELECT * FROM users WHERE reset_hash=? AND reset_expires > ? AND active=1", (token_hash(tok), time.time())).fetchone()
            if not u:
                return self.send_json({"error": "Der Link ist ungültig oder abgelaufen. Bitte unter „Passwort vergessen“ einen neuen anfordern."}, 400)
            prob = pw_problem(data.get("password") or "")
            if prob:
                return self.send_json({"error": prob}, 400)
            with db() as conn:
                conn.execute("UPDATE users SET pw_hash=?, reset_hash=NULL, reset_expires=NULL, invite_hash=NULL, invite_expires=NULL WHERE id=?",
                             (hash_pw(data["password"]), u["id"]))
                conn.execute("DELETE FROM sessions WHERE user_id=?", (u["id"],))   # überall abmelden
                account_ok(conn, u["id"])   # Sperre aufheben: der Link aus der Mail zeigt, dass es die Person selbst ist
                audit(conn, "Passwort vergessen", "Passwort per Link neu gesetzt", "users", u["id"], u["display_name"] or u["username"])
                email = account_email(conn, u)
                need = twofa_enabled(conn, u["id"])
            cfg = smtp_config()
            if email and cfg["smtp_host"]:
                body = (f"Hallo {u['display_name'] or u['username']},\n\ndas Passwort für dein Konto im RSC Frya Fresena Hub (Benutzername: {u['username']}) "
                        f"wurde gerade über „Passwort vergessen“ geändert ({datetime.now():%d.%m.%Y %H:%M}).\n\n"
                        "Warst du das nicht? Dann melde dich bitte sofort beim Vorstand bzw. Admin.")
                threading.Thread(target=lambda: send_mail(cfg, email, "RSC Frya Fresena Hub: Dein Passwort wurde geändert", body, footer=False), daemon=True).start()
            if need:   # mit Zwei-Faktor-Anmeldung nie direkt anmelden, der Code gehört dazu
                return self.send_json({"ok": True, "next": "/login"})
            return self.send_session(u["id"])
        if name == "setup":
            if user_count() > 0 or not self.is_local_direct():
                return self.send_json({"error": "Ersteinrichtung nicht möglich."}, 403)
            data = self.read_json()
            try:
                create_user(data.get("username"), data.get("password") or "", is_admin=True)
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            return self.send_json({"ok": True})
        return self.send_json({"error": "Unbekannter Pfad"}, 404)

    # -- Öffentliche Schnittstelle für die Webseite (ohne Login, nur lesen) --
    def api_public(self, method, p):
        what = p[0] if p else ""
        if method != "GET" or len(p) != 1:
            return self.send_json({"error": "Nicht gefunden"}, 404)
        fn = {"info": public_info, "termine": public_events, "pferde": public_horses, "beitraege": public_fees}.get(what)
        if not fn:
            return self.send_json({"error": "Nicht gefunden"}, 404)
        body = json.dumps(fn(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # -- Kontaktformular der Webseite (ohne Login, funktioniert ohne JavaScript) --
    def public_contact(self):
        back = lambda res: self.redirect(f"/kontakt?gesendet={res}#formular")
        try:
            raw = self.read_body(20000).decode("utf-8")
        except (PayloadTooLarge, UnicodeDecodeError):
            return back("fehler")
        f = {k: (v[0] or "").strip() for k, v in parse_qs(raw).items()}
        if f.get("website"):                       # verstecktes Feld: nur Bots füllen es aus, still verwerfen
            return back("ok")
        if not reset_rate_ok("ip", "kontakt:" + self.client_ip()):
            return back("fehler")
        name, email, phone, msg = f.get("name", "")[:100], f.get("email", "")[:150], f.get("phone", "")[:40], f.get("message", "")[:3000]
        age, exp = f.get("age", "")[:20], f.get("experience", "")[:200]
        source = f.get("anliegen") if f.get("anliegen") in CONTACT_TOPICS else "Webseite"
        if not name or not msg or f.get("consent") != "1" or not (email or phone):
            return back("fehler")
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return back("fehler")
        now = datetime.now().isoformat(timespec="seconds")
        with db() as conn:
            cur = conn.execute("INSERT INTO contact_requests (name, email, phone, message, source, age, experience, status, created, created_at, updated_at) "
                               "VALUES (?, ?, ?, ?, ?, ?, ?, 'Neu', ?, ?, ?)", (name, email, phone, msg, source, age, exp, now, now, now))
            audit(conn, "Webseite", "neu", "contact_requests", cur.lastrowid, name)
            to = get_setting(conn, "site_contact_email")
            confirm = (get_setting(conn, "site_contact_confirm") or "").strip()
        text = (f"Neue Anfrage über die Webseite ({source})\n\nName: {name}\nE-Mail: {email or '-'}\nTelefon: {phone or '-'}\n"
                + (f"Alter: {age}\n" if age else "") + (f"Reiterfahrung: {exp}\n" if exp else "") + f"\n{msg}")
        cfg = smtp_config()
        if cfg["smtp_host"]:
            # Antworten im Hub oder im Mailprogramm gehen direkt an die anfragende Person (Reply-To)
            from email.utils import formataddr
            reply = formataddr((re.sub(r"[\r\n\"<>]", " ", name).strip(), email)) if email else ""
            def deliver():
                if to:
                    send_mail(cfg, to, f"Webseite: Anfrage von {name}", text, footer=False, headers={"Reply-To": reply})
                # Bestätigung ohne Inhalte aus dem Formular (sonst ließe sich das Formular für Spam an fremde Adressen nutzen)
                if email and confirm:
                    send_mail(cfg, email, "Deine Anfrage beim RSC Frya Fresena", f"Hallo,\n\n{confirm}\n\nViele Grüße\nRSC Frya Fresena",
                              footer=False, headers={"Reply-To": to or ""})
            threading.Thread(target=deliver, daemon=True).start()
        notify_async("contact_request", f"Neue Anfrage von {name}", text + "\n\nIm Hub unter Mitglieder > Anfragen.")
        return back("ok")

    # -- Newsletter: Anmeldung mit Double-Opt-In. Gleiche Antwort, ob die Adresse schon bekannt ist oder nicht (keine Auskunft).
    # Gruppen ändern sich erst nach der Bestätigung, so kann niemand fremde Abos verstellen. Begrenzung pro IP und pro Adresse.
    def public_newsletter(self):
        back = lambda res: self.redirect(f"/newsletter?gesendet={res}#anmelden")
        try:
            raw = self.read_body(20000).decode("utf-8")
        except (PayloadTooLarge, UnicodeDecodeError):
            return back("fehler")
        q = parse_qs(raw)
        f = {k: (v[0] or "").strip() for k, v in q.items()}
        if f.get("website"):
            return back("ok")
        email, name = f.get("email", "")[:150], re.sub(r"[\x00-\x1f<>]", "", f.get("name", ""))[:80]
        if f.get("consent") != "1" or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return back("fehler")
        if not reset_rate_ok("ip", "newsletter:" + self.client_ip()) or not reset_rate_ok("user", "newsletter:" + email.lower()):
            return back("ok")   # zu viele Versuche: still, damit niemand fremde Postfächer zumüllt
        with db() as conn:
            pub = {g["id"] for g in nl_public_groups(conn)}
            chosen = sorted({int(x) for x in q.get("gruppe", []) if str(x).isdigit() and int(x) in pub})
            if pub and not chosen:
                return back("gruppe")
            token = secrets.token_urlsafe(24)
            h, exp, now = hashlib.sha256(token.encode()).hexdigest(), time.time() + NL_CONFIRM_HOURS * 3600, datetime.now().isoformat(timespec="seconds")
            row = conn.execute("SELECT id FROM nl_subscribers WHERE email=?", (email,)).fetchone()
            if row:
                conn.execute("UPDATE nl_subscribers SET pending_groups=?, confirm_hash=?, confirm_expires=?, name=COALESCE(NULLIF(?, ''), name) WHERE id=?",
                             (json.dumps(chosen), h, exp, name, row["id"]))
            else:
                conn.execute("INSERT INTO nl_subscribers (email, name, status, groups, pending_groups, confirm_hash, confirm_expires, source, created) "
                             "VALUES (?, ?, 'offen', '[]', ?, ?, ?, 'Webseite', ?)", (email, name, json.dumps(chosen), h, exp, now))
            club = get_setting(conn, "club_name") or "RSC Frya Fresena"
        link = f"{self.base_url()}/newsletter/bestaetigen/{token}"
        cfg = smtp_config()
        if cfg["smtp_host"]:
            text = (f"Hallo{(' ' + name) if name else ''},\n\nbitte bestätige deine Anmeldung zum Newsletter der {club}:\n{link}\n\n"
                    f"Der Link gilt {NL_CONFIRM_HOURS} Stunden. Wenn du dich nicht angemeldet hast, ignoriere diese E-Mail einfach, dann passiert nichts.\n\nViele Grüße\n{club}")
            threading.Thread(target=send_mail, args=(cfg, email, f"Bitte bestätige deine Anmeldung zum Newsletter der {club}", text), kwargs={"footer": False}, daemon=True).start()
        return back("ok")

    def public_newsletter_confirm(self, token):
        with contextlib.suppress(PayloadTooLarge):
            self.read_body(2000)
        h = hashlib.sha256((token or "").encode()).hexdigest()
        with db() as conn:
            r = conn.execute("SELECT * FROM nl_subscribers WHERE confirm_hash=? AND confirm_expires > ?", (h, time.time())).fetchone()
            if not r:
                return self.redirect("/newsletter?gesendet=abgelaufen")
            groups = sorted(set(nl_ids(r["pending_groups"])) | (set(nl_ids(r["groups"])) if r["status"] == "aktiv" else set()))
            now = datetime.now().isoformat(timespec="seconds")
            conn.execute("UPDATE nl_subscribers SET status='aktiv', groups=?, pending_groups='[]', confirm_hash=NULL, confirm_expires=NULL, consent_at=?, consent_text=?, "
                         "unsubscribed_at=NULL WHERE id=?", (json.dumps(groups), now, website.NL_CONSENT, r["id"]))
            audit(conn, "Webseite", "Newsletter bestätigt", "nl_subscribers", r["id"], "")
        return self.redirect("/newsletter?gesendet=bestaetigt")




    # -- API --------------------------------------------------------------
    def deny(self, msg="Dafür fehlen dir die Rechte."):
        return self.send_json({"error": msg}, 403)


    def api(self, method, p, user):
        try:
            if not p:
                return self.send_json({"app": APP_NAME, "version": VERSION})
            name = p[0]
            self.user = user   # für das Änderungsprotokoll
            perms = user["perms"]
            admin = user["is_admin"]
            can = lambda area, lvl=1: perms.get(area, 0) >= lvl
            # Wartung gibt nur Zugang zur Webseite im Wartungsmodus, kein Leserecht auf Nachschlage-Tabellen
            any_read = any(v >= 1 for a, v in perms.items() if a not in ("wartung", "postfach"))
            any_write = any(v >= 2 for a, v in perms.items() if a not in ("wartung", "postfach"))

            # Zwei-Faktor-Pflicht, aber noch nicht eingerichtet: nur Profil, eigenes Konto und Einstellungen lesen
            if user.get("must_2fa") and not (name in ("me", "profile") or (name == "settings" and method == "GET")):
                return self.send_json({"error": "Bitte richte zuerst unter „Mein Profil“ die Zwei-Faktor-Anmeldung ein.", "need_2fa": True}, 403)

            # --- eigenes Konto und Profil (alle)
            if name == "me" and len(p) > 1 and p[1] == "2fa":
                return self.api_twofa(method, p[2:], user)
            if name == "me":
                if method == "GET":
                    with db() as conn:
                        groups = [r["name"] for r in conn.execute(
                            "SELECT g.name FROM user_groups ug JOIN groups g ON g.id = ug.group_id WHERE ug.user_id=? ORDER BY g.sort, g.name", (user["id"],))]
                        own_addr = own_mail_address(conn, user["id"])
                        boxes = [{"key": k, "address": mail_addr(c)} for k, c in user_mail_boxes(conn, user["id"])]
                        urow = conn.execute("SELECT tabbar, dash_layout FROM users WHERE id=?", (user["id"],)).fetchone()
                        try:
                            tabbar = json.loads(urow["tabbar"] or "[]")
                        except (ValueError, TypeError):
                            tabbar = []
                        try:
                            dash_layout = json.loads(urow["dash_layout"] or "null")
                        except (ValueError, TypeError):
                            dash_layout = None
                    return self.send_json({**user, "perms": {**user["perms"], "ownmail": 2 if own_addr else 0}, "own_mail": own_addr, "mail_boxes": boxes, "tabbar": tabbar, "dash_layout": dash_layout,
                                           "groups": groups, "areas": AREAS, "server": SERVER_MODE, "data": str(DATA) if admin else ""})
                if method == "PUT" and len(p) > 1 and p[1] == "password":
                    data = self.read_json()
                    with db() as conn:
                        u = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
                    if not check_pw(data.get("old") or "", u["pw_hash"]):
                        return self.send_json({"error": "Das bisherige Passwort stimmt nicht."}, 400)
                    prob = pw_problem(data.get("new") or "")
                    if prob:
                        return self.send_json({"error": prob}, 400)
                    with db() as conn:
                        conn.execute("UPDATE users SET pw_hash=?, reset_hash=NULL, reset_expires=NULL WHERE id=?", (hash_pw(data["new"]), user["id"]))
                        conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash != ?", (user["id"], token_hash(self.session_token())))
                    return self.send_json({"ok": True})
            if name == "profile":
                return self.api_profile(method, user)

            # --- Verwaltung (nur Admins)
            if name == "admin":
                if not admin:
                    return self.deny("Nur für Admins.")
                return self.api_admin(method, p[1:], user)
            if name == "restore" and method == "POST":
                if not admin:
                    return self.deny("Nur für Admins.")
                try:
                    res = restore_backup(self.read_body())
                    with db() as conn:
                        audit(conn, user, "Backup eingespielt", "backup", None, f"{res.get('media', 0)} neue Bilder")
                    return self.send_json(res)
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
            if name in ("backup", "export", "backups"):
                if not admin:
                    return self.deny("Nur für Admins.")
                if name == "backups" and method == "GET":
                    files = sorted(BACKUPS.glob("*.db"), reverse=True)
                    return self.send_json([{"name": f.name, "size": f.stat().st_size} for f in files])
                if name == "backup" and method == "GET":
                    with db() as conn:
                        audit(conn, user, "Backup heruntergeladen", "backup")
                    return self.api_backup()
                if name == "export" and method == "GET":
                    return self.api_export()

            if name == "settings":
                if method != "GET" and not admin:
                    return self.deny("Nur Admins können Einstellungen ändern.")
                return self.api_settings(method, can("finanzen", 1))
            if name == "dashboard" and method == "GET":
                return self.api_dashboard(perms)
            if name == "tabbar" and method == "PUT":   # eigene Tab-Leiste (nur für das eigene Konto, nur Seitennamen, Rechte prüft das Menü)
                pages = self.read_json().get("pages")
                pages = [x for x in pages if isinstance(x, str) and re.fullmatch(r"[a-z]{2,20}", x)][:4] if isinstance(pages, list) else []
                with db() as conn:
                    conn.execute("UPDATE users SET tabbar=? WHERE id=?", (json.dumps(pages) if pages else None, user["id"]))
                return self.send_json({"ok": True, "tabbar": pages})
            if name == "dashlayout" and method == "PUT":   # eigene Übersicht (nur das eigene Konto; welche Widgets erlaubt sind, prüfen die Daten dahinter)
                items = self.read_json().get("widgets")
                clean = [{"k": x["k"], "w": 2 if x.get("w") == 2 else 1} for x in items
                         if isinstance(x, dict) and isinstance(x.get("k"), str) and re.fullmatch(r"[a-z]{2,20}", x["k"])][:40] if isinstance(items, list) else None
                with db() as conn:
                    conn.execute("UPDATE users SET dash_layout=? WHERE id=?", (json.dumps(clean) if clean is not None else None, user["id"]))
                return self.send_json({"ok": True, "dash_layout": clean})
            if name == "navcounts" and method == "GET":   # Zahlen an den Menüpunkten (nur was die Person sehen darf)
                out = {}
                today = date.today().isoformat()
                soon = (date.today() + timedelta(days=7)).isoformat()
                with db() as conn:
                    if any(perms.get(a, 0) >= 1 for a in TABLE_AREAS_ANY["contact_requests"]):
                        out["contacts"] = conn.execute("SELECT COUNT(*) FROM contact_requests WHERE IFNULL(status, 'Neu')='Neu'").fetchone()[0]
                    if perms.get("stall", 0) >= 1:
                        # fällige Termine je Pferd und Art: nur der jüngste Eintrag zählt
                        out["health"] = conn.execute(
                            "SELECT COUNT(*) FROM horse_health h JOIN horses p ON p.id=h.horse_id WHERE p.active=1 AND IFNULL(h.next_due,'') != '' "
                            "AND h.next_due <= ? AND h.id = (SELECT x.id FROM horse_health x WHERE x.horse_id=h.horse_id AND x.kind=h.kind "
                            "ORDER BY x.date DESC, x.id DESC LIMIT 1)", (soon,)).fetchone()[0]
                    if perms.get("mitglieder", 0) >= 1 or perms.get("finanzen", 0) >= 1:
                        out["fees"] = conn.execute("SELECT COUNT(*) FROM fee_payments WHERE IFNULL(paid,0)=0 AND IFNULL(due,'') != '' AND due < ?", (today,)).fetchone()[0]
                return self.send_json(out)
            if name == "upload" and method == "POST":
                if not any_write:
                    return self.deny()
                return self.api_upload(library=can("medien", 2))
            if name == "fees" and method == "POST" and p[1:] == ["generate"]:
                # Beitragsforderungen für einen Zeitraum anlegen: aktive Mitglieder mit Beitragsart, je Familie nur einmal bei Familienbeiträgen.
                # Schon vorhandene Forderungen (gleiches Mitglied bzw. gleiche Familie und gleicher Zeitraum) werden übersprungen.
                if not (can("mitglieder", 2) or can("finanzen", 2)):
                    return self.deny()
                data = self.read_json()
                period, due = str(data.get("period") or "").strip()[:20], str(data.get("due") or "").strip()[:10]
                if not period or (due and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due)):
                    return self.send_json({"error": "Bitte einen Zeitraum (z. B. 2027) und ein gültiges Fälligkeitsdatum angeben."}, 400)
                created, skipped = fee_generate(period, due, self.user)
                return self.send_json({"ok": True, "created": created, "skipped": skipped})
            if name == "fees" and method == "POST" and p[1:] == ["book"]:
                # Sammelbuchung: alle bezahlten, noch nicht gebuchten Beiträge als eine Einnahme in den Finanzen
                if not can("finanzen", 2):
                    return self.deny("Für Buchungen braucht es Bearbeiten-Rechte bei Finanzen.")
                with db() as conn:
                    rows = conn.execute("SELECT id, amount FROM fee_payments WHERE paid=1 AND booked_tx IS NULL AND amount IS NOT NULL").fetchall()
                    if not rows:
                        return self.send_json({"error": "Es gibt keine neuen bezahlten Beiträge."}, 400)
                    now = datetime.now().isoformat(timespec="seconds")
                    vals = {"date": now[:10], "kind": "Einnahme", "category": "Mitgliedsbeiträge",
                            "description": f"Mitgliedsbeiträge: {len(rows)} Zahlung{'en' if len(rows) > 1 else ''}",
                            "amount": round(sum(r["amount"] or 0 for r in rows), 2), "status": "Erledigt", "paid_by": "Mitglieder",
                            "notes": "Beiträge: " + ", ".join(f"#{r['id']}" for r in rows), "created_at": now, "updated_at": now}
                    cur = conn.execute(f"INSERT INTO transactions ({', '.join(vals)}) VALUES ({', '.join('?' for _ in vals)})", list(vals.values()))
                    conn.execute(f"UPDATE fee_payments SET booked_tx=? WHERE id IN ({', '.join('?' for _ in rows)})", [cur.lastrowid, *[r["id"] for r in rows]])
                    audit(conn, user, "neu", "transactions", cur.lastrowid, vals["description"], {"Betrag": vals["amount"], "Beiträge": vals["notes"]})
                return self.send_json({"ok": True, "transaction_id": cur.lastrowid, "amount": vals["amount"], "count": len(rows)})
            if name == "postfach":
                return self.api_postfach(method, p[1:], user, can)
            if name == "mailtasks":
                return self.api_mailtasks(method, p[1:], user, can)
            if name == "push":
                return self.api_push(method, p[1:], user)
            if name == "maintenance":
                return self.api_maintenance(method, p[1:], user, can)
            if name == "website":
                return self.api_website(method, p[1:], user, can)
            if name == "newsletter":
                return self.api_newsletter(method, p[1:], user, can)
            if name == "notify":
                return self.api_notify(method, p[1:], user)
            if name == "migrate":
                if not admin:
                    return self.deny("Nur für Admins.")
                return self.api_migrate(method, p[1:], user)

            if name in TABLES:
                area = TABLE_AREA.get(name)
                lvl = max(perms.get(a, 0) for a in TABLE_AREAS_ANY.get(name, (area,)))
                if name in ADMIN_TABLES:
                    if not admin:
                        return self.deny("Nur für Admins.")
                    lvl = 2
                if method == "GET":
                    if not (lvl >= 1 or (name in SHARED_READ and any_read)):
                        return self.deny()
                elif name == "media" and method == "POST":
                    return self.deny()
                elif lvl < 2:
                    return self.deny(f"Für den Bereich {AREAS.get(area, area)} hast du nur Leserechte.")
                rid = int(p[1]) if len(p) > 1 else None
                return self.api_table(method, name, rid, perms)

            return self.send_json({"error": "Unbekannter Pfad"}, 404)
        except PayloadTooLarge:
            raise
        except Exception as e:  # noqa
            print("Fehler:", repr(e))
            return self.send_json({"error": str(e)}, 500)

    # -- Serverumzug (nur Admins) ------------------------------------------
    def api_migrate(self, method, p, user):
        sub = p[0] if p else ""
        if method == "POST" and sub == "export":
            pw = self.read_json().get("password") or ""
            if len(pw) < 10:
                return self.send_json({"error": "Das Passwort für die Umzugsdatei braucht mindestens 10 Zeichen."}, 400)
            with db() as conn:   # vorher protokollieren, damit der Eintrag mit umzieht
                audit(conn, user, "Komplett-Export erstellt", "backup", None, "Serverumzug")
            path = migrate_export(pw)
            return self.send_json({"download": f"/api/migrate/download/{path.stem[len('_umzug_'):]}", "size": path.stat().st_size})
        if method == "GET" and sub == "download" and len(p) == 2 and re.fullmatch(r"[0-9a-f]{32}", p[1]):
            path = DATA / f"_umzug_{p[1]}.rschub"
            if not path.is_file():
                return self.send_json({"error": "Die Umzugsdatei ist nicht mehr da. Bitte neu erstellen."}, 404)
            try:
                return self.send_stream(path, f"RSCFryaFresenaHub-Umzug-{datetime.now():%Y-%m-%d}.rschub")
            finally:
                path.unlink(missing_ok=True)   # nur einmal herunterladbar
        if method == "POST" and sub == "import":
            try:
                pw = base64.b64decode(self.headers.get("X-Umzug-Passwort") or "").decode("utf-8")
            except ValueError:
                pw = ""
            if not pw:
                return self.send_json({"error": "Bitte das Passwort der Umzugsdatei angeben."}, 400)
            upload = DATA / f"_umzug_upload_{secrets.token_hex(8)}.rschub"
            try:
                self.save_body(upload, MIGRATE_MAX)
                res = migrate_import(upload, pw)
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
            finally:
                upload.unlink(missing_ok=True)
            with db() as conn:
                audit(conn, user, "Komplett-Import eingespielt", "backup", None, "Serverumzug", res)
            return self.send_json(res)
        return self.send_json({"error": "Unbekannter Pfad"}, 404)

    # -- E-Mail-Versand (nur Admins) ---------------------------------------
    def api_notify(self, method, p, user):
        if not user["is_admin"]:
            return self.deny("Nur Admins können den E-Mail-Versand einrichten.")
        sub = p[0] if p else ""
        if method == "GET" and not sub:
            with db() as conn:
                cfg = {k: secret_get(conn, k) for k in SMTP_KEYS}
                cfg["password_set"] = bool(secret_get(conn, "smtp_password"))
                cfg["last_error"] = secret_get(conn, "smtp_last_error")
            return self.send_json(cfg)
        if method == "PUT" and not sub:
            data = self.read_json()
            with db() as conn:
                for k in SMTP_KEYS:
                    if k in data:
                        secret_set(conn, k, str(data[k] or "").strip())
                if (data.get("smtp_password") or "").strip():
                    secret_set(conn, "smtp_password", data["smtp_password"].strip())
                audit(conn, user, "geändert", "settings", None, "E-Mail-Versand", {k: audit_short(data.get(k)) for k in SMTP_KEYS if k in data})
            return self.send_json({"ok": True})
        if method == "POST" and sub == "test":
            to = (self.read_json().get("to") or "").strip()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
                return self.send_json({"error": "Bitte eine gültige E-Mail-Adresse angeben."}, 400)
            cfg = smtp_config()
            if not cfg["smtp_host"]:
                return self.send_json({"error": "Es ist noch kein Mailserver eingetragen."}, 400)
            err = send_mail(cfg, to, "RSC Frya Fresena Hub: Test-Nachricht", "Der E-Mail-Versand des RSC Frya Fresena Hub funktioniert.")
            if err:
                return self.send_json({"error": f"Versand fehlgeschlagen: {err}"}, 400)
            return self.send_json({"ok": True})
        return self.send_json({"error": "Unbekannter Pfad"}, 404)


    # -- Zwei-Faktor-Anmeldung des eigenen Kontos (/api/me/2fa) ------------
    def api_twofa(self, method, p, user):
        what = p[0] if p else ""
        uid = user["id"]
        if method == "GET" and not what:
            with db() as conn:
                r = twofa_row(conn, uid)
                on = bool(r and r["enabled"])
                return self.send_json({"enabled": on, "required": twofa_required(conn, user, user["perms"]),
                                       "recovery_left": len(json.loads(r["recovery"] or "[]")) if on else 0,
                                       "enabled_at": r["enabled_at"] if on else None})
        if method != "POST":
            return self.send_json({"error": "Unbekannter Pfad"}, 404)
        data = self.read_json()
        if what in ("setup", "recovery", "disable"):   # nur mit dem Passwort (schützt vor fremder Nutzung einer offenen Sitzung)
            with db() as conn:
                pw_hash = conn.execute("SELECT pw_hash FROM users WHERE id=?", (uid,)).fetchone()["pw_hash"]
            if not check_pw(data.get("password") or "", pw_hash):
                login_failed(self.client_ip())
                time.sleep(0.6)
                return self.send_json({"error": "Das Passwort stimmt nicht."}, 400)
        with db() as conn:
            r = twofa_row(conn, uid)
            on = bool(r and r["enabled"])
            if what == "setup":
                if on:
                    return self.send_json({"error": "Die Zwei-Faktor-Anmeldung ist schon eingerichtet."}, 400)
                secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
                conn.execute("INSERT INTO user_2fa (user_id, enabled, pending_secret, pending_expires) VALUES (?, 0, ?, ?) "
                             "ON CONFLICT(user_id) DO UPDATE SET pending_secret=excluded.pending_secret, pending_expires=excluded.pending_expires",
                             (uid, secret, time.time() + 900))
                return self.send_json({"secret": secret, "uri": totp_uri(secret, user["username"]), "issuer": TOTP_ISSUER, "account": user["username"]})
            if what == "enable":
                if on:
                    return self.send_json({"error": "Die Zwei-Faktor-Anmeldung ist schon eingerichtet."}, 400)
                if not r or not r["pending_secret"] or (r["pending_expires"] or 0) < time.time():
                    return self.send_json({"error": "Die Einrichtung ist abgelaufen. Bitte noch einmal starten."}, 400)
                step = totp_match(r["pending_secret"], data.get("code") or "")
                if not step:
                    time.sleep(0.4)
                    return self.send_json({"error": "Der Code stimmt nicht. Prüfe, ob die Uhrzeit am Handy stimmt, und gib den aktuellen Code ein."}, 400)
                codes, hashes = recovery_new()
                conn.execute("UPDATE user_2fa SET secret=pending_secret, pending_secret=NULL, pending_expires=NULL, enabled=1, last_step=?, "
                             "recovery=?, enabled_at=? WHERE user_id=?", (step, json.dumps(hashes), datetime.now().isoformat(timespec="seconds"), uid))
                conn.execute("DELETE FROM sessions WHERE user_id=? AND token_hash != ?", (uid, token_hash(self.session_token())))   # andere Geräte neu anmelden
                audit(conn, user, "Zwei-Faktor-Anmeldung eingerichtet", "users", uid, user["display_name"])
                return self.send_json({"ok": True, "codes": codes})
            if what == "recovery":
                if not on:
                    return self.send_json({"error": "Die Zwei-Faktor-Anmeldung ist nicht eingerichtet."}, 400)
                codes, hashes = recovery_new()
                conn.execute("UPDATE user_2fa SET recovery=? WHERE user_id=?", (json.dumps(hashes), uid))
                audit(conn, user, "Neue Wiederherstellungscodes", "users", uid, user["display_name"])
                return self.send_json({"ok": True, "codes": codes})
            if what == "disable":
                if not on:
                    return self.send_json({"error": "Die Zwei-Faktor-Anmeldung ist nicht eingerichtet."}, 400)
                if twofa_required(conn, user, user["perms"]):
                    return self.send_json({"error": "Für dein Konto ist die Zwei-Faktor-Anmeldung Pflicht. Ausschalten kann sie nur ein Admin (zurücksetzen)."}, 400)
                conn.execute("DELETE FROM user_2fa WHERE user_id=?", (uid,))
                audit(conn, user, "Zwei-Faktor-Anmeldung ausgeschaltet", "users", uid, user["display_name"])
                return self.send_json({"ok": True})
        return self.send_json({"error": "Unbekannter Pfad"}, 404)

    # -- Postfach (/api/postfach, Bereich postfach: Lesen 1, Antworten und Löschen 2) --
    def api_postfach(self, method, p, user, can):
        sub = p[0] if p else ""
        q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        folder = (q.get("folder") or "INBOX")[:200]
        uid = q.get("uid") or ""
        if uid and not uid.isdigit():
            return self.send_json({"error": "Ungültige Mail."}, 400)
        body = self.read_json() if method == "POST" else {}
        bsel = str(q.get("box") or (body.get("box") if isinstance(body, dict) else "") or "")[:20]   # info, acc<id> oder own (= erstes eigenes)
        own_box = bsel not in ("", "info")
        own_cfg_box, bkey = None, "info"
        if own_box:
            with db() as conn:
                mine = user_mail_boxes(conn, user["id"])
            hit = (mine[0] if mine else None) if bsel == "own" else next((x for x in mine if x[0] == bsel), None)
            if not hit:
                return self.send_json({"error": "Dieses Postfach ist deinem Konto nicht zugewiesen."}, 404)
            bkey, own_cfg_box = hit
            if sub in ("assign", "people"):
                return self.deny()
        task = False   # Person ohne Postfach-Recht, der genau diese Mail zugewiesen ist: lesen und antworten
        if own_box:
            pass
        elif not can("postfach", 1):
            if sub in ("message", "attachment") and uid:
                tf, tu = folder, uid
            elif sub == "send" and isinstance(body.get("reply"), dict):
                tf, tu = str(body["reply"].get("folder") or "INBOX")[:200], str(body["reply"].get("uid") or "")
            else:
                return self.deny()
            with db() as conn:
                task = tu.isdigit() and mail_task_access(conn, user["id"], tf, tu)
            if not task:
                return self.deny()
        write = method != "GET" and sub not in ("flag", "handled", "allread", "allhandled")   # gelesen markieren darf, wer lesen darf
        if write and not task and not own_box and not can("postfach", 2):
            return self.deny("Zum Antworten, Zuweisen und Löschen braucht es Bearbeiten im Bereich Postfach.")
        self._pf_body = body
        m = None
        try:
            if sub == "status" and method == "GET" and own_box:
                return self.send_json({"configured": True, "unseen": pf_unseen(own_cfg_box), "address": own_cfg_box["smtp_user"], "server": own_cfg_box["imap"]})
            if sub == "status" and method == "GET":
                cfg = postfach_config()
                if not (cfg["imap"] and cfg["smtp_user"] and cfg["smtp_password"]):
                    return self.send_json({"configured": False})
                return self.send_json({"configured": True, "unseen": pf_unseen(), "address": cfg["smtp_user"], "server": cfg["imap"]})
            if sub == "allhandled" and method == "POST":   # alle „neu“-Hinweise des Ordners weg, Mails bleiben liegen
                if own_box:
                    return self.send_json({"ok": True, "count": 0})
                n = pf_mark_all_handled(str(self._pf_body.get("folder") or "INBOX")[:200], user)
                with db() as conn:
                    audit(conn, user, "Mails erledigt", "postfach", None, f"{n} Mails als erledigt markiert", {})
                return self.send_json({"ok": True, "count": n})
            if sub == "handled" and method == "POST":   # „Erledigt“ ohne Antwort
                d_ = self._pf_body
                if not str(d_.get("uid") or "").isdigit():
                    return self.send_json({"error": "Ungültige Mail."}, 400)
                fold = str(d_.get("folder") or "INBOX")[:200]
                ok = True if own_box else pf_mark_handled(fold, int(d_["uid"]), user, "als erledigt markiert")
                try:
                    moved = pf_move_to_done(fold, int(d_["uid"]), own_cfg_box)
                except Exception as e:  # noqa
                    return self.send_json({"ok": ok, "moved": False, "note": f"Erledigt, aber nicht verschoben: {e}"})
                return self.send_json({"ok": ok, "moved": bool(moved), "folder": moved[0] if moved else fold, "uid": moved[1] if moved else int(d_["uid"])})
            if sub == "people":
                if not can("postfach", 2):
                    return self.deny()
                with db() as conn:
                    rows = conn.execute("SELECT id, display_name, username, mail_role, email, member_id, is_admin FROM users WHERE active=1 AND pw_hash != '' "
                                        "ORDER BY COALESCE(display_name, username) COLLATE NOCASE").fetchall()
                    out = []
                    for r in rows:
                        no_access = effective_perms(conn, {"id": r["id"], "is_admin": r["is_admin"]}).get("postfach", 0) < 1
                        out.append({"id": r["id"], "name": r["display_name"] or r["username"], "role": r["mail_role"] or "",
                                    "forward": (own_mail_address(conn, r["id"]) or account_email(conn, r)) or ("-" if no_access else "")})
                return self.send_json(out)
            m, cfg = postfach_open(own_cfg_box)
            boxw = own_box or can("postfach", 2)   # im eigenen Postfach darf das Konto alles
            if sub == "folders" and method == "GET":
                if boxw and not pf_done_folder(m, create=False):   # Ordner „Erledigt“ immer anzeigen
                    with contextlib.suppress(Exception):
                        pf_done_folder(m)
                folders = postfach_folders(m)
                for f in folders:
                    if f["role"] == "inbox":
                        typ, data = m.status(imap_quote(f["name"]), "(UNSEEN)")
                        mm = re.search(rb"UNSEEN (\d+)", data[0] if data else b"")
                        f["unseen"] = int(mm.group(1)) if mm else 0
                with db() as conn:
                    senders, dflt, _c = pf_senders(conn, user, can("postfach", 2), bkey)
                    send_as = next(x["address"] for x in senders if x["key"] == dflt)
                    sig, sig_html = signature_parts(conn, user["id"], send_as, preview=True)
                boxes = ([{"key": "info", "address": postfach_config()["smtp_user"]}] if can("postfach", 1) else []) + \
                        [x for x in senders if x["key"] != "info"]
                return self.send_json({"folders": folders, "address": cfg["smtp_user"], "box": bkey, "boxes": boxes, "quota": pf_quota(m),
                                       "can_write": boxw, "signature": sig, "signature_html": sig_html, "send_as": send_as, "senders": senders, "sender": dflt})
            if sub == "messages" and method == "GET":
                from email import policy
                from email.parser import BytesHeaderParser
                pf_select(m, folder)
                term = (q.get("q") or "").strip()[:100]
                if term:
                    m.literal = term.encode("utf-8")   # als Literal, damit auch Umlaute gesucht werden können
                    typ, data = m.uid("SEARCH", "CHARSET", "UTF-8", "TEXT")
                else:
                    typ, data = m.uid("SEARCH", None, "ALL")
                uids = (data[0] or b"").split()[::-1]
                page = max(0, int(q.get("page") or 0)) if (q.get("page") or "0").isdigit() else 0
                sel = uids[page * PF_PAGE:(page + 1) * PF_PAGE]
                items = []
                if sel:
                    typ, data = m.uid("FETCH", b",".join(sel), "(UID FLAGS RFC822.SIZE BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
                    for x in data or []:
                        if not isinstance(x, tuple):
                            continue
                        meta = x[0]
                        um = re.search(rb"UID (\d+)", meta)
                        if not um:
                            continue
                        h = BytesHeaderParser(policy=policy.compat32).parsebytes(x[1])
                        fl = re.search(rb"FLAGS \(([^)]*)\)", meta)
                        flags = (fl.group(1) if fl else b"").decode()
                        low = meta.lower()
                        items.append({"uid": int(um.group(1)), "from": pf_head(h.get("From")), "to": pf_head(h.get("To")), "subject": pf_head(h.get("Subject")) or "(ohne Betreff)",
                                      "date": h.get("Date") or "", "seen": "\\Seen" in flags, "answered": "\\Answered" in flags, "flagged": "\\Flagged" in flags,
                                      "attachment": b'"attachment"' in low or b'("name"' in low or b'"filename"' in low})
                    items.sort(key=lambda i: -i["uid"])
                    tasks, fresh = {}, {}
                    if not own_box:   # Zuweisungen und Erledigt-Vermerke gibt es nur im Vereinspostfach
                        with db() as conn:
                            for t in conn.execute("SELECT * FROM mail_tasks WHERE folder=? ORDER BY id", (folder,)):
                                tasks[t["uid"]] = mail_task_row(conn, t)
                            fresh = {r["uid"]: r["status"] for r in conn.execute("SELECT uid, status FROM pf_inbox WHERE folder=?", (folder,))}
                    for it in items:
                        it["inbox"] = fresh.get(it["uid"], "")
                        t = tasks.get(it["uid"])
                        it["task"] = {"id": t["id"], "user": t["user_name"], "status": t["status"]} if t else None
                return self.send_json({"folder": folder, "total": len(uids), "page": page, "pages": max(1, -(-len(uids) // PF_PAGE)), "items": items})
            if sub == "message" and method == "GET" and uid:
                msg, flags = pf_message(m, folder, uid, mark_seen=q.get("peek") != "1" and not task)
                with db() as conn:
                    tasks = [] if own_box else [mail_task_row(conn, t) for t in conn.execute("SELECT * FROM mail_tasks WHERE folder=? AND uid=? ORDER BY id DESC", (folder, int(uid)))]
                    senders, dflt, _c = pf_senders(conn, user, can("postfach", 2) or bool(task), bkey)
                    send_as = next(x["address"] for x in senders if x["key"] == dflt)
                    sig, sig_html = signature_parts(conn, user["id"], send_as, preview=True)
                    ib = None if own_box else conn.execute("SELECT status, action, handled_at, handled_by FROM pf_inbox WHERE folder=? AND uid=? ORDER BY id DESC", (folder, int(uid))).fetchone()
                    hb = conn.execute("SELECT display_name, username FROM users WHERE id=?", (ib["handled_by"],)).fetchone() if ib and ib["handled_by"] else None
                inbox = {"status": ib["status"], "action": ib["action"] or "", "at": ib["handled_at"] or "", "by": (hb["display_name"] or hb["username"]) if hb else ""} if ib else None
                return self.send_json({"inbox": inbox, "tasks": tasks, "signature": sig, "signature_html": sig_html, "send_as": send_as, "senders": senders, "sender": dflt,
                                       "box": bkey, "can_write": boxw or bool(task), "can_assign": can("postfach", 2) and not own_box,
                                       "uid": int(uid), "folder": folder, "from": pf_head(msg.get("From")), "to": pf_head(msg.get("To")), "cc": pf_head(msg.get("Cc")),
                                       "reply_to": pf_head(msg.get("Reply-To")), "subject": pf_head(msg.get("Subject")) or "(ohne Betreff)", "date": msg.get("Date") or "",
                                       "text": pf_text(msg), "attachments": pf_attachments(msg), "answered": b"\\Answered" in flags,
                                       "draft": b"\\Draft" in flags or folder == pf_role_folder(m, "drafts")})
            if sub == "attachment" and method == "GET" and uid and (q.get("i") or "").isdigit():
                msg, _ = pf_message(m, folder, uid, mark_seen=False)
                parts = list(msg.iter_attachments())
                i = int(q["i"])
                if i >= len(parts):
                    return self.send_json({"error": "Anhang nicht gefunden."}, 404)
                part = parts[i]
                body = part.get_payload(decode=True) or b""
                name = re.sub(r'[^\w.\- ()äöüÄÖÜß]', "_", pf_head(part.get_filename() or "") or f"anhang-{i + 1}")[:150]
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")   # nie im Hub anzeigen (kein HTML/SVG im Hub-Kontext)
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if sub == "flag" and method == "POST":
                data = self._pf_body
                tuid = str(data.get("uid") or "")
                if not tuid.isdigit():
                    return self.send_json({"error": "Ungültige Mail."}, 400)
                pf_select(m, str(data.get("folder") or "INBOX")[:200], readonly=False)
                flag = {"seen": "\\Seen", "flagged": "\\Flagged"}.get(data.get("flag") or "seen", "\\Seen")
                m.uid("STORE", tuid, "+FLAGS" if data.get("value") else "-FLAGS", f"({flag})")
                return self.send_json({"ok": True})
            if sub == "draft" and method == "POST":   # Entwurf im Ordner „Entwürfe“ des geöffneten Postfachs speichern, alter Entwurf wird ersetzt
                import imaplib
                from email.message import EmailMessage
                from email.utils import formatdate, make_msgid
                data = self._pf_body
                drafts = pf_role_folder(m, "drafts")
                if not drafts:
                    return self.send_json({"error": "Dieses Postfach hat keinen Ordner für Entwürfe."}, 400)
                one = lambda k, n: re.sub(r"[\r\n]+", " ", str(data.get(k) or "")).strip()[:n]   # keine Zeilenumbrüche in Kopfzeilen
                msg = EmailMessage()
                msg["From"] = mail_sender(own_cfg_box or postfach_config())[0]
                for head, key in (("To", "to"), ("Cc", "cc")):
                    if one(key, 2000):
                        msg[head] = one(key, 2000)
                msg["Subject"] = one("subject", 250)
                msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
                msg.set_content(str(data.get("body") or "")[:100000])
                typ, _ = m.append(imap_quote(drafts), "(\\Draft \\Seen)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
                if typ != "OK":
                    return self.send_json({"error": "Entwurf konnte nicht gespeichert werden."}, 400)
                old = data.get("replace") if isinstance(data.get("replace"), dict) else {}
                if old:
                    with contextlib.suppress(Exception):
                        pf_drop_draft(m, str(old.get("folder") or "")[:200], old.get("uid"))
                return self.send_json({"ok": True, "folder": drafts})
            if sub == "allread" and method == "POST":   # alle ungelesenen Mails des Ordners als gelesen markieren
                pf_select(m, str(self._pf_body.get("folder") or "INBOX")[:200], readonly=False)
                typ, res = m.uid("SEARCH", None, "UNSEEN")
                uids = (res[0] or b"").split() if typ == "OK" and res else []
                for i in range(0, len(uids), 500):   # in Blöcken, damit der Befehl nicht zu lang wird
                    m.uid("STORE", b",".join(uids[i:i + 500]).decode(), "+FLAGS", "(\\Seen)")
                return self.send_json({"ok": True, "count": len(uids)})
            if sub == "delete" and method == "POST":
                data = self._pf_body
                tuid, fold = str(data.get("uid") or ""), str(data.get("folder") or "INBOX")[:200]
                if not tuid.isdigit():
                    return self.send_json({"error": "Ungültige Mail."}, 400)
                msg, _ = pf_message(m, fold, tuid, mark_seen=False)
                trash = pf_role_folder(m, "trash")
                pf_select(m, fold, readonly=False)
                if trash and trash != fold:
                    typ, _ = m.uid("COPY", tuid, imap_quote(trash))
                    if typ != "OK":
                        return self.send_json({"error": "Verschieben in den Papierkorb fehlgeschlagen."}, 400)
                    action = "in den Papierkorb"
                else:
                    action = "endgültig gelöscht"
                m.uid("STORE", tuid, "+FLAGS", "(\\Deleted)")
                m.expunge()
                if not own_box:
                    pf_mark_handled(fold, int(tuid), user, "gelöscht")
                with db() as conn:
                    audit(conn, user, "Mail gelöscht", "postfach", None, pf_head(msg.get("Subject"))[:100], {"Von": pf_head(msg.get("From")), "Wohin": action})
                return self.send_json({"ok": True, "action": action})
            if sub == "send" and method == "POST":
                data = self._pf_body
                addr_re = r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+"
                def addrs(v):
                    from email.utils import getaddresses
                    out = [a for _, a in getaddresses([str(v or "")]) if a]
                    return out if all(re.fullmatch(addr_re, a) for a in out) else None
                to, cc = addrs(data.get("to")), addrs(data.get("cc"))
                subject = str(data.get("subject") or "").strip()[:250]
                body = str(data.get("body") or "")[:100000]
                q = data.get("quote") if isinstance(data.get("quote"), dict) else {}
                q_head, q_text, q_fwd = str(q.get("head") or "")[:2000], str(q.get("text") or "")[:200000], bool(q.get("forward"))
                with_sig = data.get("signature", True) is not False
                most = PF_BULK_MAX if data.get("separate") else 20
                if not to or cc is None or len(to) + len(cc) > most:
                    return self.send_json({"error": f"Bitte gültige Empfänger angeben (mehrere mit Komma, höchstens {most}{'' if data.get('separate') else ', einzeln senden bis ' + str(PF_BULK_MAX)})."}, 400)
                if not subject or not body.strip():
                    return self.send_json({"error": "Bitte Betreff und Text ausfüllen."}, 400)
                headers = {}
                reply = data.get("reply") if isinstance(data.get("reply"), dict) else None
                if reply and str(reply.get("uid") or "").isdigit():
                    orig, _ = pf_message(m, str(reply.get("folder") or "INBOX")[:200], str(reply["uid"]), mark_seen=False)
                    mid = (orig.get("Message-ID") or "").strip()
                    if mid:
                        headers = {"In-Reply-To": mid, "References": ((orig.get("References") or "") + " " + mid).strip()}
                out = {}
                with db() as conn:
                    senders, dflt, cfgs = pf_senders(conn, user, can("postfach", 2) or bool(task), bkey)
                    frm = str(data.get("from") or "")
                    if frm == "own":   # alter Schlüssel: erstes eigenes Mail-Konto
                        frm = next((x["key"] for x in senders if x["key"] != "info"), frm)
                    frm = frm if frm in [x["key"] for x in senders] else dflt
                    sender = cfgs[frm]
                    sig_mail = parseaddr(mail_sender(sender)[0])[1] or sender["smtp_user"]
                    sig_text, sig_html = signature_parts(conn, user["id"], sig_mail) if with_sig else ("", "")
                quoted = ("\n".join(q_text.split("\n")) if q_fwd else "\n".join("> " + l for l in q_text.split("\n"))) if q_text else ""
                text = body.rstrip() + (("\n\n" + sig_text) if sig_text else "") + (("\n\n\n" + q_head + "\n" + quoted) if (q_head or quoted) else "")
                para = lambda t: html_esc(t).replace("\n", "<br>\n")
                html = ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:20px;color:#1a1a1a;">' + para(body.rstrip()) + "</div><br>\n"
                        + sig_html
                        + ((f'<div style="font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#555555;margin-top:20px;">{para(q_head)}</div>'
                            f'<blockquote style="margin:6px 0 0;padding-left:12px;border-left:3px solid #dddddd;color:#555555;font-family:Arial,Helvetica,sans-serif;font-size:13px;">{para(q_text)}</blockquote>')
                           if (q_head or q_text) else ""))
                separate = bool(data.get("separate")) and len(to) > 1   # an jeden einzeln: jede Mail nennt nur den eigenen Empfänger
                if separate and cc:
                    return self.send_json({"error": "Einzeln senden geht ohne Cc. Bitte alle Adressen unter „An“ eintragen."}, 400)
                if separate and len(to) > 5:   # größere Verteiler im Hintergrund, sonst läuft die Anfrage in die Zeitgrenze
                    dr = data.get("draft") if isinstance(data.get("draft"), dict) else None
                    if dr:
                        with contextlib.suppress(Exception):
                            pf_drop_draft(m, str(dr.get("folder") or "")[:200], dr.get("uid"))
                    threading.Thread(target=pf_send_bulk, args=(sender, list(to), subject, text, html, headers, dict(user)), daemon=True).start()
                    return self.send_json({"ok": True, "background": True,
                                           "note": f"{len(to)} Mails werden jetzt einzeln verschickt (etwa {max(1, len(to) // 30)} Minute{'n' if len(to) >= 60 else ''}). Du bekommst eine Nachricht, wenn alle raus sind."})
                outs, errs = [], []
                for rcpt in ([[a] for a in to] if separate else [to]):
                    o = {}
                    e_ = send_mail(sender, ", ".join(rcpt), subject, text, footer=False, headers=headers, cc=[] if separate else cc, out=o, html=html)
                    if e_:
                        errs.append(f"{', '.join(rcpt)}: {e_}")
                    else:
                        outs.append(o)
                if not outs:
                    return self.send_json({"error": f"Versand fehlgeschlagen: {errs[0] if errs else 'unbekannt'}"}, 400)
                note = ("Nicht verschickt an " + "; ".join(errs)) if errs else ""
                try:
                    if reply and str(reply.get("uid") or "").isdigit():
                        pf_select(m, str(reply.get("folder") or "INBOX")[:200], readonly=False)
                        m.uid("STORE", str(reply["uid"]), "+FLAGS", "(\\Answered)")
                    if any(o.get("raw") for o in outs):
                        import imaplib
                        box = m if frm == bkey else postfach_open(sender)[0]   # Kopie in „Gesendet“ des Postfachs, über das verschickt wurde
                        try:
                            sent = pf_role_folder(box, "sent")
                            for o in outs if sent else []:
                                if o.get("raw"):
                                    box.append(imap_quote(sent), "(\\Seen)", imaplib.Time2Internaldate(time.time()), o["raw"])
                        finally:
                            if box is not m:
                                with contextlib.suppress(Exception):
                                    box.logout()
                except Exception as e:  # noqa
                    note = (note + " " if note else "") + f"Verschickt, aber nicht unter „Gesendet“ abgelegt ({type(e).__name__})."
                dr = data.get("draft") if isinstance(data.get("draft"), dict) else None   # aus einem Entwurf gesendet: Entwurf entfernen
                if dr:
                    with contextlib.suppress(Exception):
                        pf_drop_draft(m, str(dr.get("folder") or "")[:200], dr.get("uid"))
                src = data.get("source") if isinstance(data.get("source"), dict) else None   # Weiterleitung: Ursprungsmail
                if own_box:
                    pass
                elif reply and str(reply.get("uid") or "").isdigit():
                    pf_mark_handled(str(reply.get("folder") or "INBOX")[:200], int(reply["uid"]), user, "beantwortet")
                elif src and str(src.get("uid") or "").isdigit():
                    pf_mark_handled(str(src.get("folder") or "INBOX")[:200], int(src["uid"]), user, "weitergeleitet")
                done = 0
                with db() as conn:
                    audit(conn, user, "Mail gesendet", "postfach", None, subject[:100], {"An": ", ".join(to + cc) + (" (einzeln)" if separate else "")})
                    if reply and str(reply.get("uid") or "").isdigit() and not own_box:
                        done = conn.execute("UPDATE mail_tasks SET status='erledigt', done_at=? WHERE user_id=? AND folder=? AND uid=? AND status='offen'",
                                            (datetime.now().isoformat(timespec="seconds"), user["id"], str(reply.get("folder") or "INBOX")[:200], int(reply["uid"]))).rowcount
                return self.send_json({"ok": True, "note": note, "task_done": bool(done), "from": sender.get("smtp_user", "")})
            if sub == "assign" and method == "POST":
                data = self._pf_body
                tuid, fold = str(data.get("uid") or ""), str(data.get("folder") or "INBOX")[:200]
                target = int(data["user_id"]) if str(data.get("user_id") or "").isdigit() else 0
                if not tuid.isdigit() or not target:
                    return self.send_json({"error": "Bitte eine Person auswählen."}, 400)
                msg, _ = pf_message(m, fold, tuid, mark_seen=False)
                now = datetime.now().isoformat(timespec="seconds")
                note = str(data.get("note") or "").strip()[:1000]
                subj, sender = pf_head(msg.get("Subject"))[:250] or "(ohne Betreff)", pf_head(msg.get("From"))[:250]
                with db() as conn:
                    tu = conn.execute("SELECT id, display_name, username, email, member_id, is_admin, active FROM users WHERE id=?", (target,)).fetchone()
                    if not tu or not tu["active"]:
                        return self.send_json({"error": "Konto nicht gefunden."}, 404)
                    tid = conn.execute("INSERT INTO mail_tasks (folder, uid, message_id, subject, sender, user_id, assigned_by, note, status, created, banner_seen) "
                                       "VALUES (?,?,?,?,?,?,?,?, 'offen', ?, 0)", (fold, int(tuid), (msg.get("Message-ID") or "")[:300], subj, sender, target, user["id"], note, now)).lastrowid
                    audit(conn, user, "Mail zugewiesen", "postfach", tid, subj[:100], {"An": tu["display_name"] or tu["username"]})
                    email = account_email(conn, tu)
                    no_access = effective_perms(conn, {"id": target, "is_admin": tu["is_admin"]}).get("postfach", 0) < 1
                    fwd_to = own_mail_address(conn, target) or email   # die Mail landet immer im Postfach der Person (eigenes Hub-Postfach, sonst Konto-Adresse)
                if no_access and not fwd_to:
                    with db() as conn:
                        conn.execute("DELETE FROM mail_tasks WHERE id=?", (tid,))
                    return self.send_json({"error": "Diese Person hat keinen Zugriff auf das Vereinspostfach und keine E-Mail-Adresse im Konto. "
                                                    "Bitte unter Konten eine Adresse oder ein eigenes Postfach hinterlegen."}, 400)
                if fwd_to:   # die Mail selbst (mit Anhängen) an die Person weiterleiten, Antworten gehen an den Absender
                    err = pf_forward_to_person(cfg, msg, fwd_to, tu["display_name"] or tu["username"], user, note)
                    if err:
                        with db() as conn:
                            conn.execute("DELETE FROM mail_tasks WHERE id=?", (tid,))
                        return self.send_json({"error": f"Weiterleiten an {fwd_to} fehlgeschlagen: {err}"}, 400)
                    with db() as conn:
                        audit(conn, user, "Mail weitergeleitet", "postfach", tid, subj[:100], {"An": fwd_to})
                pf_mark_handled(fold, int(tuid), user, f"an {tu['display_name'] or tu['username']} {'weitergeleitet' if fwd_to else 'zugewiesen'}")
                name = tu["display_name"] or tu["username"]
                link = f"/hub#/mailaufgabe/{tid}"
                send_push_async([target], "Neue Mail-Aufgabe", f"{subj} (von {pf_name(sender)})", link, f"task-{tid}")
                if email and target != user["id"] and not fwd_to:
                    cfg2 = smtp_config()
                    if cfg2["smtp_host"]:
                        text = (f"Hallo {name},\n\n{user['display_name']} hat dir im RSC Frya Fresena Hub eine Mail aus dem Vereinspostfach zum Bearbeiten zugewiesen.\n\n"
                                f"Von: {sender}\nBetreff: {subj}\n" + (f"Hinweis: {note}\n" if note else "") +
                                (f"\nÖffnen: {cfg2['hub_url']}{link}\n" if cfg2["hub_url"] else "\nDu findest sie im Hub auf der Startseite.\n"))
                        threading.Thread(target=lambda: send_mail(cfg2, email, subj if re.match(r"(re|aw)\s*:", subj, re.I) else f"Re: {subj}", text, footer=False), daemon=True).start()
                return self.send_json({"ok": True, "id": tid, "forwarded": fwd_to})
            if sub == "people" and method == "GET":
                if not can("postfach", 2):
                    return self.deny()
                with db() as conn:
                    rows = conn.execute("SELECT id, display_name, username, mail_role, email, member_id, is_admin FROM users WHERE active=1 AND pw_hash != '' "
                                        "ORDER BY COALESCE(display_name, username) COLLATE NOCASE").fetchall()
                    out = []
                    for r in rows:
                        no_access = effective_perms(conn, {"id": r["id"], "is_admin": r["is_admin"]}).get("postfach", 0) < 1
                        out.append({"id": r["id"], "name": r["display_name"] or r["username"], "role": r["mail_role"] or "",
                                    "forward": (own_mail_address(conn, r["id"]) or account_email(conn, r)) or ("-" if no_access else "")})
                return self.send_json(out)
            return self.send_json({"error": "Unbekannter Pfad"}, 404)
        except PostfachError as e:
            return self.send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa
            return self.send_json({"error": f"Fehler beim Postfach: {type(e).__name__}: {e}"}, 502)
        finally:
            if m is not None:
                with contextlib.suppress(Exception):
                    m.logout()

    # -- Mail-Aufgaben (/api/mailtasks): eigene Aufgaben für alle, alle offenen für Postfach-Leser --
    def api_mailtasks(self, method, p, user, can):
        tid = int(p[0]) if p and p[0].isdigit() else None
        action = p[1] if len(p) > 1 else ""
        now = datetime.now().isoformat(timespec="seconds")
        with db() as conn:
            if method == "GET" and tid is None:
                mine = [mail_task_row(conn, t) for t in conn.execute(
                    "SELECT * FROM mail_tasks WHERE user_id=? AND (status='offen' OR done_at >= ?) ORDER BY status='erledigt', id DESC LIMIT 50",
                    (user["id"], (datetime.now() - timedelta(days=14)).isoformat()))]
                others = [mail_task_row(conn, t) for t in conn.execute("SELECT * FROM mail_tasks WHERE status='offen' AND user_id != ? ORDER BY id DESC LIMIT 100", (user["id"],))] \
                    if can("postfach", 1) else []
                inbox = [dict(r) for r in conn.execute("SELECT * FROM pf_inbox WHERE status='neu' ORDER BY id DESC LIMIT 50")] \
                    if (can("postfach", 1) and user["id"] in pf_recipients(conn)) or can("postfach", 2) else []
                return self.send_json({"mine": mine, "others": others, "inbox": inbox, "badge": badge_for(conn, user["id"])})
            t = conn.execute("SELECT * FROM mail_tasks WHERE id=?", (tid,)).fetchone() if tid else None
            if not t:
                return self.send_json({"error": "Aufgabe nicht gefunden."}, 404)
            own = t["user_id"] == user["id"]
            if method == "GET":
                if not (own or can("postfach", 1)):
                    return self.deny()
                return self.send_json(mail_task_row(conn, t))
            if method == "POST" and action == "seen" and own:
                conn.execute("UPDATE mail_tasks SET banner_seen=1 WHERE id=?", (tid,))
                return self.send_json({"ok": True})
            if method == "POST" and action in ("done", "reopen") and (own or can("postfach", 2)):
                conn.execute("UPDATE mail_tasks SET status=?, done_at=?, banner_seen=1 WHERE id=?",
                             ("erledigt" if action == "done" else "offen", now if action == "done" else None, tid))
                audit(conn, user, "Mail-Aufgabe erledigt" if action == "done" else "Mail-Aufgabe wieder offen", "postfach", tid, t["subject"][:100])
                conn.commit()
                if action == "done":
                    pf_mark_handled(t["folder"], t["uid"], user, "erledigt")
                    try:
                        pf_move_to_done(t["folder"], t["uid"])
                    except Exception as e:  # noqa
                        return self.send_json({"ok": True, "note": f"Erledigt, aber nicht verschoben: {e}"})
                return self.send_json({"ok": True})
            if method == "DELETE" and can("postfach", 2):
                conn.execute("DELETE FROM mail_tasks WHERE id=?", (tid,))
                audit(conn, user, "Mail-Zuweisung entfernt", "postfach", tid, t["subject"][:100])
                return self.send_json({"ok": True})
        return self.deny()

    # -- Push-Benachrichtigungen (/api/push): Schlüssel, Abo je Gerät, Test --
    def api_push(self, method, p, user):
        sub = p[0] if p else ""
        with db() as conn:
            if method == "GET" and sub == "key":
                _, pub = vapid_keys(conn)
                n = conn.execute("SELECT COUNT(*) FROM push_subs WHERE user_id=?", (user["id"],)).fetchone()[0]
                return self.send_json({"key": b64u(pub), "devices": n})
            if method == "POST" and sub == "subscribe":
                data = self.read_json()
                ep, keys = str(data.get("endpoint") or ""), data.get("keys") or {}
                if not push_endpoint_ok(ep):
                    return self.send_json({"error": "Dieser Push-Dienst wird nicht unterstützt."}, 400)
                try:
                    ec_pub_point(b64u_dec(keys.get("p256dh")))
                    if len(b64u_dec(keys.get("auth"))) != 16:
                        raise ValueError
                except (ValueError, TypeError):
                    return self.send_json({"error": "Ungültiges Push-Abo."}, 400)
                conn.execute("DELETE FROM push_subs WHERE endpoint=?", (ep,))
                conn.execute("INSERT INTO push_subs (user_id, endpoint, p256dh, auth, ua, created) VALUES (?,?,?,?,?,?)",
                             (user["id"], ep, keys["p256dh"], keys["auth"], (self.headers.get("User-Agent") or "")[:200], datetime.now().isoformat(timespec="seconds")))
                return self.send_json({"ok": True})
            if method == "POST" and sub == "unsubscribe":
                ep = str(self.read_json().get("endpoint") or "")
                conn.execute("DELETE FROM push_subs WHERE endpoint=? AND user_id=?", (ep, user["id"]))
                return self.send_json({"ok": True})
        if method == "POST" and sub == "test":
            n = send_push([user["id"]], "RSC Frya Fresena Hub", "Push-Benachrichtigungen funktionieren.", "/hub#/profile", "test")
            return self.send_json({"ok": n > 0, "delivered": n})
        return self.send_json({"error": "Unbekannter Pfad"}, 404)

    PROFILE_FIELDS = ("email", "phone", "street", "zip", "city", "emergency_contact", "consent_photo")   # darf jede Person im eigenen Mitgliedseintrag ändern

    def api_profile(self, method, user):
        with db() as conn:
            if method == "PUT":
                data = self.read_json()
                if "display_name" in data:
                    conn.execute("UPDATE users SET display_name=? WHERE id=?", ((data["display_name"] or "").strip()[:80] or user["username"], user["id"]))
                if "account_email" in data or "notify_email" in data:
                    # E-Mail des Kontos: für Benachrichtigungen und „Passwort vergessen“
                    email = (data.get("account_email", data.get("notify_email")) or "").strip()
                    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
                        return self.send_json({"error": "Bitte eine gültige E-Mail-Adresse angeben."}, 400)
                    conn.execute("UPDATE users SET email=? WHERE id=?", (email, user["id"]))
                if "sig_phone" in data:   # Telefon in der eigenen Mail-Signatur: nur Ziffern, Leerzeichen, + / ( ) -
                    ph = re.sub(r"\s+", " ", str(data["sig_phone"] or "")).strip()[:40]
                    if ph and not re.fullmatch(r"\+?[\d ()/-]{5,40}", ph):
                        return self.send_json({"error": "Bitte nur eine Telefonnummer eintragen (Ziffern, Leerzeichen, +, /)."}, 400)
                    conn.execute("UPDATE users SET sig_phone=? WHERE id=?", (ph, user["id"]))
                if "notify" in data:
                    kinds = [k for k in (data["notify"] or []) if k in NOTIFY_KINDS and notify_allowed(user["perms"], k)]
                    conn.execute("UPDATE users SET notify=? WHERE id=?", (json.dumps(kinds), user["id"]))
                if user["member_id"]:
                    vals = clean_values("members", {k: v for k, v in data.items() if k in self.PROFILE_FIELDS})
                    if vals:
                        vals["updated_at"] = datetime.now().isoformat(timespec="seconds")
                        conn.execute(f"UPDATE members SET {', '.join(f'{k}=?' for k in vals)} WHERE id=?", [*vals.values(), user["member_id"]])
            u = row_dict(conn.execute("SELECT username, display_name, email, notify, sig_phone FROM users WHERE id=?", (user["id"],)).fetchone())
            if u["notify"] is None:   # nie gespeichert: Standard anzeigen (gilt auch beim Versand)
                u["notify"] = json.dumps([k for k in NOTIFY_DEFAULT if notify_allowed(user["perms"], k)])
            m = conn.execute("SELECT * FROM members WHERE id=?", (user["member_id"],)).fetchone() if user["member_id"] else None
            kinds = [{"key": k, "label": v[0]} for k, v in NOTIFY_KINDS.items() if notify_allowed(user["perms"], k)]
            mail_ready = bool(secret_get(conn, "smtp_host"))
            return self.send_json({"account": u, "member": row_dict(m) if m else None,
                                   "notify_kinds": kinds, "mail_ready": mail_ready})

    # -- Konten, Gruppen, Rechte ------------------------------------------
    def user_payload(self, conn, uid):
        u = conn.execute("SELECT id, username, display_name, email, is_admin, active, member_id, created_at, last_login, "
                         "invite_expires, pw_hash != '' AS has_pw, locked_until, phone, invite_profile, mail_role FROM users WHERE id=?", (uid,)).fetchone()
        d = row_dict(u)
        d["is_admin"] = bool(d["is_admin"])
        d["locked"] = (d.pop("locked_until") or 0) > time.time()
        d["twofa"] = twofa_enabled(conn, uid)
        d["groups"] = [r["group_id"] for r in conn.execute("SELECT group_id FROM user_groups WHERE user_id=?", (uid,))]
        d["overrides"] = {r["area"]: r["level"] for r in conn.execute("SELECT area, level FROM user_perms WHERE user_id=?", (uid,))}
        d["effective"] = effective_perms(conn, {"id": uid, "is_admin": d["is_admin"]})
        d["twofa_required"] = twofa_required(conn, {"id": uid, "is_admin": d["is_admin"]}, d["effective"])
        d["invite_open"] = bool(d.pop("invite_expires") or 0) and not d["has_pw"]
        d.pop("invite_profile", None)
        d["own_mail"] = ", ".join((a["address"] or a["username"]) for a in conn.execute(
            "SELECT m.address, m.username FROM mail_accounts m JOIN mail_links a ON a.account_id=m.id WHERE a.user_id=? ORDER BY lower(m.address)", (uid,)))
        return d

    def send_invite_mail(self, data, link):
        """Einladungslink per Mail schicken. Liefert None oder eine Fehlermeldung."""
        to = (data.get("email") or "").strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to):
            return "Keine gültige E-Mail-Adresse angegeben, bitte den Link selbst weitergeben."
        cfg = smtp_config()
        if not (cfg["smtp_host"] and cfg["smtp_user"]):
            return "Der E-Mail-Versand ist nicht eingerichtet, bitte den Link selbst weitergeben."
        name = (data.get("display_name") or "").strip()
        name = "" if name in ("Einladung offen",) else name
        try:
            return send_mail(cfg, to, "Einladung zum RSC Frya Fresena Hub", invite_mail_text(name, link, INVITE_DAYS), footer=False)
        except Exception as e:  # noqa
            return f"Versand fehlgeschlagen: {e}"

    def save_user_links(self, conn, uid, data):
        if "mail_role" in data:
            conn.execute("UPDATE users SET mail_role=? WHERE id=?", (str(data["mail_role"] or "").strip()[:80], uid))
        if "groups" in data:
            conn.execute("DELETE FROM user_groups WHERE user_id=?", (uid,))
            for gid in data["groups"] or []:
                conn.execute("INSERT OR IGNORE INTO user_groups (user_id, group_id) VALUES (?, ?)", (uid, int(gid)))
        if "overrides" in data:
            conn.execute("DELETE FROM user_perms WHERE user_id=?", (uid,))
            for area, lvl in (data["overrides"] or {}).items():
                if area in AREAS and lvl not in (None, "") and int(lvl) in (0, 1, 2):
                    conn.execute("INSERT INTO user_perms (user_id, area, level) VALUES (?, ?, ?)", (uid, area, int(lvl)))
        if "member_id" in data:
            mid = data["member_id"] or None
            conn.execute("UPDATE members SET user_id=NULL WHERE user_id=?", (uid,))
            conn.execute("UPDATE users SET member_id=? WHERE id=?", (mid, uid))
            if mid:
                conn.execute("UPDATE users SET member_id=NULL WHERE member_id=? AND id != ?", (mid, uid))
                conn.execute("UPDATE members SET user_id=? WHERE id=?", (uid, mid))

    def api_mail_accounts(self, method, rid, sub, me):
        with db() as conn:
            r = conn.execute("SELECT * FROM mail_accounts WHERE id=?", (rid,)).fetchone() if rid else None
            if rid and not r:
                return self.send_json({"error": "Mail-Konto nicht gefunden"}, 404)
            if method == "GET" and not rid:
                out = []
                for a in conn.execute("SELECT * FROM mail_accounts ORDER BY lower(address)"):
                    d = {k: a[k] or "" for k in ("address", "smtp_host", "smtp_port", "smtp_security", "imap_host", "username", "updated")}
                    d.update(id=a["id"], password_set=bool(a["password"]), users=[
                        {"id": u["id"], "name": u["display_name"] or u["username"], "no_shared": u["level"] == 0}
                        for u in conn.execute("SELECT u.id, u.display_name, u.username, p.level FROM mail_links x JOIN users u ON u.id=x.user_id "
                                              "LEFT JOIN user_perms p ON p.user_id=u.id AND p.area='postfach' WHERE x.account_id=? "
                                              "ORDER BY lower(coalesce(u.display_name, u.username))", (a["id"],))])
                    out.append(d)
                users = [{"id": u["id"], "name": u["display_name"] or u["username"]}
                         for u in conn.execute("SELECT id, display_name, username FROM users ORDER BY lower(coalesce(display_name, username))")]
                return self.send_json({"accounts": out, "users": users})
            if method == "DELETE" and rid and not sub:
                conn.execute("DELETE FROM mail_links WHERE account_id=?", (rid,))
                conn.execute("DELETE FROM mail_accounts WHERE id=?", (rid,))
                audit(conn, me, "Mail-Konto gelöscht", "mail_accounts", rid, r["address"] or r["username"])
                return self.send_json({"ok": True})
            if method == "POST" and rid and sub == "check":   # Anmeldung an SMTP und IMAP prüfen, es wird keine Mail verschickt
                cfg = user_mail_cfg(conn, None, row=r)
                if not cfg:
                    return self.send_json({"error": "Mailserver, Benutzername und Passwort fehlen noch."}, 400)
            elif not sub and ((method == "POST" and not rid) or (method == "PUT" and rid)):
                data = self.read_json()
                if rid and data and set(data) <= {"add_user", "remove_user", "user_id", "no_shared"}:   # Personen zuweisen/lösen, Schalter je Person
                    uid = data.get("add_user") or data.get("remove_user") or data.get("user_id")
                    uid = int(uid) if str(uid or "").isdigit() else None
                    if not uid or not conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                        return self.send_json({"error": "Konto nicht gefunden"}, 404)
                    linked = conn.execute("SELECT 1 FROM mail_links WHERE account_id=? AND user_id=?", (rid, uid)).fetchone()
                    if "add_user" in data:
                        conn.execute("INSERT OR IGNORE INTO mail_links (account_id, user_id) VALUES (?, ?)", (rid, uid))
                        audit(conn, me, "Mail-Konto zugewiesen", "mail_accounts", rid, f"{r['address']} -> {uid}")
                    elif "remove_user" in data:
                        conn.execute("DELETE FROM mail_links WHERE account_id=? AND user_id=?", (rid, uid))
                        audit(conn, me, "Mail-Konto Zuweisung gelöst", "mail_accounts", rid, f"{r['address']} -> {uid}")
                    elif "no_shared" in data:
                        if not linked:
                            return self.send_json({"error": "Die Person ist diesem Mail-Konto nicht zugewiesen."}, 400)
                        if data["no_shared"]:
                            conn.execute("INSERT OR REPLACE INTO user_perms (user_id, area, level) VALUES (?, 'postfach', 0)", (uid,))
                        else:
                            conn.execute("DELETE FROM user_perms WHERE user_id=? AND area='postfach' AND level=0", (uid,))
                        audit(conn, me, "Zugriff Vereinspostfach " + ("gesperrt" if data["no_shared"] else "freigegeben"), "users", uid, "")
                    return self.send_json({"ok": True})
                vals = {k: str(data.get(k) or "").strip()[:200] for k in ("address", "smtp_host", "smtp_port", "smtp_security", "imap_host", "username")}
                if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", vals["address"]):
                    return self.send_json({"error": "Bitte eine gültige Adresse angeben."}, 400)
                vals["username"] = vals["username"] or vals["address"]
                if not re.fullmatch(r"[A-Za-z0-9.-]{3,200}", vals["smtp_host"]):
                    return self.send_json({"error": "Bitte den Mailserver (SMTP) angeben."}, 400)
                if vals["imap_host"] and not re.fullmatch(r"[A-Za-z0-9.-]{3,200}", vals["imap_host"]):
                    return self.send_json({"error": "Der IMAP-Server sieht nicht gültig aus."}, 400)
                if vals["smtp_port"] and not vals["smtp_port"].isdigit():
                    return self.send_json({"error": "Der Port muss eine Zahl sein."}, 400)
                if vals["smtp_security"] not in ("starttls", "ssl", "none"):
                    vals["smtp_security"] = "starttls"
                pw = str(data.get("password") or "")
                if not pw and not (r and r["password"]):
                    return self.send_json({"error": "Bitte das Passwort des Postfachs eintragen."}, 400)
                vals["password"] = pw or r["password"]
                vals["updated"] = datetime.now().isoformat(timespec="seconds")
                if rid:
                    conn.execute(f"UPDATE mail_accounts SET {', '.join(k + '=?' for k in vals)} WHERE id=?", [*vals.values(), rid])
                else:
                    rid = conn.execute(f"INSERT INTO mail_accounts ({', '.join(vals)}) VALUES ({', '.join('?' for _ in vals)})", list(vals.values())).lastrowid
                audit(conn, me, "Mail-Konto gespeichert", "mail_accounts", rid, vals["address"])
                return self.send_json({"ok": True, "id": rid})
            else:
                return self.send_json({"error": "Methode nicht erlaubt"}, 405)
        return self.send_json(mail_account_check(cfg))

    def api_admin(self, method, p, me):
        what = p[0] if p else ""
        rid = int(p[1]) if len(p) > 1 and p[1].isdigit() else None
        sub = p[2] if len(p) > 2 else ""

        if what == "info" and method == "GET":   # Seite „Admin“
            fresh = (parse_qs(urlparse(self.path).query).get("neu") or [""])[0] == "1"
            return self.send_json(admin_info(fresh))
        if what == "updates":   # Admin > Updates: Status lesen, feste Aktionen beim Helfer anstoßen
            if method == "GET":
                return self.send_json(updater_state())
            if method != "POST":
                return self.send_json({"error": "Nicht erlaubt"}, 405)
            data = self.read_json()
            action = str(data.get("action") or "")
            if action not in UPD_ACTIONS:
                return self.send_json({"error": "Unbekannte Aktion."}, 400)
            with db() as conn:
                if not twofa_enabled(conn, me["id"]):
                    return self.send_json({"error": "Updates nur mit eingeschalteter Zwei-Faktor-Anmeldung (Mein Profil)."}, 403)
                if action != "check":
                    row = conn.execute("SELECT pw_hash FROM users WHERE id=?", (me["id"],)).fetchone()
                    if not row or not check_pw(str(data.get("password") or ""), row["pw_hash"]):
                        return self.send_json({"error": "Das Passwort stimmt nicht."}, 403)
                if not UPD_DIR.is_dir():
                    return self.send_json({"error": "Der Update-Helfer ist auf dem Server noch nicht eingerichtet (Anleitung auf dieser Seite)."}, 400)
                st = updater_state()
                if st["pending"] or st["running"]:
                    return self.send_json({"error": "Es läuft schon eine Aktion. Bitte warten, bis sie fertig ist."}, 409)
                tmp = UPD_DIR / f".request-{uuid.uuid4().hex}"
                tmp.write_text(action + "\n", encoding="ascii")
                os.replace(tmp, UPD_DIR / "request")
                audit(conn, me, "Update angestoßen", "admin", None, UPD_ACTIONS[action], {})
            return self.send_json(updater_state())
        if what == "log" and method == "GET":
            q = parse_qs(urlparse(self.path).query)
            limit = min(1000, max(50, int((q.get("limit") or ["300"])[0] or 300)))
            with db() as conn:
                rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return self.send_json([row_dict(r) for r in rows])
        if what == "2fa-policy":   # Pflicht zur Zwei-Faktor-Anmeldung
            with db() as conn:
                if method == "PUT":
                    data = self.read_json()
                    new = {k: "1" if data.get(k) else "" for k in TWOFA_POLICY_KEYS if k in data}
                    if "1" in new.values() and not twofa_enabled(conn, me["id"]):   # Admins haben auch Finanz-Schreibrecht
                        return self.send_json({"error": "Richte zuerst für dein eigenes Konto die Zwei-Faktor-Anmeldung ein (Mein Profil)."}, 400)
                    old = {k: get_setting(conn, k) or "" for k in new}
                    for k, v in new.items():
                        set_setting(conn, k, v)
                    changes = {k: [old[k], v] for k, v in new.items() if old[k] != v}
                    if changes:
                        audit(conn, me, "geändert", "settings", None, "Pflicht Zwei-Faktor-Anmeldung", changes)
                elif method != "GET":
                    return self.send_json({"error": "Unbekannter Pfad"}, 404)
                return self.send_json({k: get_setting(conn, k) == "1" for k in TWOFA_POLICY_KEYS})
        if what == "mail":   # Admin > E-Mails: alle Mail-Konten, Zuweisung an Personen (Passwort nur schreiben)
            return self.api_mail_accounts(method, rid, sub, me)
        if what == "users" and rid and sub in ("2fa-reset", "unlock") and method == "POST":
            with db() as conn:
                u = conn.execute("SELECT * FROM users WHERE id=?", (rid,)).fetchone()
                if not u:
                    return self.send_json({"error": "Konto nicht gefunden"}, 404)
                label = u["display_name"] or u["username"]
                if sub == "unlock":
                    account_ok(conn, rid)
                    audit(conn, me, "Anmeldesperre aufgehoben", "users", rid, label)
                    return self.send_json(self.user_payload(conn, rid))
                if not twofa_row(conn, rid):
                    return self.send_json({"error": "Für dieses Konto ist keine Zwei-Faktor-Anmeldung eingerichtet."}, 400)
                conn.execute("DELETE FROM user_2fa WHERE user_id=?", (rid,))
                audit(conn, me, "Zwei-Faktor-Anmeldung zurückgesetzt", "users", rid, label)
                email = account_email(conn, u)
                payload = self.user_payload(conn, rid)
            cfg = smtp_config()
            if email and cfg["smtp_host"] and rid != me["id"]:
                body = (f"Hallo {label},\n\nein Admin ({me['display_name']}) hat die Zwei-Faktor-Anmeldung deines Kontos im RSC Frya Fresena Hub "
                        f"(Benutzername: {u['username']}) zurückgesetzt ({datetime.now():%d.%m.%Y %H:%M}). Du meldest dich jetzt nur mit dem Passwort an "
                        "und kannst sie unter „Mein Profil“ neu einrichten.\n\nWusstest du nichts davon? Dann melde dich bitte beim Vorstand bzw. Admin.")
                threading.Thread(target=lambda: send_mail(cfg, email, "RSC Frya Fresena Hub: Zwei-Faktor-Anmeldung zurückgesetzt", body, footer=False), daemon=True).start()
            return self.send_json(payload)
        if method != "GET" and what in ("users", "groups"):
            name = None
            with db() as conn:
                if rid and what == "users":
                    u = conn.execute("SELECT username, display_name FROM users WHERE id=?", (rid,)).fetchone()
                    name = u and (u["display_name"] or u["username"])
                elif rid:
                    g = conn.execute("SELECT name FROM groups WHERE id=?", (rid,)).fetchone()
                    name = g and g["name"]
                act = {"POST": "neu", "PUT": "geändert", "DELETE": "gelöscht"}.get(method, method)
                audit(conn, me, f"{act}{' (' + sub + ')' if sub else ''}", what, rid, name or "")

        if what == "users":
            if method == "GET":
                with db() as conn:
                    ids = [r["id"] for r in conn.execute("SELECT id FROM users ORDER BY COALESCE(display_name, username) COLLATE NOCASE")]
                    return self.send_json([self.user_payload(conn, i) for i in ids])
            if method == "POST" and rid is None:
                data = self.read_json()
                pick = not (data.get("username") or "").strip()   # ohne Namen wählt die Person ihn selbst
                username = data.get("username") or f"einladung-{secrets.token_hex(3)}"
                try:
                    uid, tok = create_user(username, None, bool(data.get("is_admin")), data.get("display_name") or "",
                                           data.get("email") or "", data.get("member_id") or None)
                except ValueError as e:
                    return self.send_json({"error": str(e)}, 400)
                link = f"{self.base_url()}/invite/{tok}"
                with db() as conn:
                    self.save_user_links(conn, uid, {k: v for k, v in data.items() if k in ("groups", "overrides", "mail_role")})
                    if pick:
                        conn.execute("UPDATE users SET invite_profile=? WHERE id=?", (json.dumps({"pick_name": pick}), uid))
                    if pick and not (data.get("display_name") or "").strip():
                        conn.execute("UPDATE users SET display_name='Einladung offen' WHERE id=?", (uid,))
                    payload = self.user_payload(conn, uid)
                mail_error = self.send_invite_mail(data, link) if data.get("send_mail") else None
                return self.send_json({"user": payload, "invite": link, "days": INVITE_DAYS, "mailed": bool(data.get("send_mail")) and not mail_error,
                                       "mail_error": mail_error}, 201)
            if method == "POST" and rid and sub == "invite":
                data = self.read_json() if int(self.headers.get("Content-Length") or 0) else {}
                with db() as conn:
                    u = conn.execute("SELECT display_name, email FROM users WHERE id=?", (rid,)).fetchone()
                    if not u:
                        return self.send_json({"error": "Konto nicht gefunden"}, 404)
                    tok = new_invite(conn, rid)
                link = f"{self.base_url()}/invite/{tok}"
                mail_error = self.send_invite_mail({"email": u["email"], "display_name": u["display_name"]}, link) if data.get("send_mail") else None
                return self.send_json({"invite": link, "days": INVITE_DAYS, "mailed": bool(data.get("send_mail")) and not mail_error, "mail_error": mail_error})
            if method == "PUT" and rid:
                data = self.read_json()
                with db() as conn:
                    u = conn.execute("SELECT * FROM users WHERE id=?", (rid,)).fetchone()
                    if not u:
                        return self.send_json({"error": "Konto nicht gefunden"}, 404)
                    is_admin = bool(data.get("is_admin", u["is_admin"]))
                    active = bool(data.get("active", u["active"]))
                    if rid == me["id"] and (not is_admin or not active):
                        return self.send_json({"error": "Du kannst dir die Admin-Rechte nicht selbst entziehen oder dich deaktivieren."}, 400)
                    if "username" in data and data["username"] != u["username"]:
                        try:
                            name = check_username(data["username"])
                        except ValueError as e:
                            return self.send_json({"error": str(e)}, 400)
                        if conn.execute("SELECT 1 FROM users WHERE username=? AND id != ?", (name, rid)).fetchone():
                            return self.send_json({"error": "Diesen Benutzernamen gibt es schon."}, 400)
                        conn.execute("UPDATE users SET username=? WHERE id=?", (name, rid))
                    conn.execute("UPDATE users SET display_name=?, email=?, is_admin=?, active=? WHERE id=?",
                                 ((data.get("display_name") or u["display_name"] or u["username"]).strip()[:80],
                                  (data.get("email") if "email" in data else u["email"]) or "", 1 if is_admin else 0, 1 if active else 0, rid))
                    if not active:
                        conn.execute("DELETE FROM sessions WHERE user_id=?", (rid,))
                    self.save_user_links(conn, rid, data)
                    return self.send_json(self.user_payload(conn, rid))
            if method == "DELETE" and rid:
                if rid == me["id"]:
                    return self.send_json({"error": "Du kannst dich nicht selbst löschen."}, 400)
                with db() as conn:
                    for t in ("sessions", "user_groups", "user_perms", "user_2fa", "push_subs", "mail_links"):
                        conn.execute(f"DELETE FROM {t} WHERE user_id=?", (rid,))
                    conn.execute("DELETE FROM mail_links WHERE user_id=?", (rid,))   # Mail-Konten bleiben, nur ohne diese Person
                    conn.execute("UPDATE members SET user_id=NULL WHERE user_id=?", (rid,))
                    conn.execute("DELETE FROM users WHERE id=?", (rid,))
                return self.send_json({"ok": True})

        if what == "groups":
            with db() as conn:
                if method == "GET":
                    out = []
                    for g in conn.execute("SELECT * FROM groups ORDER BY sort, name"):
                        d = row_dict(g)
                        d["perms"] = {a: 0 for a in AREAS}
                        for r in conn.execute("SELECT area, level FROM group_perms WHERE group_id=?", (g["id"],)):
                            d["perms"][r["area"]] = r["level"]
                        d["members"] = conn.execute("SELECT COUNT(*) FROM user_groups WHERE group_id=?", (g["id"],)).fetchone()[0]
                        out.append(d)
                    return self.send_json(out)
                if method in ("POST", "PUT"):
                    data = self.read_json()
                    name = (data.get("name") or "").strip()[:40]
                    if not name:
                        return self.send_json({"error": "Bitte einen Namen angeben."}, 400)
                    if conn.execute("SELECT 1 FROM groups WHERE name=? AND id != ?", (name, rid or 0)).fetchone():
                        return self.send_json({"error": "Diese Gruppe gibt es schon."}, 400)
                    if method == "POST":
                        sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM groups").fetchone()[0]
                        rid = conn.execute("INSERT INTO groups (name, description, sort) VALUES (?, ?, ?)",
                                           (name, data.get("description") or "", sort)).lastrowid
                    else:
                        conn.execute("UPDATE groups SET name=?, description=? WHERE id=?", (name, data.get("description") or "", rid))
                    for area in AREAS:
                        lvl = int((data.get("perms") or {}).get(area, 0) or 0)
                        conn.execute("INSERT OR REPLACE INTO group_perms (group_id, area, level) VALUES (?, ?, ?)", (rid, area, max(0, min(2, lvl))))
                    return self.send_json({"ok": True, "id": rid})
                if method == "DELETE" and rid:
                    conn.execute("DELETE FROM user_groups WHERE group_id=?", (rid,))
                    conn.execute("DELETE FROM group_perms WHERE group_id=?", (rid,))
                    conn.execute("DELETE FROM groups WHERE id=?", (rid,))
                    return self.send_json({"ok": True})
        return self.send_json({"error": "Unbekannter Pfad"}, 404)

    def api_table(self, method, table, rid, perms=None):
        now = datetime.now().isoformat(timespec="seconds")
        hide = ()
        if perms is not None:
            if perms.get("finanzen", 0) < 1:
                hide += FINANCE_FIELDS.get(table, ())
            if perms.get(TABLE_AREA.get(table), 0) < 1:
                hide += AREA_FIELDS.get(table, ())
            if perms.get(TABLE_AREA.get(table), 0) < 2:
                hide += WRITE_FIELDS.get(table, ())
                if table in WRITE_ONLY_TABLES:
                    return self.send_json({"error": "Dafür fehlen dir die Rechte."}, 403)

        def out(r):
            d = row_dict(r)
            for f in hide:
                d.pop(f, None)
            return d

        with db() as conn:
            if method == "GET":
                if rid is None:
                    rows = conn.execute(f'SELECT * FROM "{table}" ORDER BY id').fetchall()
                    return self.send_json([out(r) for r in rows])
                r = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (rid,)).fetchone()
                return self.send_json(out(r) if r else None, 200 if r else 404)

            if method == "POST":
                vals = clean_values(table, self.read_json())
                if any(has_bad_link(v) for v in vals.values()):
                    return self.send_json({"error": "Links müssen mit https:// beginnen oder auf eine Datei im Hub zeigen."}, 400)
                for f in hide + SERVER_FIELDS.get(table, ()):
                    vals.pop(f, None)
                if table == "arena_bookings":
                    clash = arena_conflict(conn, vals)
                    if clash:
                        return self.send_json({"error": clash}, 409)
                    vals["user_id"] = (getattr(self, "user", None) or {}).get("id")
                vals["created_at"] = now
                vals["updated_at"] = now
                cols = ", ".join(f'"{k}"' for k in vals)
                qs = ", ".join("?" for _ in vals)
                cur = conn.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({qs})', list(vals.values()))
                r = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (cur.lastrowid,)).fetchone()
                audit(conn, getattr(self, "user", None), "neu", table, cur.lastrowid, audit_label(r),
                      {k: audit_short(v) for k, v in vals.items() if k not in AUDIT_SKIP and v not in (None, "")})
                return self.send_json(out(r), 201)

            if method == "PUT" and rid is not None:
                vals = clean_values(table, self.read_json())
                if any(has_bad_link(v) for v in vals.values()):
                    return self.send_json({"error": "Links müssen mit https:// beginnen oder auf eine Datei im Hub zeigen."}, 400)
                for f in hide + SERVER_FIELDS.get(table, ()):
                    vals.pop(f, None)
                vals["updated_at"] = now
                old = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (rid,)).fetchone()
                if table == "fee_payments" and old and old["booked_tx"] and any(k in vals and str(vals[k] or "") != str(old[k] or "") for k in ("paid", "amount")):
                    return self.send_json({"error": "Dieser Beitrag ist schon in den Finanzen gebucht, Betrag und „bezahlt“ lassen sich nicht mehr ändern."}, 400)
                if table == "arena_bookings" and old:
                    clash = arena_conflict(conn, {**row_dict(old), **vals}, rid)
                    if clash:
                        return self.send_json({"error": clash}, 409)
                sets = ", ".join(f'"{k}"=?' for k in vals)
                conn.execute(f'UPDATE "{table}" SET {sets} WHERE id=?', list(vals.values()) + [rid])
                r = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (rid,)).fetchone()
                if old:
                    changes = {k: [audit_short(old[k]), audit_short(v)] for k, v in vals.items()
                               if k not in AUDIT_SKIP and str(old[k] if old[k] is not None else "") != str(v if v is not None else "")}
                    if changes:
                        audit(conn, getattr(self, "user", None), "geändert", table, rid, audit_label(r), changes)
                return self.send_json(out(r) if r else None)

            if method == "DELETE" and rid is not None:
                old = conn.execute(f'SELECT * FROM "{table}" WHERE id=?', (rid,)).fetchone()
                if table == "fee_payments" and old and old["booked_tx"]:
                    return self.send_json({"error": "Dieser Beitrag ist schon in den Finanzen gebucht und kann nicht gelöscht werden."}, 400)
                if old:
                    audit(conn, getattr(self, "user", None), "gelöscht", table, rid, audit_label(old),
                          {k: audit_short(v) for k, v in row_dict(old).items() if k not in AUDIT_SKIP and v not in (None, "")})
                conn.execute(f'DELETE FROM "{table}" WHERE id=?', (rid,))
                # abhängige Einträge mitnehmen (sonst bleiben verwaiste Zeilen liegen)
                for child, col in {"horses": (("horse_health", "horse_id"),),
                                   "lesson_groups": (("lesson_participants", "group_id"), ("lesson_attendance", "group_id")),
                                   "lesson_participants": (("lesson_attendance", "participant_id"),),
                                   "events": (("event_entries", "event_id"), ("event_helpers", "event_id"))}.get(table, ()):
                    conn.execute(f'DELETE FROM "{child}" WHERE "{col}"=?', (rid,))
                if table == "members":
                    conn.execute("UPDATE users SET member_id=NULL WHERE member_id=?", (rid,))
                if table == "horses":
                    conn.execute("UPDATE boxes SET horse_id=NULL WHERE horse_id=?", (rid,))
                return self.send_json({"ok": True})

        return self.send_json({"error": "Methode nicht erlaubt"}, 405)

    # -- Webseite im Hub (Bereich Medien) ---------------------------------
    # -- Wartungsmodus der Webseite (/api/maintenance): Lesen mit Wartung 1, Schalten und Texte mit Wartung 2 --
    def api_maintenance(self, method, p, user, can):
        if not can("wartung", 1):
            return self.deny()
        if p == ["image"]:   # Hintergrundbild der Wartungsseite, gleiche Prüfung wie die Bilder der Webseite
            if not can("wartung", 2):
                return self.deny("Zum Ändern des Wartungsmodus braucht es Schreibrecht im Bereich Wartung.")
            return self.site_image(method, website.MAINT_SLOT, user, lambda: self.api_maintenance("GET", [], user, can))
        if p:
            return self.send_json({"error": "Nicht gefunden"}, 404)
        if method == "PUT":
            if not can("wartung", 2):
                return self.deny("Zum Ändern des Wartungsmodus braucht es Schreibrecht im Bereich Wartung.")
            data = self.read_json()
            new = {}
            if "on" in data:
                new["maint_on"] = "1" if data["on"] else ""
            if "title" in data:
                new["maint_title"] = str(data["title"] or "").strip()[:120]
            if "text" in data:
                new["maint_text"] = str(data["text"] or "").strip()[:2000]
            if "until" in data:
                u = str(data["until"] or "").strip()[:16]
                new["maint_until"] = u if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?", u) else ""
            with db() as conn:
                old = {k: get_setting(conn, k) for k in new}
                changes = {k: [audit_short(old[k]), audit_short(v)] for k, v in new.items() if (old[k] or "") != v}
                for k, v in new.items():
                    set_setting(conn, k, v)
                if "maint_on" in changes:
                    audit(conn, user, "eingeschaltet" if new["maint_on"] else "ausgeschaltet", "settings", None, "Wartungsmodus", changes)
                elif changes:
                    audit(conn, user, "geändert", "settings", None, "Wartungsmodus", changes)
            return self.api_maintenance("GET", [], user, can)
        if method != "GET":
            return self.send_json({"error": "Methode nicht erlaubt"}, 405)
        site = public_site()
        own = site["images"].get(website.MAINT_SLOT)
        with db() as conn:
            st = {k: get_setting(conn, k) for k in ("maint_on", "maint_title", "maint_text", "maint_until")}
            alt = (web_images(conn).get(website.MAINT_SLOT) or {}).get("alt", "")
            last = conn.execute("SELECT time, user_name, action FROM audit_log WHERE label='Wartungsmodus' AND action IN ('eingeschaltet','ausgeschaltet') "
                                "ORDER BY id DESC LIMIT 1").fetchone()
        src = (own or {}).get("src") or {}
        return self.send_json({"on": st["maint_on"] == "1", "title": st["maint_title"], "text": st["maint_text"], "until": st["maint_until"],
                               "image": {"own": bool(own), "alt": alt, "preview": src.get(640) or (src[min(src)] if src else "/web/assets/img/hero-640.webp")},
                               "last": dict(last) if last else None, "can_edit": can("wartung", 2)})

    def site_image(self, method, slot, user, done):
        """Eigenes Bild der Webseite hochladen (WebP vom Browser), Alt-Text ändern oder zum Standardbild zurück."""
        with db() as conn:
            imgs = web_images(conn)
            cur = imgs.get(slot) or {}
            if method == "DELETE":   # zurück zum Standardbild
                for f in (cur.get("files") or {}).values():
                    (SITE_DIR / str(f)).unlink(missing_ok=True)
                imgs.pop(slot, None)
                action = "Standardbild"
            elif method == "PUT":    # nur Alt-Text
                imgs[slot] = {**cur, "alt": str(self.read_json().get("alt") or "")[:300]}
                action = "Alt-Text"
            elif method == "POST":
                data = self.read_json()
                files, stamp = {}, uuid.uuid4().hex[:10]
                SITE_DIR.mkdir(parents=True, exist_ok=True)
                for w, url in (data.get("files") or {}).items():
                    m = re.match(r"^data:image/webp;base64,(.+)$", str(url), re.S)
                    if not (str(w).isdigit() and int(w) in SITE_WIDTHS and m):
                        continue
                    raw = base64.b64decode(m.group(1))
                    if len(raw) > SITE_IMAGE_MAX or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
                        for f in files.values():
                            (SITE_DIR / f).unlink(missing_ok=True)
                        return self.send_json({"error": "Das Bild ist ungültig oder zu groß."}, 400)
                    name = f"{slot}-{w}-{stamp}.webp"
                    (SITE_DIR / name).write_bytes(raw)
                    files[str(w)] = name
                if not files:
                    return self.send_json({"error": "Kein Bild empfangen."}, 400)
                for f in (cur.get("files") or {}).values():
                    (SITE_DIR / str(f)).unlink(missing_ok=True)
                imgs[slot] = {"alt": str(data.get("alt") or "")[:300], "w": int(data.get("w") or 0), "h": int(data.get("h") or 0), "files": files}
                action = "neues Bild"
            else:
                return self.send_json({"error": "Methode nicht erlaubt"}, 405)
            set_setting(conn, "web_images", json.dumps(imgs))
            audit(conn, user, "geändert", "settings", None, f"Webseite Bild {website.IMAGE_SLOTS[slot][0]}", {"Änderung": ["", action]})
        return done()

    def api_newsletter(self, method, p, user, can):
        """Hub > Newsletter (Bereich Medien: Lesen 1, Ändern und Senden 2): Abonnenten, Ausgaben; Gruppen über die generische Tabelle nl_groups."""
        if not can("medien", 1):
            return self.deny()
        if method != "GET" and not can("medien", 2):
            return self.deny("Für den Newsletter braucht es Bearbeiten im Bereich Medien.")
        what = p[0] if p else ""
        rid = int(p[1]) if len(p) > 1 and p[1].isdigit() else None
        act = p[2] if len(p) > 2 else ""
        now = datetime.now().isoformat(timespec="seconds")
        email_ok = lambda v: re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v or "")
        if method == "GET" and not p:
            with db() as conn:
                subs = conn.execute("SELECT * FROM nl_subscribers ORDER BY created DESC, id DESC").fetchall()
                issues = conn.execute("SELECT * FROM nl_issues ORDER BY id DESC").fetchall()
                groups = [row_dict(r) for r in conn.execute("SELECT * FROM nl_groups ORDER BY IFNULL(sort, 0), name")]
            return self.send_json({
                "subscribers": [{"id": r["id"], "email": r["email"], "name": r["name"] or "", "status": r["status"], "groups": nl_ids(r["groups"]),
                                 "consent_at": r["consent_at"] or "", "source": r["source"] or "", "created": r["created"] or "", "unsubscribed_at": r["unsubscribed_at"] or ""} for r in subs],
                "issues": [{"id": r["id"], "subject": r["subject"] or "", "body": r["body"] or "", "groups": nl_ids(r["groups"]), "status": r["status"],
                            "created": r["created"] or "", "sent_at": r["sent_at"] or "", "sent_count": r["sent_count"] or 0, "failed": r["failed"] or 0} for r in issues],
                "groups": groups, "link_ok": bool(SITE_URL)})
        data = self.read_json() if method in ("POST", "PUT") else {}
        with db() as conn:
            all_groups = {r["id"] for r in conn.execute("SELECT id FROM nl_groups")}
            gl = lambda v: sorted({int(x) for x in (v if isinstance(v, list) else []) if str(x).isdigit() and int(x) in all_groups})
            if what == "subscribers":
                if method == "POST" and rid is None:   # von Hand eintragen, nur mit bestätigter Einwilligung
                    email = str(data.get("email") or "").strip()[:150]
                    if not email_ok(email):
                        return self.send_json({"error": "Bitte eine gültige E-Mail-Adresse eintragen."}, 400)
                    if not data.get("consent"):
                        return self.send_json({"error": "Bitte bestätigen, dass die Person in den Newsletter eingewilligt hat."}, 400)
                    note = re.sub(r"\s+", " ", str(data.get("note") or "")).strip()[:300]
                    who = user.get("display_name") or user.get("username")
                    ctext = f"Im Hub eingetragen von {who}" + (f": {note}" if note else "")
                    row = conn.execute("SELECT id, groups, status FROM nl_subscribers WHERE email=?", (email,)).fetchone()
                    g = gl(data.get("groups"))
                    if row:
                        g = sorted(set(g) | (set(nl_ids(row["groups"])) if row["status"] == "aktiv" else set()))
                        conn.execute("UPDATE nl_subscribers SET status='aktiv', groups=?, name=COALESCE(NULLIF(?, ''), name), consent_at=?, consent_text=?, unsubscribed_at=NULL WHERE id=?",
                                     (json.dumps(g), str(data.get("name") or "")[:80], now, ctext, row["id"]))
                        sid = row["id"]
                    else:
                        sid = conn.execute("INSERT INTO nl_subscribers (email, name, status, groups, consent_at, consent_text, source, created) VALUES (?, ?, 'aktiv', ?, ?, ?, 'Hub', ?)",
                                           (email, str(data.get("name") or "")[:80], json.dumps(g), now, ctext, now)).lastrowid
                    audit(conn, user, "Newsletter eingetragen", "nl_subscribers", sid, email)
                    return self.send_json({"ok": True, "id": sid})
                r = conn.execute("SELECT * FROM nl_subscribers WHERE id=?", (rid,)).fetchone() if rid else None
                if not r:
                    return self.send_json({"error": "Abonnent nicht gefunden."}, 404)
                if method == "PUT":
                    conn.execute("UPDATE nl_subscribers SET groups=?, name=? WHERE id=?", (json.dumps(gl(data.get("groups"))), str(data.get("name") or "")[:80], rid))
                    audit(conn, user, "geändert", "nl_subscribers", rid, r["email"])
                    return self.send_json({"ok": True})
                if method == "POST" and act == "unsubscribe":
                    conn.execute("UPDATE nl_subscribers SET status='abgemeldet', unsubscribed_at=?, confirm_hash=NULL WHERE id=?", (now, rid))
                    audit(conn, user, "Newsletter abgemeldet", "nl_subscribers", rid, r["email"])
                    return self.send_json({"ok": True})
                if method == "DELETE":   # Daten auf Wunsch ganz löschen
                    conn.execute("DELETE FROM nl_subscribers WHERE id=?", (rid,))
                    audit(conn, user, "gelöscht", "nl_subscribers", rid, r["email"])
                    return self.send_json({"ok": True})
            if what == "issues":
                subject, body = str(data.get("subject") or "").strip()[:200], str(data.get("body") or "").replace("\r", "")[:50000]
                if method == "POST" and rid is None:
                    if not subject:
                        return self.send_json({"error": "Bitte einen Betreff eintragen."}, 400)
                    iid = conn.execute("INSERT INTO nl_issues (subject, body, groups, status, created, created_by) VALUES (?, ?, ?, 'entwurf', ?, ?)",
                                       (subject, body, json.dumps(gl(data.get("groups"))), now, user["id"])).lastrowid
                    return self.send_json({"ok": True, "id": iid})
                it = conn.execute("SELECT * FROM nl_issues WHERE id=?", (rid,)).fetchone() if rid else None
                if not it:
                    return self.send_json({"error": "Newsletter nicht gefunden."}, 404)
                draft = it["status"] == "entwurf"
                if method == "PUT":
                    if not draft:
                        return self.send_json({"error": "Verschickte Newsletter lassen sich nicht mehr ändern."}, 400)
                    if not subject:
                        return self.send_json({"error": "Bitte einen Betreff eintragen."}, 400)
                    conn.execute("UPDATE nl_issues SET subject=?, body=?, groups=? WHERE id=?", (subject, body, json.dumps(gl(data.get("groups"))), rid))
                    return self.send_json({"ok": True, "id": rid})
                if method == "DELETE":
                    if not draft:
                        return self.send_json({"error": "Verschickte Newsletter bleiben als Nachweis gespeichert."}, 400)
                    conn.execute("DELETE FROM nl_issues WHERE id=?", (rid,))
                    return self.send_json({"ok": True})
                if method == "POST" and act in ("test", "send"):
                    if not (it["subject"] or "").strip() or not (it["body"] or "").strip():
                        return self.send_json({"error": "Bitte Betreff und Text eintragen und speichern."}, 400)
                    cfg = smtp_config()
                    if not cfg["smtp_host"]:
                        return self.send_json({"error": "E-Mail-Versand ist nicht eingerichtet (Einstellungen)."}, 400)
                    base = self.base_url()
                    if act == "test":
                        to = account_email(conn, conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone())
                        if not to:
                            return self.send_json({"error": "In deinem Profil fehlt eine E-Mail-Adresse für den Test."}, 400)
                        text, html = nl_mail(get_setting(conn, "club_name") or "RSC Frya Fresena", letter_text(conn, "footer"), it["subject"], it["body"], base + "/newsletter")
                        err = send_mail(cfg, to, "[Test] " + it["subject"], text, footer=False, html=html)
                        return self.send_json({"error": f"Test fehlgeschlagen: {err}"}, 400) if err else self.send_json({"ok": True, "to": to})
                    if not draft:
                        return self.send_json({"error": "Dieser Newsletter wurde schon verschickt."}, 400)
                    n = len(nl_recipients(conn, nl_ids(it["groups"])))
                    if not n:
                        return self.send_json({"error": "In den gewählten Gruppen gibt es keine aktiven Abonnenten."}, 400)
                    conn.execute("UPDATE nl_issues SET status='läuft' WHERE id=?", (rid,))
                    audit(conn, user, "Newsletter gestartet", "nl_issues", rid, it["subject"][:100], {"Empfänger": ["", str(n)]})
                    threading.Thread(target=nl_send_issue, args=(rid, dict(user), base), daemon=True).start()
                    return self.send_json({"ok": True, "count": n})
        return self.send_json({"error": "Nicht gefunden"}, 404)


    def api_website(self, method, p, user, can):
        if not can("medien", 1):
            return self.deny()
        if method != "GET" and not can("medien", 2):
            return self.deny("Zum Bearbeiten der Webseite braucht es Schreibrecht im Bereich Medien.")
        text_keys = [k for k in DEFAULT_SETTINGS if k.startswith("site_")]
        if not p and method == "PUT":
            data = self.read_json()
            texts = {k: str(v)[:40000 if k in ("site_impressum", "site_datenschutz") else 5000] for k, v in (data.get("texts") or {}).items() if k in text_keys}
            with db() as conn:
                old = {k: get_setting(conn, k) for k in texts}
                changes = {k: [audit_short(old[k]), audit_short(v)] for k, v in texts.items() if (old[k] or "") != v}
                for k, v in texts.items():
                    set_setting(conn, k, v)
                if isinstance(data.get("layout"), list):
                    set_setting(conn, "web_layout", json.dumps([[str(x[0]), bool(x[1])] for x in data["layout"]
                                                                if isinstance(x, list) and len(x) == 2 and x[0] in website.START_SECTIONS]))
                    changes["Aufbau Startseite"] = ["", " > ".join(k for k, v in web_layout(conn) if v)]
                if isinstance(data.get("nav"), list):   # Menü der Webseite: nur bekannte Einträge
                    known = dict(NAV_BUILTIN)
                    clean = [{"key": it["key"], "show": bool(it.get("show")), "label": str(it.get("label") or "").strip()[:40], "button": bool(it.get("button"))}
                             for it in data["nav"] if isinstance(it, dict) and it.get("key") in known]
                    set_setting(conn, "web_nav", json.dumps(clean, ensure_ascii=False))
                    changes["Menü"] = ["", " · ".join((it["label"] or known[it["key"]]) for it in clean if it["show"])]
                if data.get("links_reset"):   # Link-Seite: zurück zur Standardliste
                    set_setting(conn, "web_links", "")
                    changes["Link-Seite"] = ["", "Standard"]
                elif isinstance(data.get("links"), list):
                    bad = [str(it.get("label") or it.get("url") or "?")[:40] for it in data["links"] if isinstance(it, dict)
                           and (str(it.get("label") or "").strip() or str(it.get("url") or "").strip()) and not (str(it.get("label") or "").strip() and link_url_ok(it.get("url")))]
                    if bad:
                        return self.send_json({"error": "Bitte bei jedem Knopf einen Namen und eine Adresse mit https:// oder eine eigene Seite mit / eintragen: " + ", ".join(bad)}, 400)
                    clean = [{"label": str(it["label"]).strip()[:60], "url": link_url_ok(it["url"]), "hot": bool(it.get("hot"))}
                             for it in data["links"] if isinstance(it, dict) and str(it.get("label") or "").strip()][:LINKS_MAX]
                    set_setting(conn, "web_links", json.dumps(clean, ensure_ascii=False))
                    changes["Link-Seite"] = ["", " · ".join(it["label"] for it in clean)]
                if isinstance(data.get("sitemap"), dict):
                    sm = web_sitemap(conn, data["sitemap"])   # nur bekannte Seiten und gültige Prioritäten
                    set_setting(conn, "web_sitemap", json.dumps(sm))
                    changes["Sitemap"] = ["", ", ".join(f"{k or 'start'} {p}" for k, (on, p) in sm.items() if on)]
                if changes:
                    audit(conn, user, "geändert", "settings", None, "Webseite", changes)
            return self.api_website("GET", [], user, can)
        if p[:1] == ["image"] and len(p) == 2 and p[1] in website.IMAGE_SLOTS and p[1] != website.MAINT_SLOT:
            return self.site_image(method, p[1], user, lambda: self.api_website("GET", [], user, can))
        if p or method != "GET":
            return self.send_json({"error": "Nicht gefunden"}, 404)
        return self.send_json(website_overview(text_keys))

    def api_settings(self, method, finance=True):
        with db() as conn:
            if method == "PUT":
                data = self.read_json()
                old = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
                changes = {k: [audit_short(old.get(k)), audit_short(v)] for k, v in data.items()
                           if k not in TWOFA_POLICY_KEYS
                           and str(old.get(k) or "") != str(v or "")}
                if changes:
                    audit(conn, getattr(self, "user", None), "geändert", "settings", None, "Einstellungen", changes)
                for k, v in data.items():
                    if k in TWOFA_POLICY_KEYS or k.startswith("sheet_"):   # 2FA-Pflicht nur über /api/admin/2fa-policy, Google-Tabelle gibt es nicht mehr
                        continue
                    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return self.send_json({r["key"]: r["value"] for r in rows if finance or not r["key"].startswith("inv_")})   # Bank/Rechnung nur mit Finanzrecht

    def api_upload(self, library=True):
        data = self.read_json()
        raw = data.get("data", "")
        name = data.get("name") or "bild.png"
        m = re.match(r"^data:([\w/+.-]+);base64,(.*)$", raw, re.S)
        if not m:
            return self.send_json({"error": "Ungültige Datei"}, 400)
        mime, b64 = m.groups()
        suffix = Path(name).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
            suffix = ""
        # Unbekannte Typen (z. B. .pages, .indd) behalten ihre Endung statt .bin
        ext = (mimetypes.guess_extension(mime) if mime != "application/octet-stream" else None) or suffix or ".bin"
        if ext == ".jpe":
            ext = ".jpg"
        safe = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(name).stem)[:40].strip("-") or "datei"
        # Zufallsteil lang genug, dass niemand fremde Dateinamen (Belege, Verträge) erraten kann; /media braucht ohnehin eine Anmeldung
        fname = f"{datetime.now():%Y%m%d-%H%M%S}-{safe}-{uuid.uuid4().hex[:20]}{ext}"
        (MEDIA / fname).write_bytes(base64.b64decode(b64))
        path = f"/media/{fname}"
        if data.get("library", True) and library:
            with db() as conn:
                conn.execute(
                    "INSERT INTO media (path, name, tags, created, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (path, name, data.get("tags", ""), datetime.now().isoformat(timespec="seconds"),
                     datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
                )
        return self.send_json({"path": path})

    def api_backup(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            # konsistente Kopie der Datenbank
            tmp = db_snapshot(DATA / "_backup_tmp.db")
            # Zugangsdaten gehören nicht in ein heruntergeladenes Backup
            c = sqlite3.connect(tmp)
            try:
                for t in ("sessions", "users", "user_groups", "user_perms", "secrets", "audit_log", "user_2fa", "push_subs", "mail_tasks", "pf_inbox", "mail_accounts", "mail_links"):
                    c.execute(f"DELETE FROM {t}")
                c.commit()
                c.execute("VACUUM")
            finally:
                c.close()
            z.write(tmp, "data/rsc.db")
            tmp.unlink(missing_ok=True)
            for f in MEDIA.iterdir():
                if f.is_file():
                    z.write(f, f"data/media/{f.name}")
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="RSCFryaFresenaHub-Backup-{datetime.now():%Y-%m-%d}.zip"')
        self.end_headers()
        self.wfile.write(data)

    def api_export(self):
        out = {}
        with db() as conn:
            for t in TABLES:
                out[t] = [row_dict(r) for r in conn.execute(f'SELECT * FROM "{t}"')]
            out["settings"] = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings")}
        return self.send_json(out)

    def api_dashboard(self, perms):
        """Übersicht: nur was die Person mit ihren Rechten sehen darf."""
        today = datetime.now().strftime("%Y-%m-%d")
        soon = (date.today() + timedelta(days=14)).isoformat()
        lv = lambda *areas: max(perms.get(a, 0) for a in areas)
        res = {"today": today, "counts": {}}
        with db() as conn:
            q = lambda sql, a=(): [row_dict(r) for r in conn.execute(sql, a)]
            res["next_events"] = q("SELECT id, title, kind, date, end_date, time, place, entry_deadline FROM events WHERE IFNULL(end_date, date) >= ? "
                                   "ORDER BY date, time LIMIT 6", (today,))
            if lv("unterricht", "stall", "veranstaltungen") >= 1:
                rows = conn.execute("SELECT * FROM arena_bookings WHERE date <= ? AND IFNULL(NULLIF(repeat_until,''), date) >= ?", (today, today)).fetchall()
                res["arena_today"] = [{"place": r["place"], "start": r["start"], "end": r["end"], "kind": r["kind"], "title": r["title"]}
                                      for _, r in arena_expand(rows, today, today)]
                res["lessons_today"] = q("SELECT id, name, kind, time, place, instructor FROM lesson_groups WHERE active=1 AND weekday=? ORDER BY time",
                                         (str(date.today().isoweekday()),))
            if lv("stall") >= 1:
                res["health_due"] = q(
                    "SELECT h.id, h.kind, h.next_due, h.details, p.id AS horse_id, p.name FROM horse_health h JOIN horses p ON p.id=h.horse_id "
                    "WHERE p.active=1 AND IFNULL(h.next_due,'') != '' AND h.next_due <= ? AND h.id = (SELECT x.id FROM horse_health x "
                    "WHERE x.horse_id=h.horse_id AND x.kind=h.kind ORDER BY x.date DESC, x.id DESC LIMIT 1) ORDER BY h.next_due", (soon,))
                res["counts"]["horses"] = conn.execute("SELECT COUNT(*) FROM horses WHERE active=1").fetchone()[0]
                res["counts"]["school_horses"] = conn.execute("SELECT COUNT(*) FROM horses WHERE active=1 AND kind='Schulpferd'").fetchone()[0]
                res["counts"]["boxes_free"] = conn.execute("SELECT COUNT(*) FROM boxes WHERE active=1 AND horse_id IS NULL").fetchone()[0]
            if lv("mitglieder") >= 1:
                res["counts"]["members"] = conn.execute("SELECT COUNT(*) FROM members WHERE status IN ('Aktiv', 'Probezeit')").fetchone()[0]
                res["counts"]["waiting"] = conn.execute("SELECT COUNT(*) FROM members WHERE status='Warteliste'").fetchone()[0]
            if lv("mitglieder", "finanzen") >= 1:
                r = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM fee_payments WHERE IFNULL(paid,0)=0").fetchone()
                res["fees_open"] = {"count": r[0], "sum": r[1],
                                    "overdue": conn.execute("SELECT COUNT(*) FROM fee_payments WHERE IFNULL(paid,0)=0 AND IFNULL(due,'') != '' AND due < ?", (today,)).fetchone()[0]}
            if lv(*TABLE_AREAS_ANY["contact_requests"]) >= 1:
                res["counts"]["contacts"] = conn.execute("SELECT COUNT(*) FROM contact_requests WHERE IFNULL(status,'Neu')='Neu'").fetchone()[0]
            if lv("unterricht") >= 1:
                res["counts"]["lessons"] = conn.execute("SELECT COUNT(*) FROM lesson_groups WHERE active=1").fetchone()[0]
            if perms.get("finanzen", 0) >= 1:
                y = today[:4]
                inc = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE kind='Einnahme' AND substr(date, 1, 4)=?", (y,)).fetchone()[0]
                exp = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM transactions WHERE kind='Ausgabe' AND substr(date, 1, 4)=?", (y,)).fetchone()[0]
                res["finance"] = {"year": y, "income": inc, "expense": exp,
                                  "open": conn.execute("SELECT COUNT(*) FROM transactions WHERE status='Offen'").fetchone()[0]}
        return self.send_json(res)


# ---------------------------------------------------------------------------
# Seiten für Anmeldung und Ersteinrichtung
# ---------------------------------------------------------------------------
AUTH_PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>RSC Frya Fresena Hub</title><link rel="icon" href="/static/icon.png"><link rel="stylesheet" href="/static/style.css">
<link rel="manifest" href="/manifest.webmanifest"><link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">
<meta name="theme-color" content="#173F3A"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="RSC Frya Fresena Hub"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
body{display:flex;align-items:center;justify-content:center;min-height:100vh;background:#173F3A;padding:16px}
.auth{width:min(380px,100%);background:#fff;border-radius:14px;padding:30px 28px;box-shadow:0 20px 60px rgba(0,0,0,.4)}
.auth-logo{border-bottom:2px solid #B8955A;display:flex;align-items:center;gap:12px;padding:0 0 14px;margin:-6px -4px 22px}
.auth-logo img{height:64px}.auth-logo b{color:#173F3A;font-family:var(--head);letter-spacing:.3px}
.auth-logo span{display:block;color:#8a6d3b;font-size:11px;letter-spacing:2px;text-transform:uppercase}
.auth .field{margin-bottom:12px}.auth .btn{width:100%;justify-content:center;padding:10px;margin-top:6px}
.err{color:#b00020;font-size:13px;min-height:18px;margin-top:8px}
</style></head><body>
<form class="auth" id="f" method="post" action="/auth/none">
  <div class="auth-logo"><img src="/static/logo.png" alt=""><div><b>RSC Frya Fresena</b><span>Hub</span></div></div>
  %%BODY%%
  <div class="err" id="err"></div>
</form>
<script>
let action = '%%ACTION%%';
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = Object.fromEntries(new FormData(document.getElementById('f')));
  if (fd.password2 !== undefined && fd.password !== fd.password2) { document.getElementById('err').textContent = 'Die Passwörter stimmen nicht überein.'; return false; }
  const r = await fetch(action, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-TMH': '1' }, body: JSON.stringify(fd) });
  const d = await r.json().catch(() => ({}));
  if (r.ok && d.need_2fa && document.getElementById('s2')) {   // Anmeldung: Passwort stimmt, jetzt der Code aus der App
    action = '/auth/login2';
    document.querySelectorAll('#s1 input').forEach(i => { i.disabled = true; });
    document.getElementById('s1').hidden = true;
    document.getElementById('s2').hidden = false;
    document.querySelectorAll('#s2 input').forEach(i => { i.disabled = false; });
    document.getElementById('tk').value = d.ticket;
    document.getElementById('c').focus();
    document.getElementById('err').textContent = '';
    return;
  }
  if (r.ok) location.href = d.next || '%%NEXT%%';
  else if (d.restart) { document.getElementById('err').textContent = d.error; setTimeout(() => location.reload(), 2500); }
  else document.getElementById('err').textContent = d.error || 'Fehler';
});
</script></body></html>"""

LOGIN_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/auth/login").replace("%%NEXT%%", "/hub").replace("%%BODY%%", """
  <div id="s1">
  <div class="field"><label for="u">Benutzername</label><input class="input" id="u" name="username" autocomplete="username" required autofocus></div>
  <div class="field"><label for="p">Passwort</label><input class="input" id="p" name="password" type="password" autocomplete="current-password" required></div>
  <button class="btn primary">Anmelden</button>
  <p class="small" style="text-align:center;margin:14px 0 0"><a href="/passwort-vergessen">Passwort vergessen?</a></p>
  </div>
  <div id="s2" hidden>
  <p style="margin-top:0"><b>Zwei-Faktor-Anmeldung</b></p>
  <p class="muted small">Gib den 6-stelligen Code aus deiner Authenticator-App ein. Handy nicht zur Hand? Dann einen deiner Wiederherstellungscodes.</p>
  <input type="hidden" id="tk" name="ticket" disabled>
  <div class="field"><label for="c">Code</label><input class="input" id="c" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="20" required disabled></div>
  <button class="btn primary">Bestätigen</button>
  <p class="small" style="text-align:center;margin:14px 0 0"><a href="/login">Abbrechen</a></p>
  </div>"""))

FORGOT_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/auth/forgot").replace("%%NEXT%%", "/passwort-vergessen/gesendet").replace("%%BODY%%", """
  <p style="margin-top:0"><b>Passwort vergessen?</b></p>
  <p class="muted small">Gib deinen Benutzernamen oder deine E-Mail-Adresse ein. Du bekommst dann einen Link, mit dem du ein neues Passwort festlegst.</p>
  <div class="field"><label for="l">Benutzername oder E-Mail</label><input class="input" id="l" name="login" autocomplete="username" required autofocus></div>
  <button class="btn primary">Link schicken</button>
  <p class="small" style="text-align:center;margin:14px 0 0"><a href="/login">Zurück zur Anmeldung</a></p>"""))

FORGOT_SENT_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/").replace("%%NEXT%%", "/").replace("%%BODY%%", """
  <p style="margin-top:0"><b>Schau in dein Postfach</b></p>
  <p class="muted">Wenn es ein Konto mit diesen Angaben und einer hinterlegten E-Mail-Adresse gibt, haben wir gerade einen Link geschickt.
    Er gilt 60 Minuten. Schau auch im Spam-Ordner nach.</p>
  <p class="muted small">Keine Mail bekommen? Dann ist bei deinem Konto vermutlich keine E-Mail-Adresse hinterlegt. Bitte wende dich an den Vorstand bzw. Admin.</p>
  <p class="small" style="text-align:center;margin:14px 0 0"><a href="/login">Zurück zur Anmeldung</a></p>"""))

RESET_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/auth/reset").replace("%%NEXT%%", "/hub").replace("%%BODY%%", """
  <p style="margin-top:0">Hallo <b>%%NAME%%</b>, leg jetzt dein neues Passwort fest.</p>
  <p class="muted small">Dein Benutzername: <b>%%USER%%</b></p>
  <input type="hidden" name="token" value="%%TOKEN%%">
  <input type="text" name="username" value="%%USER%%" autocomplete="username" hidden>
  <div class="field"><label for="p">Neues Passwort (mind. 10 Zeichen)</label><input class="input" id="p" name="password" type="password" autocomplete="new-password" required autofocus></div>
  <div class="field"><label for="p2">Passwort wiederholen</label><input class="input" id="p2" name="password2" type="password" autocomplete="new-password" required></div>
  <button class="btn primary">Passwort speichern und anmelden</button>"""))

INVALID_RESET_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/").replace("%%NEXT%%", "/").replace("%%BODY%%", """
  <p>Dieser Link ist ungültig oder abgelaufen.</p>
  <p class="muted">Ein Link gilt 60 Minuten und nur einmal. <a href="/passwort-vergessen">Neuen Link anfordern</a></p>"""))

SETUP_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/auth/setup").replace("%%NEXT%%", "/login").replace("%%BODY%%", """
  <p class="muted" style="margin-top:0">Ersteinrichtung: Lege deinen Admin-Zugang an.</p>
  <div class="field"><label for="u">Benutzername</label><input class="input" id="u" name="username" autocomplete="username" required autofocus></div>
  <div class="field"><label for="p">Passwort (mind. 10 Zeichen)</label><input class="input" id="p" name="password" type="password" autocomplete="new-password" required></div>
  <div class="field"><label for="p2">Passwort wiederholen</label><input class="input" id="p2" name="password2" type="password" autocomplete="new-password" required></div>
  <button class="btn primary">Zugang anlegen</button>"""))




def invite_profile(u):
    try:
        prof = json.loads(u["invite_profile"] or "{}")
    except (ValueError, TypeError, IndexError, KeyError):
        prof = {}
    return prof if isinstance(prof, dict) else {}


def invite_page(u, tok):
    """Einladungsseite: Benutzername, Passwort und persönliche Daten."""
    prof = invite_profile(u)
    e = html_esc
    dn = (u["display_name"] or "").strip()
    first, _, last = dn.partition(" ") if dn and dn not in (u["username"], "Einladung offen") else ("", "", "")
    def field(name, label, value="", typ="text", req=False, extra="", hint=""):
        return (f'<div class="field"><label for="i_{name}">{label}{" *" if req else ""}</label>'
                f'<input class="input" id="i_{name}" name="{name}" type="{typ}" value="{e(value)}" {"required" if req else ""} {extra}>'
                f'{f"<div class=hint>{hint}</div>" if hint else ""}</div>')
    body = f"""
  <style>body{{align-items:flex-start;padding-top:24px}}.auth{{width:min(560px,100%)}}.row2{{display:grid;grid-template-columns:1fr 1fr;gap:0 12px}}
  .inv-h{{font-size:15px;margin:18px 0 8px;padding-top:12px;border-top:1px solid #eee}}.inv-consent{{display:flex;gap:8px;align-items:flex-start;font-size:13px;margin:6px 0 10px}}
  .hint{{font-size:12px;color:#777;margin-top:3px}}@media(max-width:480px){{.row2{{grid-template-columns:1fr}}}}</style>
  <p style="margin-top:0">Willkommen im <b>RSC Frya Fresena Hub</b>! Bitte trag einmal deine Angaben ein und leg dein Passwort fest.</p>
  <input type="hidden" name="token" value="{e(tok)}">
  <h3 class="inv-h" style="border:0;padding:0;margin-top:6px">Zugang</h3>
  {field("username", "Benutzername", "" if prof.get("pick_name") else u["username"], req=True, extra='autocomplete="username" maxlength="40" pattern="[A-Za-z0-9._@-]{2,40}"', hint="2 bis 40 Zeichen: Buchstaben, Zahlen und . _ - @ (ohne Leerzeichen). Damit meldest du dich an.")}
  <div class="row2">{field("password", "Passwort (mind. 10 Zeichen)", typ="password", req=True, extra='autocomplete="new-password" minlength="10"')}
    {field("password2", "Passwort wiederholen", typ="password", req=True, extra='autocomplete="new-password"')}</div>
  <h3 class="inv-h">Über dich</h3>
  <div class="row2">{field("first_name", "Vorname", first, req=True, extra='maxlength="60" autocomplete="given-name"')}{field("last_name", "Nachname", last, req=True, extra='maxlength="60" autocomplete="family-name"')}</div>
  <div class="row2">{field("email", "E-Mail", u["email"] or "", "email", req=True, extra='maxlength="150" autocomplete="email"', hint="Für „Passwort vergessen“ und Benachrichtigungen.")}
    {field("phone", "Telefon", "", "tel", extra='maxlength="40" autocomplete="tel"', hint="Nur intern für die Vereinsorganisation.")}</div>
  <p class="muted small">Deine Angaben sieht nur der Verein im geschützten Bereich. Mehr dazu in der <a href="/datenschutz" target="_blank">Datenschutzerklärung</a>.</p>
  <button class="btn primary">Speichern und loslegen</button>"""
    return AUTH_PAGE.replace("%%ACTION%%", "/auth/invite").replace("%%NEXT%%", "/hub#/profile").replace("%%BODY%%", body)


def invite_mail_text(name, link, days):
    return (f"Hallo{(' ' + name) if name else ''},\n\ndu bist zum RSC Frya Fresena Hub eingeladen, dem internen Bereich des RSC Frya Fresena.\n\n"
            f"Über diesen Link legst du deinen Zugang an und trägst einmal deine Angaben ein:\n{link}\n\n"
            f"Der Link gilt {days} Tage und nur für dich. Bitte nicht weiterleiten.\n\nViele Grüße\nRSC Frya Fresena")


INVALID_INVITE_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/").replace("%%NEXT%%", "/").replace("%%BODY%%", """
  <p>Dieser Einladungslink ist ungültig oder abgelaufen.</p>
  <p class="muted">Bitte frag beim Vorstand bzw. Admin nach einem neuen Link. Wenn du schon ein Passwort hast: <a href="/login">zur Anmeldung</a></p>"""))

ORDER_INVALID_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/").replace("%%NEXT%%", "/").replace("%%BODY%%", """
  <p>Dieser Bestell-Link ist ungültig.</p>
  <p class="muted">Bitte frag beim Verein nach dem aktuellen Link.</p>"""))


def html_esc(t):
    return (str(t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


NO_USER_PAGE = (AUTH_PAGE.replace("%%ACTION%%", "/").replace("%%NEXT%%", "/").replace("%%BODY%%", """
  <p>Es ist noch kein Benutzer angelegt.</p>
  <p class="muted">Bitte in der Datei <b>.env</b> auf dem Server <b>TMH_ADMIN_USER</b> und <b>TMH_ADMIN_PASSWORD</b>
  eintragen und den Hub neu starten (<code>docker compose up -d</code>).</p>"""))


def cleanup_loop():
    while True:
        try:
            with db() as conn:
                conn.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
                conn.execute("DELETE FROM audit_log WHERE time < ?",
                             ((datetime.now() - timedelta(days=AUDIT_KEEP_DAYS)).isoformat(timespec="seconds"),))
            for f in DATA.glob("_umzug_*"):   # nicht abgeholte Umzugsdateien nach einer Stunde löschen
                if time.time() - f.stat().st_mtime > 3600:
                    f.unlink(missing_ok=True)
        except Exception:  # noqa
            pass
        threading.Event().wait(6 * 3600)


def cli():
    """Kommandozeile: python server.py adduser NAME [admin] | passwd NAME | users"""
    import getpass
    init_db()
    cmd = sys.argv[1]
    if cmd == "users":
        with db() as conn:
            for r in conn.execute("SELECT username, is_admin, active, last_login FROM users ORDER BY username"):
                print(f"{r['username']:<24} {'Admin' if r['is_admin'] else '':<6} {'' if r['active'] else 'deaktiviert':<12} "
                      f"zuletzt angemeldet: {r['last_login'] or 'nie'}")
        return
    if len(sys.argv) < 3:
        print(cli.__doc__)
        return
    name = sys.argv[2]
    pw = getpass.getpass("Neues Passwort: ")
    if pw != getpass.getpass("Wiederholen: "):
        print("Die Passwörter stimmen nicht überein.")
        sys.exit(1)
    if cmd == "adduser":
        create_user(name, pw, is_admin="admin" in sys.argv[3:])
        print("Benutzer angelegt.")
    elif cmd == "passwd":
        prob = pw_problem(pw)
        if prob:
            print(prob)
            sys.exit(1)
        with db() as conn:
            cur = conn.execute("UPDATE users SET pw_hash=?, invite_hash=NULL, active=1 WHERE username=?", (hash_pw(pw), name))
            conn.execute("DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE username=?)", (name,))
        print("Passwort geändert." if cur.rowcount else "Benutzer nicht gefunden.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("adduser", "passwd", "users"):
        return cli()
    init_db()
    bootstrap_admin()
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        url = f"http://127.0.0.1:{PORT}/"
        running = ""
        try:
            with urllib.request.urlopen(url + "healthz", timeout=3) as r:
                running = json.loads(r.read().decode("utf-8")).get("version", "")
        except Exception:  # noqa
            pass
        if running == VERSION and not SERVER_MODE:
            print(f"{APP_NAME} läuft bereits. Öffne {url}")
            webbrowser.open(url)
        else:
            print("=" * 56)
            print(f"  Port {PORT} ist belegt" + (f" ({APP_NAME} {running})" if running else "") + ".")
            print("  Bitte die andere Instanz beenden und erneut starten.")
            print("=" * 56)
            sys.exit(1)
        return
    httpd.daemon_threads = True
    url = f"http://{'127.0.0.1' if HOST in ('0.0.0.0', '::') else HOST}:{PORT}/"
    print("=" * 56)
    print(f"  {APP_NAME} " + VERSION + ("  (Serverbetrieb)" if SERVER_MODE else ""))
    print(f"  Läuft unter {url}")
    print(f"  Daten: {DATA}")
    if user_count() == 0:
        print("  Noch kein Benutzer angelegt." + ("" if SERVER_MODE else " Ersteinrichtung im Browser."))
    if not SERVER_MODE:
        print("  Zum Beenden dieses Fenster schließen (oder Strg+C).")
    print("=" * 56, flush=True)
    for fn in (backup_loop, cleanup_loop, notify_loop, pf_poll_loop):
        threading.Thread(target=fn, daemon=True).start()
    if not SERVER_MODE and "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
