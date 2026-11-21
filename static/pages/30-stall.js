/* ====================================================================== */
/* Stall: Pferde mit Gesundheit, Boxen, Einsteller */

/* Jüngster Eintrag je Pferd und Art (ältere sind durch einen neuen ersetzt), wie in notify_tick im Server */
function latestHealth(health) {
  const last = {};
  [...health].sort((a, b) => (a.date || '').localeCompare(b.date || '') || a.id - b.id).forEach(h => { last[h.horse_id + '|' + h.kind] = h; });
  return Object.values(last);
}

App.pages.horses = async (view, args) => {
  await loadAll('horses', 'boxes', 'members');
  const health = await load('horse_health', true);
  if (args[0]) return horseDetail(view, Number(args[0]), health);
  let kind = App._hKind || 'Alle', showInactive = false;
  const draw = () => {
    App._hKind = kind;
    const due = latestHealth(health).filter(h => h.next_due && daysUntil(h.next_due) <= 14);
    let list = App.cache.horses.filter(h => showInactive || Number(h.active) !== 0);
    if (kind !== 'Alle') list = list.filter(h => h.kind === kind);
    list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
    view.innerHTML = `
      <div class="page-head"><h1>Pferde<small>${App.cache.horses.filter(h => Number(h.active) !== 0).length} im Stall</small></h1>
        <div class="btn-row"><button class="btn" id="h_health">+ Gesundheit eintragen</button><button class="btn primary" id="h_new">+ Pferd</button></div></div>
      ${due.length ? `<div class="card" style="margin-bottom:16px"><h2>Fällig in den nächsten 14 Tagen</h2>
        ${due.sort((a, b) => a.next_due.localeCompare(b.next_due)).map(h => `<div class="sum-row"><span><a href="#/horses/${h.horse_id}"><b>${esc(byId(App.cache.horses, h.horse_id)?.name || '?')}</b></a>: ${esc(h.kind)}${h.details ? ` <span class="muted small">${esc(h.details)}</span>` : ''}</span>${dueBadge(h.next_due)}</div>`).join('')}</div>` : ''}
      <div class="toolbar">${['Alle', ...HORSE_KINDS].map(k => `<button class="btn sm ${k === kind ? 'dark' : ''}" data-k="${k}">${k}</button>`).join('')}
        <label class="check"><input type="checkbox" id="h_inact" ${showInactive ? 'checked' : ''}> inaktive zeigen</label></div>
      <div class="grid g3">${list.map(h => {
        const box = byId(App.cache.boxes, h.box_id);
        const next = latestHealth(health.filter(x => x.horse_id == h.id)).filter(x => x.next_due).sort((a, b) => a.next_due.localeCompare(b.next_due))[0];
        return `<a class="card horse-tile" href="#/horses/${h.id}" style="text-decoration:none;color:inherit;${Number(h.active) === 0 ? 'opacity:.55' : ''}">
          <div class="ht-img">${h.photo ? `<img src="${esc(h.photo)}" alt="" loading="lazy">` : tabIcon('horse')}</div>
          <div><b>${esc(h.name || '?')}</b> ${Number(h.public) ? '<span class="badge ok" title="auf der Webseite">Web</span>' : ''}
            <div class="muted small">${esc([h.kind, h.breed, h.birth_year].filter(Boolean).join(', '))}${box ? ' | ' + esc(box.name) : ''}</div>
            ${next ? `<div class="small" style="margin-top:4px">${esc(next.kind)}: ${dueBadge(next.next_due)}</div>` : ''}</div></a>`; }).join('') || '<div class="card empty">Noch keine Pferde eingetragen.</div>'}</div>`;
    $$('[data-k]').forEach(b => b.onclick = () => { kind = b.dataset.k; draw(); });
    $('#h_inact').onchange = e => { showInactive = e.target.checked; draw(); };
    $('#h_new').onclick = async () => { const r = await editRecord('horses', F.horses, { kind: 'Schulpferd', active: 1 }, { newTitle: 'Neues Pferd' }); if (r) { await load('horses', true); draw(); } };
    $('#h_health').onclick = async () => { if (await editRecord('horse_health', F.horse_health, { date: todayStr(), kind: 'Impfung' }, { newTitle: 'Gesundheit eintragen' })) App.pages.horses(view, []); };
  };
  draw();
};

