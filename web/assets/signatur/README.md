# E-Mail-Signatur RSC Frya Fresena

Der Hub baut die Signatur für Mails aus dem Postfach selbst (`signature_parts` in `server.py`).

| Datei | Zweck |
|---|---|
| `signatur.html` | HTML-Signatur, alle Styles inline, Layout per Tabellen (Outlook) |
| `signatur.txt` | Textfassung |
| `logo.png` | Vereinslogo 240 x 240 px, angezeigt mit 90 x 90 px |
| `icon-mail.png`, `icon-web.png`, `icon-phone.png`, `icon-insta.png` | Symbole 72 x 72 px, Anzeige 18 x 18 px |

Platzhalter, die der Hub ersetzt:

- `{{NAME}}`: Anzeigename des Kontos
- `{{ROLLE}}`: „Rolle in der Signatur“ (Konten) oder die Gruppen der Person, Zeile entfällt ohne Rolle
- `{{SIG}}`: Adresse der Bilder, `<DOMAIN>/signatur/` (aus `DOMAIN` in der `.env`)
- `{{CLUB}}`: Vereinsname aus den Einstellungen
- `{{TELEFON}}` / `{{TELEFON_ZEILE}}`: Telefon aus „Mein Profil“, entfällt ohne Nummer
- `{{EMAIL}}` / `{{EMAIL_ZEILE}}`: Kontakt-E-Mail der Webseite, entfällt ohne Adresse
- `{{WEB}}` / `{{WEB_ZEILE}}`: Adresse der Webseite, entfällt ohne Domain

Die Bilder liegen öffentlich unter `/signatur/<datei>.png`. Keine Base64-Bilder, keine `<style>`-Blöcke.
