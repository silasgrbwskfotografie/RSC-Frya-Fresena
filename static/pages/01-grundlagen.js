/* RSC Frya Fresena Hub: Grundlagen der Seiten (Feld-Definitionen, Beschriftungen, Hilfen) */

/* ---------- Auswahllisten ---------- */
const WEEKDAYS = [['1', 'Montag'], ['2', 'Dienstag'], ['3', 'Mittwoch'], ['4', 'Donnerstag'], ['5', 'Freitag'], ['6', 'Samstag'], ['7', 'Sonntag']];
const weekdayName = n => (WEEKDAYS.find(w => w[0] === String(n)) || ['', ''])[1];
const MEMBER_STATUS = ['Aktiv', 'Passiv', 'Probezeit', 'Warteliste', 'Ausgetreten'];
const HORSE_KINDS = ['Schulpferd', 'Einsteller', 'Vereinspferd', 'Pflegepferd'];
const HEALTH_KINDS = ['Impfung', 'Hufschmied', 'Wurmkur', 'Tierarzt', 'Zahnarzt', 'Sonstiges'];
const LESSON_KINDS = ['Gruppenstunde', 'Einzelstunde', 'Longe', 'Voltigieren', 'Kurs'];
const BOOKING_KINDS = ['Unterricht', 'Buchung', 'Sperrzeit', 'Veranstaltung', 'Pflege'];
const EVENT_KINDS = ['Turnier', 'Lehrgang', 'Abzeichen', 'Vereinsleben', 'Versammlung', 'Sonstiges'];
const DUTY_KINDS = ['Stalldienst', 'Arbeitsdienst', 'Turnierhelfer', 'Sonstiges'];
const CONTACT_TOPICS = ['Probestunde', 'Mitgliedschaft', 'Einstellplatz', 'Allgemeine Frage'];   // wie CONTACT_TOPICS in server.py
const places = () => (App.settings.arena_places || '').split('\n').map(x => x.trim()).filter(Boolean);

/* ---------- Beschriftungen für Auswahlfelder ---------- */
const memberLabel = m => fullName(m) + (m.status && m.status !== 'Aktiv' ? ` (${m.status})` : '');
const horseLabel = h => (h.name || '?') + (h.kind ? ` (${h.kind})` : '') + (Number(h.active) === 0 ? ' (inaktiv)' : '');
const boxLabel = b => (b.name || '?') + (b.location ? ` (${b.location})` : '');
const feeTypeLabel = f => (f.name || '?') + (f.amount != null && f.amount !== '' ? ` (${eur(f.amount)} ${f.interval || ''})` : '');
const groupLabel = g => (g.name || '?') + (g.weekday ? `, ${weekdayName(g.weekday)}` : '') + (g.time ? ` ${g.time}` : '');
const eventLabel = e => `${fmtDate(e.date)} ${e.title || ''}`;