async function horseDetail(view, id, health) {
  const h = byId(App.cache.horses, id);
  if (!h) { view.innerHTML = '<div class="card empty">Pferd nicht gefunden. <a href="#/horses">Zurück</a></div>'; return; }
  const box = byId(App.cache.boxes, h.box_id), owner = byId(App.cache.members, h.owner_member_id);
  const mine = health.filter(x => x.horse_id == id).sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  const current = latestHealth(mine);
  let contracts = [];
  try { contracts = (await load('boarding_contracts', true)).filter(c => c.horse_id == id); } catch { /* ohne Recht */ }
  view.innerHTML = `
    <div class="page-head"><h1><a href="#/horses" class="muted" style="text-decoration:none">Pferde</a> / ${esc(h.name)}<small>${esc([h.kind, h.breed, h.sex, h.birth_year].filter(Boolean).join(', '))}</small></h1>
      <div class="btn-row"><button class="btn" id="hd_edit">${App.readOnly ? 'Details' : 'Bearbeiten'}</button><button class="btn primary" id="hd_add">+ Gesundheit</button></div></div>
    <div class="grid g2">
      <div class="card">${h.photo ? `<img src="${esc(h.photo)}" alt="" style="width:100%;border-radius:10px;margin-bottom:12px">` : ''}
        ${[['Box', box ? box.name : ''], ['Besitzer', owner ? fullName(owner) : h.owner], ['Stockmaß', h.height], ['Farbe', h.color], ['Lebensnummer', h.ueln],
          ['Geeignet für', h.suited_for], ['Einheiten pro Tag', h.lessons_max], ['Webseite', Number(h.public) ? 'ja, unter Schulpferde' : 'nein']]
          .filter(x => x[1] !== undefined && x[1] !== null && x[1] !== '').map(([k, v]) => `<div class="sum-row"><span>${k}</span><b>${esc(v)}</b></div>`).join('')}
        ${h.feeding ? `<h3 style="margin-top:14px">Fütterung und Besonderheiten</h3><p style="white-space:pre-line">${esc(h.feeding)}</p>` : ''}
        ${h.notes ? `<h3>Notizen</h3><p style="white-space:pre-line">${esc(h.notes)}</p>` : ''}</div>
      <div class="card"><h2>Nächste Termine</h2>
        ${current.filter(x => x.next_due).sort((a, b) => a.next_due.localeCompare(b.next_due)).map(x => `<div class="sum-row"><span>${esc(x.kind)}</span>${dueBadge(x.next_due)}</div>`).join('') || '<p class="muted">Keine Termine eingetragen.</p>'}
        ${contracts.length ? `<h2 style="margin-top:16px">Einstellervertrag</h2>${contracts.map(c => `<div class="sum-row"><span>${esc(c.boarder || fullName(byId(App.cache.members, c.member_id)))}</span><span>${esc(c.status || '')}${c.end ? ', bis ' + fmtDate(c.end) : ''}</span></div>`).join('')}` : ''}</div>
    </div>
    <h2 style="margin-top:20px">Gesundheit</h2>
    ${simpleTable(['Datum', 'Art', 'Details', 'Von', 'Nächster Termin'], mine.map(x => ({ id: x.id, cells: [fmtDate(x.date), esc(x.kind || ''), esc(x.details || ''), esc(x.by || ''),
      x.next_due ? (current.includes(x) ? dueBadge(x.next_due) : `<span class="muted small">${fmtDate(x.next_due)} (ersetzt)</span>`) : ''] })), 'Noch keine Einträge.', 'data-hh')}`;
  const again = () => App.pages.horses(view, [String(id)]);
  $('#hd_edit').onclick = async () => { const r = await editRecord('horses', F.horses, h, { editTitle: h.name }); if (r) { await load('horses', true); r.deleted ? go('#/horses') : again(); } };
  $('#hd_add').onclick = async () => { if (await editRecord('horse_health', F.horse_health, { horse_id: id, date: todayStr(), kind: 'Impfung' }, { newTitle: `Gesundheit: ${h.name}` })) again(); };
  $$('[data-hh]').forEach(b => b.onclick = async () => { if (await editRecord('horse_health', F.horse_health, byId(mine, b.dataset.hh), { editTitle: 'Eintrag' })) again(); });
}

