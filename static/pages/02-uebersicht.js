/* ====================================================================== */
/* Übersicht (schlicht): Kopf, Kennzahlen-Leiste, Widgets als Karten mit Kopfzeile.
   Auswahl, Reihenfolge und Breite der Widgets je Konto (users.dash_layout); die Schlüssel in W bleiben gleich, damit gespeicherte Auswahlen gelten. */
App.pages.dashboard = async (view) => {
  const stall = can('stall'), unterricht = can('unterricht'), medien = can('medien');
  const belegung = can('unterricht|stall|veranstaltungen');
  const contactsOk = can('mitglieder|verein|unterricht|stall');
  const [dash, bookings] = await Promise.all([api('dashboard'), belegung ? load('arena_bookings', true).catch(() => []) : []]);
  const me = App.me;
  const c = dash.counts || {};

  // Hinweise je nach Rechten
  const notes = [];
  (dash.health_due || []).filter(h => daysUntil(h.next_due) < 0).forEach(h =>
    notes.push(`${esc(h.name)}: ${esc(h.kind)} ist seit ${fmtDate(h.next_due)} fällig. <a class="btn sm" href="#/horses/${h.horse_id}">Ansehen</a>`));
  if (dash.fees_open?.overdue) notes.push(`${dash.fees_open.overdue} Beitrag${dash.fees_open.overdue > 1 ? 'e sind' : ' ist'} überfällig. <a class="btn sm" href="#/fees">Beiträge</a>`);
  if (c.contacts) notes.push(`${c.contacts} neue Anfrage${c.contacts > 1 ? 'n' : ''} über die Webseite. <a class="btn sm" href="#/contacts">Ansehen</a>`);
  (dash.next_events || []).filter(e => e.entry_deadline && daysUntil(e.entry_deadline) >= 0 && daysUntil(e.entry_deadline) <= 7).forEach(e =>
    notes.push(`Nennungsschluss „${esc(e.title)}“ am ${fmtDate(e.entry_deadline)}. <a class="btn sm" href="#/events/${e.id}">Ansehen</a>`));
  if (dash.finance?.open) notes.push(`${dash.finance.open} offene Buchung(en) in den Finanzen. <a class="btn sm" href="#/finance">Ansehen</a>`);
  if (me.member_id === null && !me.is_admin) notes.push('Dein Zugang ist noch keinem Mitgliedseintrag zugeordnet. Das macht der Admin unter Konten.');

  setTimeout(() => dashMailTasks($('#dashTasks')), 0);
  if (can('wartung')) api('maintenance').then(m => {
    const box = $('#dashMaint'); if (!box || !m.on) return;
    box.innerHTML = `<div class="card maint-note"><b>Wartungsmodus ist an.</b> Besucher sehen statt der Webseite die Wartungsseite.
      <a class="btn sm" href="#/wartung">Wartungsmodus</a></div>`;
  }).catch(() => {});

  const first = esc((me.display_name || me.username).split(' ')[0]);
  const today = todayStr(), dow = (new Date(today + 'T12:00:00').getDay() + 6) % 7, monday = addDays(today, -dow);
  const week = [0, 1, 2, 3, 4, 5, 6].map(i => addDays(monday, i));
  const WDS = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
  const nextEv = (dash.next_events || [])[0];
  const days = nextEv ? daysUntil(nextEv.date) : null;
  const inDays = d => d === 0 ? 'heute' : d === 1 ? 'morgen' : `in ${d} Tagen`;
  const rows = list => list.map(([l, v, st]) => `<div class="sum-row"><span>${l}</span><b${st ? ` style="${st}"` : ''}>${v}</b></div>`).join('');
  const num = (n, l) => `<div class="dash-num"><b>${n}</b><span>${l}</span></div>`;
  const none = t => `<div class="empty-s">${t}</div>`;
  const loading = '<p class="muted small">Lädt ...</p>';
  const weekBookings = expandArena(bookings || [], week[0], week[6]);
  const agendaItems = d => [
    ...(dash.next_events || []).filter(e => e.date <= d && (e.end_date || e.date) >= d)
      .map(e => `<a class="ev game" href="#/events/${e.id}" title="${esc(e.kind || '')}: ${esc(e.title)}">${e.time ? e.time + ' ' : ''}${esc(e.title)}</a>`),
    ...weekBookings.filter(x => x.day === d).slice(0, 4)
      .map(x => `<a class="ev" href="#/arena" title="${esc(x.place || '')}: ${esc(x.kind || '')}${x.title ? ', ' + esc(x.title) : ''}">${esc(x.start || '')} ${esc(x.kind || '')}</a>`),
  ].join('');

  // Alle Widgets: t = Titel, link = [Adresse, Text] oben rechts, body() = Inhalt, later(el) füllt nach, ok() = darf die Person es sehen
  const W = {
    events: { t: 'Nächste Termine', w: 1, link: ['#/events', 'Alle'], body: () => (dash.next_events || []).length
      ? `<div class="list">${dash.next_events.slice(0, 5).map(e => `<a class="list-item" href="#/events/${e.id}" style="text-decoration:none;color:inherit">${dateBox(e.date)}
          <div class="grow"><b>${esc(e.title)}</b><div class="muted small">${esc([e.kind, e.time, e.place].filter(Boolean).join(' | '))}</div></div></a>`).join('')}</div>`
      : none('Keine Termine geplant.') },
    countdown: { t: 'Nächster Termin', w: 1, link: ['#/events', 'Veranstaltungen'], body: () => nextEv
      ? `${num(days === 0 ? 'heute' : days, days === 0 ? '' : days === 1 ? 'Tag' : 'Tage')}${rows([['Termin', esc(nextEv.title)], ['Datum', fmtDate(nextEv.date, true) + (nextEv.time ? ', ' + esc(nextEv.time) : '')],
          ['Art', esc(nextEv.kind || '-')], ['Nennungsschluss', nextEv.entry_deadline ? fmtDate(nextEv.entry_deadline) : '-']])}`
      : none('Kein Termin geplant.') },
    arena: { t: 'Halle und Platz heute', w: 1, ok: () => belegung, link: ['#/arena', 'Belegung'], body: () => (dash.arena_today || []).length
      ? rows(dash.arena_today.map(b => [`${esc(b.start || '')} bis ${esc(b.end || '')} <span class="muted small">${esc(b.place || '')}</span>`, esc(b.kind + (b.title ? ': ' + b.title : ''))]))
      : `${none('Heute ist nichts eingetragen.')}${can('unterricht|stall|veranstaltungen', 2) ? '<a class="btn sm" href="#/arena">Belegung eintragen</a>' : ''}` },
    lessons: { t: 'Reitstunden heute', w: 1, ok: () => unterricht, link: ['#/lessons', 'Alle'], body: () => (dash.lessons_today || []).length
      ? rows(dash.lessons_today.map(l => [`<a href="#/lessons/${l.id}">${esc(l.name)}</a>`, esc([l.time, l.place, l.instructor].filter(Boolean).join(', '))]))
      : none('Heute keine festen Stunden.') },
    health: { t: 'Pferde: fällige Termine', w: 1, ok: () => stall, link: ['#/horses', 'Pferde'], body: () => (dash.health_due || []).length
      ? (dash.health_due || []).slice(0, 8).map(h => `<div class="sum-row"><span><a href="#/horses/${h.horse_id}">${esc(h.name)}</a> ${esc(h.kind)}</span>${dueBadge(h.next_due)}</div>`).join('')
      : none('Nichts fällig in den nächsten 14 Tagen.') },
    notes: { t: 'Hinweise', w: 1, tall: true, body: () => notes.length
      ? notes.map(n => `<div class="dash-hint"><span class="dn-dot"></span><div>${n}</div></div>`).join('')
      : '<div class="dash-hint"><span class="dn-dot ok"></span><div>Keine offenen Hinweise.</div></div>' },
    fees: { t: 'Beiträge', w: 1, ok: () => !!dash.fees_open, link: ['#/fees', 'Beiträge'], body: () =>
      rows([['Offene Forderungen', dash.fees_open.count], ['Offener Betrag', eur(dash.fees_open.sum)], ['Überfällig', dash.fees_open.overdue, dash.fees_open.overdue ? 'color:var(--red-text)' : '']]) },
    club: { t: 'Verein in Zahlen', w: 1, ok: () => Object.keys(c).length > 0, body: () => rows([
      c.members !== undefined ? ['Mitglieder (aktiv)', c.members] : null, c.waiting !== undefined ? ['Warteliste', c.waiting] : null,
      c.horses !== undefined ? ['Pferde im Stall', c.horses] : null, c.school_horses !== undefined ? ['davon Schulpferde', c.school_horses] : null,
      c.boxes_free !== undefined ? ['Freie Boxen', c.boxes_free] : null, c.lessons !== undefined ? ['Aktive Stunden und Kurse', c.lessons] : null].filter(Boolean)) },
    finance: { t: 'Finanzen', w: 1, ok: () => !!dash.finance, link: ['#/finance', 'Buchungen'], body: () => rows([['Einnahmen ' + dash.finance.year, eur(dash.finance.income)],
      ['Ausgaben ' + dash.finance.year, eur(dash.finance.expense)],
      ['Saldo', eur(dash.finance.income - dash.finance.expense), `color:${dash.finance.income - dash.finance.expense < 0 ? 'var(--red-text)' : 'var(--ok)'}`],
      ['Offene Buchungen', dash.finance.open]]) },
    calendar: { t: `Diese Woche <span>${fmtDate(monday)} bis ${fmtDate(week[6])}</span>`, name: 'Wochenkalender', w: 2, cls: 'dash-cal',
      link: belegung ? ['#/arena', 'Hallenbelegung'] : ['#/events', 'Termine'], body: () =>
      `<div class="dash-agenda">${week.map((d, i) => `<div class="ad${d === today ? ' today' : ''}"><div class="ad-d">${WDS[i]} ${d.slice(8, 10)}.${d === today ? ' heute' : ''}</div>${agendaItems(d)}</div>`).join('')}</div>` },
    contacts: { t: 'Neue Anfragen', w: 1, ok: () => contactsOk, link: ['#/contacts', 'Anfragen'], body: () => `<div data-fill>${loading}</div>`,
      later: async el => { const r = (await load('contact_requests', true)).filter(x => x.status === 'Neu');
        el.innerHTML = num(r.length, 'neu über die Webseite') + rows(r.slice(0, 4).map(x => [esc(x.name || ''), `<span class="muted small">${esc(x.source || '')}</span>`])); } },
    mail: { t: 'Postfach', w: 1, ok: () => can('postfach|ownmail'), link: ['#/postfach', 'Öffnen'], body: () => `<div data-fill>${loading}</div>`,
      later: async el => { const boxes = [...(can('postfach') ? [['info', 'Vereinspostfach']] : []), ...(me.mail_boxes || []).map(b => [b.key, b.address])];
        const res = await Promise.all(boxes.map(([k, l]) => api('postfach/status' + (k === 'info' ? '' : '?box=' + k)).then(r => [l, Number(r.unseen) || 0]).catch(() => [l, '?'])));
        el.innerHTML = num(res.reduce((n, [, v]) => n + (Number(v) || 0), 0), 'ungelesen') + rows(res.map(([l, v]) => [esc(l), v])); } },
    newsletter: { t: 'Newsletter', w: 1, ok: () => medien, link: ['#/newsletter', 'Newsletter'], body: () => `<div data-fill>${loading}</div>`,
      later: async el => { const d = await api('newsletter'); const act = d.subscribers.filter(x => x.status === 'aktiv').length, last = d.issues.find(i => i.status === 'gesendet');
        el.innerHTML = num(act, 'aktive Abonnenten') + rows([['Unbestätigt', d.subscribers.filter(x => x.status === 'offen').length], ['Entwürfe', d.issues.filter(i => i.status === 'entwurf').length]])
          + (last ? `<p class="muted small" style="margin:8px 0 0">Zuletzt: ${esc(last.subject)} (${fmtDate((last.sent_at || '').slice(0, 10))}, ${last.sent_count} erreicht)</p>` : ''); } },
    quick: { t: 'Schnellzugriff', w: 1, body: () => `<div class="dash-quick">${tabChoices().filter(x => x.page !== 'dashboard').slice(0, 12).map(x => `<a href="${x.href}">${tabIcon(x.icon)}<span>${esc(x.full)}</span></a>`).join('')}</div>` },
  };
  Object.values(W).forEach(x => { x.name = x.name || x.t; });
  const DEFAULT = ['events', 'arena', 'health', 'notes', 'lessons', 'fees', 'calendar', 'club', 'finance'];
  const avail = k => W[k] && (!W[k].ok || W[k].ok());
  const layout = (Array.isArray(me.dash_layout) && me.dash_layout.length ? me.dash_layout : DEFAULT.map(k => ({ k, w: W[k].w }))).filter(x => avail(x.k));
  const kpis = [
    c.members !== undefined ? ['Mitglieder', c.members, '#/members'] : null,
    c.horses !== undefined ? ['Pferde', c.horses, '#/horses'] : null,
    c.boxes_free !== undefined ? ['Freie Boxen', c.boxes_free, '#/boxes'] : null,
    unterricht ? ['Stunden heute', (dash.lessons_today || []).length, '#/lessons'] : null,
    belegung ? ['Halle heute', (dash.arena_today || []).length, '#/arena'] : null,
    dash.fees_open ? ['Beiträge überfällig', dash.fees_open.overdue, '#/fees', dash.fees_open.overdue ? 'hot' : ''] : null,
    nextEv ? ['Nächster Termin', inDays(days), `#/events/${nextEv.id}`] : null,
  ].filter(Boolean).slice(0, 6).concat([['Hinweise', notes.length, '#/', notes.length ? 'hot' : '']]);   // höchstens 7 Zellen, Hinweise immer

  view.innerHTML = `
    <div id="dashMaint"></div>
    <div class="dash-head"><div><h1>Übersicht</h1><div class="muted small">${fmtDate(dash.today, true)} | ${first}${me.groups?.length ? ' | ' + esc(me.groups.join(', ')) : ''}</div></div>
      <div class="btn-row">${can('unterricht|stall|veranstaltungen', 2) ? '<a class="btn primary" href="#/arena">+ Halle belegen</a>' : ''}
        ${can('stall', 2) ? '<a class="btn" href="#/horses">Pferde</a>' : ''}<button class="btn keep" id="dashEdit">Anpassen</button></div></div>
    <section class="dash-kpis">${kpis.map(([l, v, href, cls]) => `<a href="${href}"><small>${esc(l)}</small><b class="${cls || ''}">${v ?? 0}</b></a>`).join('')}</section>
    <div id="dashTasks"></div>
    <section class="dash-grid">${layout.map(x => `<article class="card dash-w ${W[x.k].cls || ''}${x.w === 2 ? ' w2' : ''}${W[x.k].tall ? ' tall' : ''}" data-w="${x.k}">
        <div class="dash-wh"><h3>${W[x.k].t}</h3>${W[x.k].link ? `<a href="${W[x.k].link[0]}">${W[x.k].link[1]}</a>` : ''}</div><div class="dash-wb">${W[x.k].body()}</div></article>`).join('')
      || '<div class="card muted">Keine Widgets ausgewählt. Über „Anpassen“ welche hinzufügen.</div>'}</section>`;
  layout.forEach(x => { const box = $(`[data-w="${x.k}"] [data-fill]`, view); if (W[x.k].later && box) W[x.k].later(box).catch(() => { box.innerHTML = '<p class="muted small">Gerade nicht verfügbar.</p>'; }); });

  $('#dashEdit').onclick = () => {   // Widgets an und aus, Reihenfolge, Breite; gespeichert im eigenen Konto
    let items = [...layout.map(x => ({ k: x.k, w: x.w, on: true })), ...Object.keys(W).filter(k => avail(k) && !layout.some(x => x.k === k)).map(k => ({ k, w: W[k].w, on: false }))];
    const draw = () => {
      openModal(`<h2>Übersicht anpassen</h2>
        <p class="muted small">Haken = Widget anzeigen, Pfeile = Reihenfolge, Breite = eine oder zwei Spalten. Es erscheinen nur Widgets, für die du Rechte hast. Gilt für dein Konto auf allen Geräten.</p>
        <div class="list dash-edit">${items.map((x, i) => `<div class="list-item${x.on ? '' : ' off'}">
          <label class="check grow"><input type="checkbox" data-on="${i}" ${x.on ? 'checked' : ''}> ${esc(W[x.k].name.replace(/<[^>]*>.*$/, ''))}</label>
          <select class="input" data-wd="${i}" style="width:auto" aria-label="Breite"><option value="1" ${x.w !== 2 ? 'selected' : ''}>Normal</option><option value="2" ${x.w === 2 ? 'selected' : ''}>Breit</option></select>
          <button class="btn sm" data-mv="${i}" data-d="-1" ${i ? '' : 'disabled'} aria-label="nach oben">↑</button><button class="btn sm" data-mv="${i}" data-d="1" ${i < items.length - 1 ? '' : 'disabled'} aria-label="nach unten">↓</button></div>`).join('')}</div>
        <div class="modal-foot"><div><button class="btn ghost" id="de_reset">Standard</button></div><div class="btn-row"><button class="btn" onclick="closeModal()">Abbrechen</button><button class="btn primary" id="de_save">Speichern</button></div></div>`);
      $$('[data-on]').forEach(cb => cb.onchange = () => { items[cb.dataset.on].on = cb.checked; cb.closest('.list-item').classList.toggle('off', !cb.checked); });
      $$('[data-wd]').forEach(s => s.onchange = () => { items[s.dataset.wd].w = Number(s.value); });
      $$('[data-mv]').forEach(b => b.onclick = () => { const i = Number(b.dataset.mv), j = i + Number(b.dataset.d); [items[i], items[j]] = [items[j], items[i]]; draw(); });
      const save = async widgets => { try { const r = await api('dashlayout', 'PUT', { widgets }); App.me.dash_layout = r.dash_layout; closeModal(); toast('Übersicht gespeichert'); route(); } catch (e) { alert(e.message); } };
      $('#de_save').onclick = () => save(items.filter(x => x.on).map(({ k, w }) => ({ k, w })));
      $('#de_reset').onclick = () => save(null);
    };
    draw();
  };
};
