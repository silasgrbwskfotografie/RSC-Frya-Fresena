# Offene Platzhalter

Stand: Version 1.0. Alles hier erscheint auf der Webseite als **gelb markierter Platzhalter** oder ist ein Vorschlag, der geprüft werden muss.
Nichts davon ist erfunden: Wo eine Angabe fehlt, steht ein Platzhalter. Gepflegt wird fast alles im Hub ohne Code
(**Medien > Webseite**, die Übersicht dort listet, was noch fehlt).

## Vom Verein zu liefern

| Was | Wo es erscheint | Wo pflegen |
|---|---|---|
| Kontakt-E-Mail, Telefon | Footer, Kontakt, Impressum, Signatur | Webseite > Texte > Allgemein und Kontakt |
| Anschrift der Anlage, Karten-Link | Footer, Anlage, Kontakt | Webseite > Texte > Allgemein und Kontakt |
| Satz unter der Überschrift (Startseite) | Startseite | Webseite > Texte > Startseite |
| Über uns, Geschichte | Startseite, Verein | Webseite > Texte > Verein |
| Vorstand und Ansprechpersonen | Verein | Webseite > Texte > Verein (eine Zeile pro Person: Funktion \| Name) |
| Reitunterricht: Einleitung, Angebote, Preise | Reitunterricht, Startseite | Webseite > Texte > Reitunterricht |
| Voltigieren: ob es das gibt, Text | Reitunterricht (nur wenn eingeschaltet) | Webseite > Texte > Reitunterricht |
| Schulpferde mit Fotos und Text | Schulpferde, Startseite | Stall > Pferde (Haken „auf der Webseite“) |
| Anlage: Beschreibung, Eckdaten, Einstellplätze | Anlage | Webseite > Texte > Anlage |
| Termine (Turniere, Lehrgänge, Vereinsleben) | Termine, Startseite | Veranstaltungen |
| Beitragsarten mit Beträgen | Mitgliedschaft | Mitglieder > Beiträge > Beitragsarten |
| Beitrittsantrag als PDF | Mitgliedschaft | Webseite > Texte > Mitgliedschaft |
| Fotos für die Titelbilder (Start, Verein, Unterricht, Pferde, Anlage, Termine, News, Mitgliedschaft, Kontakt) | jeweilige Seite, ohne Foto Fläche in Vereinsfarbe mit Platzhalter | Webseite > Bilder |
| Orte für Unterricht und Belegung (vorbelegt: Reithalle, Reitplatz) | Hallenbelegung, Reitstunden | Einstellungen (Reitbetrieb) |
| Pflicht-Arbeitsstunden je Mitglied | Arbeitsdienste | Einstellungen (Reitbetrieb) |
| Social-Media-Kanäle (falls vorhanden) | Menü, Footer | Webseite > Menü |

## Impressum und Datenschutz

Beide sind Vorlagen mit Angaben in [eckigen Klammern], die gelb erscheinen. Bitte ausfüllen und rechtlich prüfen lassen:

- Vereinsanschrift, vertretungsberechtigter Vorstand, Registergericht und Registernummer, ggf. Umsatzsteuer-ID, Verantwortliche nach § 18 MStV
- Bildnachweis
- Datenschutz: Hosting-Anbieter mit Anschrift, E-Mail-Anbieter mit Anschrift und Serverstandort, Löschfrist für Anfragen,
  zuständige Landesdatenschutzbehörde am Vereinssitz, Stand (Monat Jahr), Pflicht zum Datenschutzbeauftragten prüfen
- Die Datenschutzerklärung beschreibt nur die öffentliche Webseite. Für die Mitgliederdaten im Hub (Mitglieder, Beiträge, Pferde, Einsteller)
  braucht der Verein zusätzlich eigene Informationen für die Mitglieder (z. B. im Beitrittsantrag).

## Noch offen (Entscheidungen)

- **E-Mail-Anbieter** für Postfach und Versand: noch nicht festgelegt. Eintragen unter Einstellungen > E-Mail-Versand.
- **Repository öffentlich oder privat**: noch nicht festgelegt. Bei privat bleibt `TMH_UPDATE_URL` leer (siehe `.env.example`).
- **Eigene Domain**: vorerst `pferd.silasgrbwskfotografie.de`, Suchmaschinen sind ausgeschlossen (abschaltbar unter Webseite > Texte > Suchmaschinen).

## Vorschläge, die geprüft werden sollten

- Gruppen und Rechte (Vorstand, Kasse, Reitlehrer, Stall, Turnierteam, Medien, Wartung) sind ein Vorschlag, änderbar unter Gruppen & Rechte.
- Beschriftungen und Auswahllisten im Hub (z. B. Arten von Gesundheitseinträgen, Unterrichtsarten, Beitragszeiträume, Finanzkategorien) sind Vorschläge.
- Feste Texte auf der Webseite (z. B. „Lust auf eine Probestunde?“, „Fragen zu Unterricht und Preisen klären wir gern persönlich“, Abschnitt „Beitritt“)
  stehen in `website.py` und sind allgemein gehalten, ohne Fakten. Bitte prüfen, ob sie zum Verein passen.
- Vereinsfarben aus dem Logo abgeleitet: Tannengrün `#0D393A` und Gold `#B0945C`.
