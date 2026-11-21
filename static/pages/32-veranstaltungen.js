/* ====================================================================== */
/* Veranstaltungen: Turniere, Lehrgänge, Abzeichen mit Nennungen, Ergebnissen und Helferplan */

App.pages.events = async (view, args) => {
  await loadAll('members', 'horses');
  const events = await load('events', true);
  if (args[0]) return eventDetail(view, Number(args[0]), events);
  let when = App._evWhen || 'kommend';
  const draw = () => {
    App._evWhen = when;
    const today = todayStr();
    const list = events.filter(e => when === 'alle' || (when === 'kommend' ? (e.end_date || e.date || '9') >= today : (e.end_date || e.date || '') < today))
      .sort((a, b) => when === 'vergangen' ? (b.date || '').localeCompare(a.date || '') : (a.date || '9').localeCompare(b.date || '9'));
    view.innerHTML = `
      <div class="page-head"><h1>Turniere &amp; Lehrgänge<small>${events.filter(e => (e.end_date || e.date || '9') >= today).length} kommende Termine</small></h1>
        <div class="btn-row"><button class="btn primary" id="ev_new">+ Veranstaltung</button></div></div>
      <div class="toolbar">${[['kommend', 'Kommende'], ['vergangen', 'Vergangene'], ['alle', 'Alle']].map(([k, l]) => `<button class="btn sm ${k === when ? 'dark' : ''}" data-w="${k}">${l}</button>`).join('')}</div>
      <div class="list">${list.map(e => `<a class="list-item card" href="#/events/${e.id}" style="text-decoration:none;color:inherit">${dateBox(e.date)}
        <div class="grow"><b>${esc(e.title || '?')}</b> ${Number(e.public) ? '<span class="badge ok">Web</span>' : ''}
          <div class="muted small">${esc([e.kind, e.end_date && e.end_date !== e.date ? 'bis ' + fmtDate(e.end_date) : '', e.time, e.place].filter(Boolean).join(' | '))}</div></div>
        ${e.entry_deadline && e.entry_deadline >= today ? `<span class="small">Nennschluss ${dueBadge(e.entry_deadline)}</span>` : ''}</a>`).join('') || '<div class="card empty">Keine Veranstaltungen in dieser Ansicht.</div>'}</div>`;
    $$('[data-w]').forEach(b => b.onclick = () => { when = b.dataset.w; draw(); });
    $('#ev_new').onclick = async () => { const r = await editRecord('events', F.events, { kind: 'Turnier', public: 1 }, { newTitle: 'Neue Veranstaltung' }); if (r && r.id) go(`#/events/${r.id}`); };
  };
  draw();
};