/* ---------- Feld-Definitionen der Tabellen ---------- */
const F = {
  members: [
    { key: 'first_name', label: 'Vorname' },
    { key: 'last_name', label: 'Nachname' },
    { key: 'status', label: 'Status', type: 'select', options: MEMBER_STATUS },
    { key: 'fee_type_id', label: 'Beitragsart', type: 'ref', ref: { table: 'fee_types', label: feeTypeLabel } },
    { key: 'family', label: 'Familie', placeholder: 'z. B. Muster', hint: 'Gleicher Text bei allen Familienmitgliedern. Familienbeiträge werden je Familie nur einmal angelegt.' },
    { key: 'birthdate', label: 'Geburtsdatum', type: 'date' },
    { key: 'member_since', label: 'Mitglied seit', type: 'date' },
    { key: 'exit_date', label: 'Ausgetreten am', type: 'date', show: v => v.status === 'Ausgetreten' },
    { key: 'email', label: 'E-Mail' },
    { key: 'phone', label: 'Telefon' },
    { key: 'street', label: 'Straße und Hausnummer', full: true },
    { key: 'zip', label: 'PLZ' },
    { key: 'city', label: 'Ort' },
    { key: 'guardian', label: 'Erziehungsberechtigte', full: true, hint: 'Bei Minderjährigen: Name und Kontakt.' },
    { key: 'emergency_contact', label: 'Notfallkontakt', full: true },
    { key: 'payment', label: 'Zahlweise', type: 'select', options: ['Lastschrift', 'Überweisung', 'Bar'] },
    { key: 'mandate_ref', label: 'SEPA-Mandatsreferenz', hint: 'Bankdaten selbst stehen nicht im Hub.' },
    { key: 'work_hours_due', label: 'Pflicht-Arbeitsstunden im Jahr', type: 'number', hint: 'Leer = Standard aus den Einstellungen.' },
    { key: 'consent_photo', label: 'Foto-Einwilligung liegt vor', type: 'check' },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  fee_types: [
    { key: 'name', label: 'Name', placeholder: 'z. B. Kinder, Jugend, Erwachsene, Familie, passiv' },
    { key: 'amount', label: 'Betrag (€)', type: 'number' },
    { key: 'interval', label: 'Zeitraum', type: 'select', options: ['jährlich', 'halbjährlich', 'vierteljährlich', 'monatlich'] },
    { key: 'admission_fee', label: 'Aufnahmegebühr (€)', type: 'number' },
    { key: 'age_from', label: 'Alter von', type: 'number' },
    { key: 'age_to', label: 'Alter bis', type: 'number' },
    { key: 'per_family', label: 'Familienbeitrag (einmal je Familie)', type: 'check' },
    { key: 'public', label: 'Auf der Webseite zeigen', type: 'check', default: 1 },
    { key: 'active', label: 'Aktiv', type: 'check', default: 1 },
    { key: 'sort', label: 'Reihenfolge', type: 'number', hint: 'Kleine Zahl steht oben.' },
    { key: 'description', label: 'Beschreibung', type: 'textarea', rows: 2 },
  ],
  fee_payments: [
    { key: 'member_id', label: 'Mitglied', type: 'ref', ref: { table: 'members', label: memberLabel } },
    { key: 'period', label: 'Zeitraum', placeholder: 'z. B. 2027 oder 2027 H1' },
    { key: 'amount', label: 'Betrag (€)', type: 'number' },
    { key: 'due', label: 'Fällig am', type: 'date' },
    { key: 'paid', label: 'Bezahlt', type: 'check' },
    { key: 'paid_on', label: 'Bezahlt am', type: 'date', show: v => Number(v.paid) },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  duties: [
    { key: 'date', label: 'Datum', type: 'date', default: todayStr() },
    { key: 'kind', label: 'Art', type: 'select', options: DUTY_KINDS },
    { key: 'start', label: 'Beginn', type: 'time' },
    { key: 'end', label: 'Ende', type: 'time' },
    { key: 'task', label: 'Aufgabe', full: true, placeholder: 'z. B. Misten Stallgasse A, Hallenboden abziehen' },
    { key: 'member_id', label: 'Mitglied', type: 'ref', ref: { table: 'members', label: memberLabel } },
    { key: 'name', label: 'Name (falls kein Mitglied)' },
    { key: 'hours', label: 'Stunden', type: 'number' },
    { key: 'status', label: 'Status', type: 'select', options: ['geplant', 'erledigt', 'abgesagt'] },
    { key: 'confirmed', label: 'Stunden bestätigt', type: 'check', hint: 'Nur bestätigte Stunden zählen für die Pflichtstunden.' },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  contact_requests: [
    { key: 'name', label: 'Name' },
    { key: 'source', label: 'Anliegen', type: 'select', options: [...CONTACT_TOPICS, 'Webseite', 'Telefon', 'Persönlich'] },
    { key: 'email', label: 'E-Mail' },
    { key: 'phone', label: 'Telefon' },
    { key: 'age', label: 'Alter' },
    { key: 'experience', label: 'Reiterfahrung' },
    { key: 'message', label: 'Nachricht', type: 'textarea', rows: 5 },
    { key: 'status', label: 'Status', type: 'select', options: ['Neu', 'In Bearbeitung', 'Erledigt'] },
    { key: 'notes', label: 'Interne Notizen', type: 'textarea', rows: 2 },
  ],
  horses: [
    { key: 'name', label: 'Name' },
    { key: 'kind', label: 'Art', type: 'select', options: HORSE_KINDS },
    { key: 'breed', label: 'Rasse' },
    { key: 'sex', label: 'Geschlecht', type: 'select', options: ['', 'Stute', 'Wallach', 'Hengst'] },
    { key: 'birth_year', label: 'Jahrgang' },
    { key: 'height', label: 'Stockmaß', placeholder: 'z. B. 1,48 m' },
    { key: 'color', label: 'Farbe' },
    { key: 'ueln', label: 'Lebensnummer (UELN)' },
    { key: 'owner', label: 'Besitzer' },
    { key: 'owner_member_id', label: 'Besitzer (Mitglied)', type: 'ref', ref: { table: 'members', label: memberLabel } },
    { key: 'box_id', label: 'Box', type: 'ref', ref: { table: 'boxes', label: boxLabel } },
    { key: 'suited_for', label: 'Geeignet für', placeholder: 'z. B. Anfänger, Kinder, Voltigieren' },
    { key: 'lessons_max', label: 'Höchstens Einheiten pro Tag', type: 'number' },
    { key: 'active', label: 'Aktiv im Stall', type: 'check', default: 1 },
    { key: 'feeding', label: 'Fütterung und Besonderheiten', type: 'textarea', rows: 2 },
    { key: 'public', label: 'Auf der Webseite unter Schulpferde zeigen', type: 'check', hint: 'Öffentlich sind nur Name, Rasse, Geschlecht, Jahrgang, Stockmaß, „Geeignet für“, Text und Foto.' },
    { key: 'public_text', label: 'Text für die Webseite', type: 'textarea', rows: 3, show: v => Number(v.public) },
    { key: 'photo', label: 'Foto', type: 'image', full: true },
    { key: 'notes', label: 'Interne Notizen', type: 'textarea', rows: 2 },
  ],
  horse_health: [
    { key: 'horse_id', label: 'Pferd', type: 'ref', ref: { table: 'horses', label: horseLabel } },
    { key: 'kind', label: 'Art', type: 'select', options: HEALTH_KINDS },
    { key: 'date', label: 'Datum', type: 'date', default: todayStr() },
    { key: 'next_due', label: 'Nächster Termin', type: 'date', hint: 'Der Hub erinnert vorher (Einstellung „Erinnerung Tage vorher“) und am Tag.' },
    { key: 'by', label: 'Durchgeführt von', placeholder: 'Tierarzt, Hufschmied, selbst' },
    { key: 'details', label: 'Details', placeholder: 'z. B. Influenza und Tetanus, Eisen vorne' },
    { key: 'cost', label: 'Kosten (€)', type: 'number', show: () => can('finanzen') },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  boxes: [
    { key: 'name', label: 'Bezeichnung', placeholder: 'z. B. Box 12' },
    { key: 'kind', label: 'Art', type: 'select', options: ['Box', 'Paddockbox', 'Offenstall', 'Weide'] },
    { key: 'location', label: 'Stallgasse / Gebäude' },
    { key: 'size', label: 'Größe' },
    { key: 'rent', label: 'Monatsmiete (€)', type: 'number', show: () => can('finanzen') },
    { key: 'horse_id', label: 'Pferd', type: 'ref', ref: { table: 'horses', label: horseLabel } },
    { key: 'active', label: 'In Nutzung', type: 'check', default: 1 },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  boarding_contracts: [
    { key: 'horse_id', label: 'Pferd', type: 'ref', ref: { table: 'horses', label: horseLabel } },
    { key: 'box_id', label: 'Box', type: 'ref', ref: { table: 'boxes', label: boxLabel } },
    { key: 'boarder', label: 'Einsteller (Name)' },
    { key: 'member_id', label: 'Einsteller (Mitglied)', type: 'ref', ref: { table: 'members', label: memberLabel } },
    { key: 'email', label: 'E-Mail' },
    { key: 'phone', label: 'Telefon' },
    { key: 'start', label: 'Beginn', type: 'date' },
    { key: 'end', label: 'Ende', type: 'date', hint: 'Der Hub erinnert 30 Tage vorher.' },
    { key: 'rent', label: 'Monatsmiete laut Vertrag (€)', type: 'number' },
    { key: 'status', label: 'Status', type: 'select', options: ['Angefragt', 'Aktiv', 'Gekündigt', 'Beendet'] },
    { key: 'services', label: 'Leistungen (eine pro Zeile)', type: 'textarea', rows: 3 },
    { key: 'files', label: 'Vertrag und Unterlagen', type: 'files', full: true },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  lesson_groups: [
    { key: 'name', label: 'Name', placeholder: 'z. B. Anfänger Kinder' },
    { key: 'kind', label: 'Art', type: 'select', options: LESSON_KINDS },
    { key: 'weekday', label: 'Wochentag', type: 'select', options: [['', '(kein fester Tag)'], ...WEEKDAYS] },
    { key: 'time', label: 'Uhrzeit', type: 'time' },
    { key: 'duration', label: 'Dauer (Minuten)', type: 'number', default: 60 },
    { key: 'place', label: 'Ort', type: 'select', options: () => ['', ...places()] },
    { key: 'instructor', label: 'Reitlehrer/in' },
    { key: 'level', label: 'Niveau', placeholder: 'z. B. Anfänger, Fortgeschritten' },
    { key: 'max_riders', label: 'Höchstens Teilnehmende', type: 'number' },
    { key: 'price', label: 'Preis', placeholder: 'z. B. 18 € je Stunde' },
    { key: 'start_date', label: 'Beginn (bei Kursen)', type: 'date' },
    { key: 'end_date', label: 'Ende (bei Kursen)', type: 'date' },
    { key: 'active', label: 'Aktiv', type: 'check', default: 1 },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  lesson_participants: [
    { key: 'group_id', label: 'Stunde / Kurs', type: 'ref', ref: { table: 'lesson_groups', label: groupLabel } },
    { key: 'member_id', label: 'Mitglied', type: 'ref', ref: { table: 'members', label: memberLabel } },
    { key: 'name', label: 'Name (falls kein Mitglied)' },
    { key: 'horse_id', label: 'Stammpferd', type: 'ref', ref: { table: 'horses', label: horseLabel } },
    { key: 'since', label: 'Dabei seit', type: 'date', default: todayStr() },
    { key: 'active', label: 'Aktiv', type: 'check', default: 1 },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  arena_bookings: [
    { key: 'place', label: 'Ort', type: 'select', options: () => places() },
    { key: 'kind', label: 'Art', type: 'select', options: BOOKING_KINDS },
    { key: 'date', label: 'Datum', type: 'date', default: todayStr() },
    { key: 'start', label: 'Von', type: 'time' },
    { key: 'end', label: 'Bis', type: 'time' },
    { key: 'repeat_until', label: 'Wöchentlich wiederholen bis', type: 'date', hint: 'Leer = nur dieser Tag.' },
    { key: 'title', label: 'Titel', placeholder: 'z. B. Springstunde, Hallenboden abziehen' },
    { key: 'booked_by', label: 'Gebucht für / von' },
    { key: 'public', label: 'Im öffentlichen Hallenplan zeigen', type: 'check', default: 1, hint: 'Öffentlich nur Zeit und Art, bei „Buchung“ ohne Titel, nie Namen.' },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  events: [
    { key: 'title', label: 'Titel', full: true },
    { key: 'kind', label: 'Art', type: 'select', options: EVENT_KINDS },
    { key: 'date', label: 'Datum', type: 'date' },
    { key: 'end_date', label: 'Bis (mehrtägig)', type: 'date' },
    { key: 'time', label: 'Uhrzeit', type: 'time' },
    { key: 'place', label: 'Ort' },
    { key: 'entry_deadline', label: 'Nennungs- bzw. Anmeldeschluss', type: 'date' },
    { key: 'max_entries', label: 'Höchstens Teilnehmende', type: 'number' },
    { key: 'price', label: 'Kosten', placeholder: 'z. B. 25 € je Start' },
    { key: 'contact', label: 'Ansprechperson' },
    { key: 'public', label: 'Auf der Webseite unter Termine zeigen', type: 'check', default: 1 },
    { key: 'file', label: 'Ausschreibung (PDF oder Bild)', type: 'file', full: true, hint: 'Erscheint auf der Webseite als Link „Ausschreibung ansehen“.' },
    { key: 'description', label: 'Beschreibung (öffentlich)', type: 'textarea', rows: 4 },
    { key: 'notes', label: 'Interne Notizen', type: 'textarea', rows: 2 },
  ],
  event_entries: [
    { key: 'event_id', label: 'Veranstaltung', type: 'ref', ref: { table: 'events', label: eventLabel } },
    { key: 'member_id', label: 'Mitglied', type: 'ref', ref: { table: 'members', label: memberLabel } },
    { key: 'name', label: 'Name (falls kein Mitglied)' },
    { key: 'horse_id', label: 'Pferd', type: 'ref', ref: { table: 'horses', label: horseLabel } },
    { key: 'horse_name', label: 'Pferd (falls nicht im Stall)' },
    { key: 'class', label: 'Prüfung / Gruppe' },
    { key: 'status', label: 'Status', type: 'select', options: ['gemeldet', 'bestätigt', 'abgesagt'] },
    { key: 'fee', label: 'Nenngeld (€)', type: 'number' },
    { key: 'paid', label: 'Bezahlt', type: 'check' },
    { key: 'result', label: 'Platzierung' },
    { key: 'score', label: 'Wertnote / Punkte / Zeit' },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
  event_helpers: [
    { key: 'event_id', label: 'Veranstaltung', type: 'ref', ref: { table: 'events', label: eventLabel } },
    { key: 'task', label: 'Aufgabe', placeholder: 'z. B. Meldestelle, Parcoursdienst, Kuchenbuffet' },
    { key: 'date', label: 'Datum', type: 'date' },
    { key: 'start', label: 'Von', type: 'time' },
    { key: 'end', label: 'Bis', type: 'time' },
    { key: 'slots', label: 'Benötigte Helfer', type: 'number', default: 1 },
    { key: 'sort', label: 'Reihenfolge', type: 'number' },
    { key: 'helpers', label: 'Eingeteilt (ein Name pro Zeile)', type: 'textarea', rows: 3 },
    { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
  ],
};

/* ---------- kleine Bausteine ---------- */
function dateBox(d) {
  const x = parseDate(d);
  if (!x) return '<div class="date-box"><b>?</b><span>offen</span></div>';
  return `<div class="date-box"><b>${x.getDate()}</b><span>${MON_S[x.getMonth()]} ${WD_S[x.getDay()]}</span></div>`;
}
const badge = (text, cls = '') => `<span class="badge ${cls}">${esc(text)}</span>`;
function dueBadge(d) {
  const n = daysUntil(d);
  if (n === null) return '';
  if (n < 0) return badge(`überfällig seit ${-n} Tag${n === -1 ? '' : 'en'}`, 'red');
  if (n <= 14) return badge(n === 0 ? 'heute' : `in ${n} Tag${n === 1 ? '' : 'en'}`, 'warn');
  return badge(fmtDate(d), '');
}
/* Belegungen (mit wöchentlicher Wiederholung) im Zeitraum als Einzeltermine, wie arena_expand im Server */
function expandArena(rows, start, end) {
  const out = [];
  for (const r of rows) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(r.date || '')) continue;
    const until = /^\d{4}-\d{2}-\d{2}$/.test(r.repeat_until || '') ? r.repeat_until : r.date;
    for (let d = r.date; d <= until && d <= end; d = addDays(d, 7)) if (d >= start) out.push({ ...r, day: d });
  }
  return out.sort((a, b) => a.day.localeCompare(b.day) || (a.start || '').localeCompare(b.start || ''));
}
/* CSV-Download (erste Zeile = Spaltennamen, UTF-8 mit BOM für Excel) */
function downloadCsv(filename, header, rows) {
  const q = v => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const csv = [header.map(q).join(','), ...rows.map(r => r.map(q).join(','))].join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
  a.download = filename; a.click();
}
const fmtStamp = s => s ? new Date(s).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
/* Kleine Tabelle mit Bearbeiten-Knöpfen für die Unterseiten */
function simpleTable(cols, rows, empty, editAttr) {
  return `<div class="table-wrap"><table><thead><tr>${cols.map(c => `<th>${c}</th>`).join('')}<th></th></tr></thead><tbody>
    ${rows.map(r => `<tr>${r.cells.map(c => `<td>${c}</td>`).join('')}<td class="actions"><button class="btn sm ghost" ${editAttr}="${r.id}">${App.readOnly ? 'Ansehen' : 'Bearbeiten'}</button></td></tr>`).join('')
      || `<tr><td colspan="${cols.length + 1}" class="empty">${empty}</td></tr>`}</tbody></table></div>`;
}
