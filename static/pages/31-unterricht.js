/* ====================================================================== */
/* Unterricht: Reitstunden und Kurse mit Teilnehmenden und Anwesenheit, Hallen- und Platzbelegung */

App.pages.lessons = async (view, args) => {
  await loadAll('members', 'horses');
  const groups = await load('lesson_groups', true);
  const parts = await load('lesson_participants', true);
  if (args[0]) return lessonDetail(view, Number(args[0]), groups, parts);
  let showInactive = false;
  const draw = () => {
    const list = groups.filter(g => showInactive || Number(g.active) !== 0)
      .sort((a, b) => (a.weekday || '9').localeCompare(b.weekday || '9') || (a.time || '').localeCompare(b.time || '') || (a.name || '').localeCompare(b.name || ''));
    const count = g => parts.filter(p => p.group_id == g.id && Number(p.active) !== 0).length;
    view.innerHTML = `
      <div class="page-head"><h1>Reitstunden &amp; Kurse<small>${groups.filter(g => Number(g.active) !== 0).length} aktiv</small></h1>
        <div class="btn-row"><button class="btn primary" id="l_new">+ Stunde / Kurs</button></div></div>
      <div class="toolbar"><label class="check"><input type="checkbox" id="l_inact" ${showInactive ? 'checked' : ''}> inaktive zeigen</label></div>
      ${simpleTable(['Name', 'Art', 'Wann', 'Ort', 'Reitlehrer/in', 'Belegt'], list.map(g => ({ id: g.id, cells: [
        `<a href="#/lessons/${g.id}"><b>${esc(g.name || '?')}</b></a>${Number(g.active) === 0 ? ' <span class="badge">inaktiv</span>' : ''}`, esc(g.kind || ''),
        g.weekday ? `${weekdayName(g.weekday)}${g.time ? ' ' + esc(g.time) : ''}` : g.start_date ? `${fmtDate(g.start_date)}${g.end_date ? ' bis ' + fmtDate(g.end_date) : ''}` : '',
        esc(g.place || ''), esc(g.instructor || ''), `${count(g)}${g.max_riders ? ' / ' + g.max_riders : ''}`] })), 'Noch keine Stunden oder Kurse angelegt.', 'data-l')}`;
    $('#l_inact').onchange = e => { showInactive = e.target.checked; draw(); };
    $('#l_new').onclick = async () => { const r = await editRecord('lesson_groups', F.lesson_groups, { kind: 'Gruppenstunde', duration: 60, active: 1 }, { newTitle: 'Neue Stunde / Kurs' }); if (r && r.id) go(`#/lessons/${r.id}`); };
    $$('[data-l]').forEach(b => b.onclick = async () => { if (await editRecord('lesson_groups', F.lesson_groups, byId(groups, b.dataset.l), { editTitle: 'Stunde / Kurs' })) App.pages.lessons(view, []); });
  };
  draw();
};