async function eventDetail(view, id, events) {
  const ev = byId(events, id);
  if (!ev) { view.innerHTML = '<div class="card empty">Nicht gefunden. <a href="#/events">Zurück</a></div>'; return; }
  const entries = (await load('event_entries', true)).filter(x => x.event_id == id);
  const helpers = (await load('event_helpers', true)).filter(x => x.event_id == id)
    .sort((a, b) => (Number(a.sort) || 0) - (Number(b.sort) || 0) || (a.date || '').localeCompare(b.date || '') || (a.start || '').localeCompare(b.start || ''));
  const who = x => esc(fullName(byId(App.cache.members, x.member_id)) || x.name || '?');
  const horse = x => esc(byId(App.cache.horses, x.horse_id)?.name || x.horse_name || '');
  const names = h => (h.helpers || '').split('\n').map(s => s.trim()).filter(Boolean);
  const needed = helpers.reduce((n, h) => n + (Number(h.slots) || 0), 0), filled = helpers.reduce((n, h) => n + Math.min(names(h).length, Number(h.slots) || 0), 0);
  view.innerHTML = `
    <div class="page-head"><h1><a href="#/events" class="muted" style="text-decoration:none">Veranstaltungen</a> / ${esc(ev.title)}<small>${esc([ev.kind, fmtDate(ev.date, true) + (ev.end_date && ev.end_date !== ev.date ? ' bis ' + fmtDate(ev.end_date, true) : ''), ev.time, ev.place].filter(Boolean).join(' | '))}</small></h1>
      <div class="btn-row"><button class="btn" id="ed_edit">${App.readOnly ? 'Details' : 'Bearbeiten'}</button>${ev.file ? `<a class="btn keep" href="${esc(ev.file)}" target="_blank" rel="noopener">Ausschreibung</a>` : ''}</div></div>
    <div class="grid g4" style="margin-bottom:16px">
      <div class="card stat"><span>Nennungen</span><b>${entries.filter(x => x.status !== 'abgesagt').length}${ev.max_entries ? ' / ' + ev.max_entries : ''}</b></div>
      <div class="card stat"><span>Nennschluss</span><b>${ev.entry_deadline ? fmtDate(ev.entry_deadline) : '-'}</b></div>
      <div class="card stat"><span>Helfer</span><b>${filled} / ${needed}</b></div>
      <div class="card stat"><span>Webseite</span><b>${Number(ev.public) ? 'ja' : 'nein'}</b></div>
    </div>
    <div class="page-head" style="margin-bottom:8px"><h2 style="margin:0">Nennungen und Ergebnisse</h2><div class="btn-row"><button class="btn keep" id="ed_csv">CSV</button><button class="btn primary" id="ed_entry">+ Nennung</button></div></div>
    ${simpleTable(['Name', 'Pferd', 'Prüfung', 'Status', 'Nenngeld', 'Ergebnis'], entries.map(x => ({ id: x.id, cells: [`<b>${who(x)}</b>`, horse(x), esc(x.class || ''),
      badge(x.status || '', x.status === 'bestätigt' ? 'ok' : x.status === 'abgesagt' ? '' : 'warn'), x.fee ? eur(x.fee) + (Number(x.paid) ? ' ' + badge('bezahlt', 'ok') : '') : '',
      esc([x.result, x.score].filter(Boolean).join(', '))] })), 'Noch keine Nennungen.', 'data-en')}
    <div class="page-head" style="margin:20px 0 8px"><h2 style="margin:0">Helferplan</h2><button class="btn primary" id="ed_helper">+ Aufgabe</button></div>
    ${simpleTable(['Aufgabe', 'Wann', 'Eingeteilt', 'Besetzt'], helpers.map(h => ({ id: h.id, cells: [`<b>${esc(h.task || '?')}</b>`,
      [h.date ? fmtDate(h.date) : '', [h.start, h.end].filter(Boolean).join(' bis ')].filter(Boolean).join(', '), esc(names(h).join(', ')),
      names(h).length >= (Number(h.slots) || 0) ? badge(`${names(h).length} / ${h.slots || 0}`, 'ok') : badge(`${names(h).length} / ${h.slots || 0}`, 'warn')] })), 'Noch keine Aufgaben.', 'data-he')}
    ${ev.description ? `<div class="card" style="margin-top:16px"><h2>Beschreibung (öffentlich)</h2><p style="white-space:pre-line">${esc(ev.description)}</p></div>` : ''}`;
  const again = () => App.pages.events(view, [String(id)]);
  $('#ed_edit').onclick = async () => { const r = await editRecord('events', F.events, ev, { editTitle: ev.title }); if (r) r.deleted ? go('#/events') : again(); };
  $('#ed_entry').onclick = async () => { if (await editRecord('event_entries', F.event_entries, { event_id: id, status: 'gemeldet' }, { newTitle: 'Neue Nennung' })) again(); };
  $('#ed_helper').onclick = async () => { if (await editRecord('event_helpers', F.event_helpers, { event_id: id, date: ev.date, slots: 1 }, { newTitle: 'Neue Aufgabe' })) again(); };
  $$('[data-en]').forEach(b => b.onclick = async () => { if (await editRecord('event_entries', F.event_entries, byId(entries, b.dataset.en), { editTitle: 'Nennung' })) again(); });
  $$('[data-he]').forEach(b => b.onclick = async () => { if (await editRecord('event_helpers', F.event_helpers, byId(helpers, b.dataset.he), { editTitle: 'Aufgabe im Helferplan' })) again(); });
  $('#ed_csv').onclick = () => downloadCsv(`nennungen-${ev.date || ''}-${(ev.title || '').replace(/\W+/g, '-')}.csv`, ['Name', 'Pferd', 'Prüfung', 'Status', 'Nenngeld', 'Bezahlt', 'Platzierung', 'Wertung'],
    entries.map(x => [fullName(byId(App.cache.members, x.member_id)) || x.name, byId(App.cache.horses, x.horse_id)?.name || x.horse_name, x.class, x.status,
      String(x.fee ?? '').replace('.', ','), Number(x.paid) ? 'ja' : 'nein', x.result, x.score]));
}