App.pages.boxes = async (view) => {
  await loadAll('horses');
  const boxes = await load('boxes', true);
  const list = [...boxes].sort((a, b) => (a.location || '').localeCompare(b.location || '') || (a.name || '').localeCompare(b.name || '', 'de', { numeric: true }));
  const free = list.filter(b => Number(b.active) !== 0 && !b.horse_id).length;
  view.innerHTML = `
    <div class="page-head"><h1>Boxen<small>${list.length} Plätze, ${free} frei</small></h1><div class="btn-row"><button class="btn primary" id="b_new">+ Box</button></div></div>
    ${simpleTable(['Box', 'Art', 'Stallgasse', 'Pferd', ...(can('finanzen') ? ['Miete'] : [])], list.map(b => ({ id: b.id, cells: [
      `<b>${esc(b.name || '?')}</b>${Number(b.active) === 0 ? ' <span class="badge">außer Nutzung</span>' : ''}`, esc(b.kind || ''), esc(b.location || ''),
      b.horse_id ? `<a href="#/horses/${b.horse_id}">${esc(byId(App.cache.horses, b.horse_id)?.name || '?')}</a>` : badge('frei', 'ok'),
      ...(can('finanzen') ? [b.rent ? eur(b.rent) : ''] : [])] })), 'Noch keine Boxen angelegt.', 'data-b')}`;
  const edit = async rec => { if (await editRecord('boxes', F.boxes, rec, { newTitle: 'Neue Box', editTitle: 'Box' })) { invalidate('horses'); App.pages.boxes(view); } };
  $('#b_new').onclick = () => edit({ kind: 'Box', active: 1 });
  $$('[data-b]').forEach(b => b.onclick = () => edit(byId(boxes, b.dataset.b)));
};

App.pages.boarding = async (view) => {
  await loadAll('horses', 'boxes', 'members');
  const list = await load('boarding_contracts', true);
  let st = App._bdSt || 'aktuell';
  const draw = () => {
    App._bdSt = st;
    const rows = list.filter(c => st === 'alle' || (st === 'aktuell' ? ['Angefragt', 'Aktiv', 'Gekündigt'].includes(c.status) : c.status === st))
      .sort((a, b) => (a.status || '').localeCompare(b.status || '') || (a.end || '9').localeCompare(b.end || '9'));
    const rent = list.filter(c => c.status === 'Aktiv').reduce((a, c) => a + (Number(c.rent) || 0), 0);
    view.innerHTML = `
      <div class="page-head"><h1>Einsteller<small>${list.filter(c => c.status === 'Aktiv').length} aktive Verträge${rent ? ', ' + eur(rent) + ' Miete im Monat' : ''}</small></h1>
        <div class="btn-row"><button class="btn keep" id="bd_csv">CSV exportieren</button><button class="btn primary" id="bd_new">+ Vertrag</button></div></div>
      <div class="toolbar">${['aktuell', 'Angefragt', 'Aktiv', 'Gekündigt', 'Beendet', 'alle'].map(k => `<button class="btn sm ${k === st ? 'dark' : ''}" data-s="${k}">${k === 'aktuell' ? 'Aktuelle' : k === 'alle' ? 'Alle' : k}</button>`).join('')}</div>
      ${simpleTable(['Pferd', 'Einsteller', 'Box', 'Beginn', 'Ende', 'Miete', 'Status'], rows.map(c => ({ id: c.id, cells: [
        c.horse_id ? `<a href="#/horses/${c.horse_id}">${esc(byId(App.cache.horses, c.horse_id)?.name || '?')}</a>` : '',
        esc(c.boarder || fullName(byId(App.cache.members, c.member_id))), esc(byId(App.cache.boxes, c.box_id)?.name || ''), fmtDate(c.start),
        c.end ? (c.status === 'Aktiv' || c.status === 'Gekündigt' ? dueBadge(c.end) : fmtDate(c.end)) : '', c.rent ? eur(c.rent) : '', badge(c.status || '', c.status === 'Aktiv' ? 'ok' : c.status === 'Angefragt' ? 'warn' : '')] })),
        'Keine Verträge in dieser Ansicht.', 'data-bd')}`;
    $$('[data-s]').forEach(b => b.onclick = () => { st = b.dataset.s; draw(); });
    const edit = async rec => { if (await editRecord('boarding_contracts', F.boarding_contracts, rec, { newTitle: 'Neuer Einstellervertrag', editTitle: 'Einstellervertrag' })) App.pages.boarding(view); };
    $('#bd_new').onclick = () => edit({ status: 'Aktiv', start: todayStr() });
    $$('[data-bd]').forEach(b => b.onclick = () => edit(byId(list, b.dataset.bd)));
    $('#bd_csv').onclick = () => downloadCsv(`einsteller-${todayStr()}.csv`, ['Pferd', 'Einsteller', 'E-Mail', 'Telefon', 'Box', 'Beginn', 'Ende', 'Miete', 'Status'],
      rows.map(c => [byId(App.cache.horses, c.horse_id)?.name || '', c.boarder || fullName(byId(App.cache.members, c.member_id)), c.email, c.phone, byId(App.cache.boxes, c.box_id)?.name || '',
        fmtDate(c.start), fmtDate(c.end), String(c.rent ?? '').replace('.', ','), c.status]));
  };
  draw();
};