async function lessonDetail(view, id, groups, parts) {
  const g = byId(groups, id);
  if (!g) { view.innerHTML = '<div class="card empty">Nicht gefunden. <a href="#/lessons">Zurück</a></div>'; return; }
  const att = (await load('lesson_attendance', true)).filter(a => a.group_id == id);
  const mine = parts.filter(p => p.group_id == id).sort((a, b) => Number(b.active !== 0) - Number(a.active !== 0) || pName(a).localeCompare(pName(b)));
  const activeParts = mine.filter(p => Number(p.active) !== 0);
  // Termin für die Anwesenheit: heute bzw. der letzte passende Wochentag
  let day = App._attDay?.[id] || todayStr();
  if (!App._attDay?.[id] && g.weekday) { const dow = (new Date(day + 'T12:00:00').getDay() + 6) % 7 + 1; day = addDays(day, -((dow - Number(g.weekday) + 7) % 7)); }
  const dates = [...new Set(att.map(a => a.date))].sort().reverse().slice(0, 12);
  const draw = () => {
    const onDay = att.filter(a => a.date === day);
    const st = p => onDay.find(a => a.participant_id == p.id);
    view.innerHTML = `
      <div class="page-head"><h1><a href="#/lessons" class="muted" style="text-decoration:none">Stunden</a> / ${esc(g.name)}<small>${esc([g.kind, g.weekday ? weekdayName(g.weekday) + (g.time ? ' ' + g.time : '') : '', g.place, g.instructor].filter(Boolean).join(', '))}</small></h1>
        <div class="btn-row"><button class="btn" id="ld_edit">${App.readOnly ? 'Details' : 'Bearbeiten'}</button><button class="btn primary" id="ld_add">+ Teilnehmer/in</button></div></div>
      <div class="grid g2">
        <div class="card"><h2>Teilnehmende ${activeParts.length}${g.max_riders ? ' / ' + g.max_riders : ''}</h2>
          ${mine.map(p => `<div class="sum-row" style="${Number(p.active) === 0 ? 'opacity:.55' : ''}"><span><b>${esc(pName(p))}</b>${p.horse_id ? ` <span class="muted small">${esc(byId(App.cache.horses, p.horse_id)?.name || '')}</span>` : ''}</span>
            <button class="btn sm ghost" data-p="${p.id}">${App.readOnly ? 'Ansehen' : 'Bearbeiten'}</button></div>`).join('') || '<p class="muted">Noch niemand eingetragen.</p>'}</div>
        <div class="card"><h2>Anwesenheit</h2>
          <div class="toolbar"><input type="date" class="input" id="ld_day" value="${esc(day)}" style="width:auto">
            ${dates.filter(d => d !== day).slice(0, 4).map(d => `<button class="btn sm" data-day="${d}">${fmtDate(d)}</button>`).join('')}</div>
          ${activeParts.map(p => { const a = st(p); return `<div class="sum-row"><span>${esc(pName(p))}</span><span class="btn-row">${['da', 'entschuldigt', 'fehlt'].map(s =>
            `<button class="btn sm ${a?.status === s ? (s === 'da' ? 'primary' : 'dark') : ''}" data-att="${p.id}" data-s="${s}" ${App.readOnly ? 'disabled' : ''}>${s}</button>`).join('')}</span></div>`; }).join('') || '<p class="muted">Keine aktiven Teilnehmenden.</p>'}
          <p class="hint">${onDay.filter(a => a.status === 'da').length} von ${activeParts.length} da am ${fmtDate(day, true)}.</p></div>
      </div>`;
    $('#ld_day').onchange = e => { day = e.target.value; App._attDay = { ...(App._attDay || {}), [id]: day }; draw(); };
    $$('[data-day]').forEach(b => b.onclick = () => { day = b.dataset.day; App._attDay = { ...(App._attDay || {}), [id]: day }; draw(); });
    $$('[data-att]').forEach(b => b.onclick = async () => {
      const p = byId(parts, b.dataset.att), cur = onDay.find(a => a.participant_id == p.id);
      try {
        const rec = cur ? await api(`lesson_attendance/${cur.id}`, 'PUT', { status: b.dataset.s })
          : await api('lesson_attendance', 'POST', { group_id: id, date: day, participant_id: p.id, status: b.dataset.s, horse_id: p.horse_id || null });
        const i = att.findIndex(a => a.id === rec.id); i >= 0 ? (att[i] = rec) : att.push(rec);
        draw();
      } catch (e) { alert(e.message); }
    });
    const again = () => App.pages.lessons(view, [String(id)]);
    $('#ld_edit').onclick = async () => { const r = await editRecord('lesson_groups', F.lesson_groups, g, { editTitle: g.name }); if (r) r.deleted ? go('#/lessons') : again(); };
    $('#ld_add').onclick = async () => { if (await editRecord('lesson_participants', F.lesson_participants, { group_id: id, active: 1, since: todayStr() }, { newTitle: 'Neue Teilnehmerin / neuer Teilnehmer' })) { await load('lesson_participants', true); again(); } };
    $$('[data-p]').forEach(b => b.onclick = async () => { if (await editRecord('lesson_participants', F.lesson_participants, byId(parts, b.dataset.p), { editTitle: 'Teilnahme' })) { await load('lesson_participants', true); again(); } });
  };
  draw();
}
const pName = p => fullName(byId(App.cache.members, p.member_id)) || p.name || '?';

/* Hallen- und Platzbelegung: Wochenansicht je Ort, Buchung mit Prüfung auf Überschneidung im Server */
App.pages.arena = async (view) => {
  const rows = await load('arena_bookings', true);
  const groups = can('unterricht') ? await load('lesson_groups') : [];
  const ww = can('unterricht|stall|veranstaltungen', 2);
  const today = todayStr();
  let monday = App._arenaWeek || addDays(today, -((new Date(today + 'T12:00:00').getDay() + 6) % 7));
  const draw = () => {
    App._arenaWeek = monday;
    const days = [0, 1, 2, 3, 4, 5, 6].map(i => addDays(monday, i));
    const items = expandArena(rows, days[0], days[6]);
    const ps = places().length ? places() : [...new Set(rows.map(r => r.place).filter(Boolean))];
    // feste Reitstunden als Hinweis (nur Anzeige, belegen die Halle erst als eigene Buchung)
    const lessons = d => groups.filter(g => Number(g.active) !== 0 && g.weekday && Number(g.weekday) === ((new Date(d + 'T12:00:00').getDay() + 6) % 7) + 1
      && (!g.start_date || g.start_date <= d) && (!g.end_date || g.end_date >= d));
    const cls = k => ({ Sperrzeit: 'red', Unterricht: 'dark', Veranstaltung: 'warn', Pflege: '' }[k] ?? 'light');
    view.innerHTML = `
      <div class="page-head"><h1>Hallenbelegung<small>${fmtDate(days[0])} bis ${fmtDate(days[6])}</small></h1>
        <div class="btn-row"><button class="btn" id="ar_prev">‹ Woche</button><button class="btn" id="ar_today">Heute</button><button class="btn" id="ar_next">Woche ›</button>
          ${ww ? '<button class="btn primary" id="ar_new">+ Belegung</button>' : ''}</div></div>
      ${ps.length ? '' : '<div class="card" style="margin-bottom:12px">Noch keine Orte eingetragen (Einstellungen > Reitbetrieb > Orte).</div>'}
      <div class="arena-week">${days.map(d => `<div class="aw-day${d === today ? ' today' : ''}"><div class="aw-h"><b>${WD_S[new Date(d + 'T12:00:00').getDay()]}</b> ${fmtDate(d).slice(0, 6)}</div>
        ${items.filter(x => x.day === d).map(x => `<button class="aw-ev ${cls(x.kind)}" data-b="${x.id}" title="${esc(x.notes || '')}"><b>${esc(x.start || '')} bis ${esc(x.end || '')}</b>
          <span>${esc(x.place || '')}</span><small>${esc(x.kind || '')}${x.title ? ': ' + esc(x.title) : ''}${x.booked_by ? ' (' + esc(x.booked_by) + ')' : ''}${x.repeat_until ? ' ↻' : ''}</small></button>`).join('')}
        ${lessons(d).map(g => `<a class="aw-ev hint-ev" href="#/lessons/${g.id}"><b>${esc(g.time || '')}</b><span>${esc(g.place || '')}</span><small>Stunde: ${esc(g.name || '')}</small></a>`).join('')}
        </div>`).join('')}</div>
      <p class="hint">↻ = wöchentliche Wiederholung. Gestrichelt: feste Reitstunden laut Stundenplan (nur Hinweis, für die Hallenbelegung als eigene Belegung „Unterricht“ eintragen).
        Öffentlicher Hallenplan: ${App.settings.site_arena_public === '1' ? '<a href="/hallenplan" target="_blank" rel="noopener">an</a>' : 'aus (Webseite > Texte > Termine)'}.</p>`;
    $('#ar_prev').onclick = () => { monday = addDays(monday, -7); draw(); };
    $('#ar_next').onclick = () => { monday = addDays(monday, 7); draw(); };
    $('#ar_today').onclick = () => { monday = addDays(today, -((new Date(today + 'T12:00:00').getDay() + 6) % 7)); draw(); };
    const edit = async rec => { if (await editRecord('arena_bookings', F.arena_bookings, rec, { newTitle: 'Neue Belegung', editTitle: 'Belegung', allowWrite: ww })) App.pages.arena(view); };
    if ($('#ar_new')) $('#ar_new').onclick = () => edit({ date: today < monday ? monday : today, kind: 'Buchung', place: ps[0] || '', public: 1 });
    $$('[data-b]').forEach(b => b.onclick = () => edit(byId(rows, b.dataset.b)));
  };
  draw();
};
