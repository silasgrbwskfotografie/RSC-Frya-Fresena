/* ====================================================================== */
/* Mitglieder */
function ageOf(birth, at = todayStr()) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(birth || '')) return null;
  let a = Number(at.slice(0, 4)) - Number(birth.slice(0, 4));
  if (at.slice(5) < birth.slice(5)) a--;
  return a;
}

App.pages.members = async (view) => {
  await loadAll('members', 'fee_types');
  const users = App.me.is_admin ? await api('admin/users') : [];
  let st = App._mStatus || 'aktuell', q = '';
  const draw = () => {
    App._mStatus = st;
    let list = [...App.cache.members];
    if (st === 'aktuell') list = list.filter(m => !['Ausgetreten', 'Warteliste'].includes(m.status));
    else if (st !== 'alle') list = list.filter(m => m.status === st);
    if (q) list = list.filter(m => (fullName(m) + ' ' + (m.email || '') + ' ' + (m.phone || '') + ' ' + (m.family || '')).toLowerCase().includes(q));
    list.sort((a, b) => (a.last_name || '').localeCompare(b.last_name || '') || (a.first_name || '').localeCompare(b.first_name || ''));
    const counts = MEMBER_STATUS.map(s => [s, App.cache.members.filter(m => m.status === s).length]);
    const userOf = m => users.find(u => u.member_id == m.id);
    view.innerHTML = `
      <div class="page-head"><h1>Mitglieder<small>${counts.filter(c => c[1]).map(c => `${c[1]} ${c[0].toLowerCase()}`).join(', ') || 'noch keine Einträge'}</small></h1>
        <div class="btn-row"><button class="btn keep" id="m_csv">CSV exportieren</button><button class="btn primary" id="m_new">+ Mitglied</button></div></div>
      <div class="toolbar">
        ${[['aktuell', 'Aktuelle'], ...MEMBER_STATUS.map(s => [s, s]), ['alle', 'Alle']].map(([k, l]) => `<button class="btn sm ${k === st ? 'dark' : ''}" data-ms="${k}">${l}</button>`).join('')}
        <input class="input" id="m_q" placeholder="Suchen" style="max-width:220px" value="${esc(q)}">
      </div>
      <div class="table-wrap"><table><thead><tr><th>Name</th><th>Status</th><th>Beitragsart</th><th>Alter</th><th>Telefon</th><th>E-Mail</th><th>Foto</th>${App.me.is_admin ? '<th>Zugang</th>' : ''}<th></th></tr></thead><tbody>
      ${list.map(m => { const u = userOf(m); const ft = byId(App.cache.fee_types, m.fee_type_id); const age = ageOf(m.birthdate); return `<tr style="${m.status === 'Ausgetreten' ? 'opacity:.55' : ''}">
        <td><b>${esc(fullName(m))}</b>${m.family ? ` <span class="muted small">Familie ${esc(m.family)}</span>` : ''}</td>
        <td><span class="badge ${m.status === 'Aktiv' ? 'ok' : ['Probezeit', 'Warteliste'].includes(m.status) ? 'warn' : ''}">${esc(m.status || '')}</span></td>
        <td class="small">${esc(ft?.name || '')}</td>
        <td class="small">${age ?? ''}</td>
        <td class="small">${m.phone ? `<a href="tel:${esc(m.phone.replace(/\s/g, ''))}">${esc(m.phone)}</a>` : ''}</td>
        <td class="small">${m.email ? `<a href="mailto:${esc(m.email)}">${esc(m.email)}</a>` : ''}</td>
        <td>${Number(m.consent_photo) ? '<span class="badge ok">ja</span>' : '<span class="badge warn">fehlt</span>'}</td>
        ${App.me.is_admin ? `<td>${u ? `<span class="badge ${u.invite_open ? 'warn' : 'ok'}">${u.invite_open ? 'eingeladen' : esc(u.username)}</span>` : `<button class="btn sm keep" data-acc="${m.id}">Zugang anlegen</button>`}</td>` : ''}
        <td class="actions"><button class="btn sm ghost" data-e="${m.id}">${App.readOnly ? 'Ansehen' : 'Bearbeiten'}</button></td></tr>`; }).join('')
        || `<tr><td colspan="9" class="empty">Keine Mitglieder in dieser Ansicht.</td></tr>`}
      </tbody></table></div>`;
    $$('[data-ms]').forEach(b => b.onclick = () => { st = b.dataset.ms; draw(); });
    $('#m_q').oninput = e => { q = e.target.value.toLowerCase(); const pos = e.target.selectionStart; draw(); const el = $('#m_q'); el.focus(); el.setSelectionRange(pos, pos); };
    const edit = async rec => { const r = await editRecord('members', F.members, rec, { newTitle: 'Neues Mitglied', editTitle: 'Mitglied' }); if (r) { await load('members', true); draw(); } };
    $('#m_new').onclick = () => edit({ status: 'Aktiv', member_since: todayStr(), payment: 'Lastschrift' });
    $$('[data-e]').forEach(b => b.onclick = () => edit(byId(App.cache.members, b.dataset.e)));
    $$('[data-acc]').forEach(b => b.onclick = () => accountDialog(null, byId(App.cache.members, b.dataset.acc)));
    $('#m_csv').onclick = () => downloadCsv(`mitglieder-${todayStr()}.csv`,
      ['Vorname', 'Nachname', 'Status', 'Beitragsart', 'Familie', 'Geburtsdatum', 'E-Mail', 'Telefon', 'Straße', 'PLZ', 'Ort', 'Erziehungsberechtigte', 'Mitglied seit', 'Zahlweise', 'Mandatsreferenz', 'Foto-Einwilligung'],
      list.map(m => [m.first_name, m.last_name, m.status, byId(App.cache.fee_types, m.fee_type_id)?.name || '', m.family, fmtDate(m.birthdate), m.email, m.phone, m.street, m.zip, m.city,
        m.guardian, fmtDate(m.member_since), m.payment, m.mandate_ref, Number(m.consent_photo) ? 'ja' : 'nein']));
  };
  draw();
};

/* ====================================================================== */
/* Beiträge: Beitragsarten und Forderungen je Mitglied */
App.pages.fees = async (view) => {
  await loadAll('fee_types', 'members');
  const payments = await load('fee_payments', true);
  const ww = can('mitglieder', 2) || can('finanzen', 2);
  const periods = [...new Set(payments.map(p => p.period).filter(Boolean))].sort().reverse();
  let tab = App._feeTab || 'offen', period = App._feePeriod || periods[0] || '';
  const draw = () => {
    App._feeTab = tab; App._feePeriod = period;
    const today = todayStr();
    let list = payments.filter(p => !period || p.period === period);
    if (tab === 'offen') list = list.filter(p => !Number(p.paid));
    if (tab === 'bezahlt') list = list.filter(p => Number(p.paid));
    const sum = arr => arr.reduce((a, p) => a + (Number(p.amount) || 0), 0);
    const inP = payments.filter(p => !period || p.period === period);
    const open = inP.filter(p => !Number(p.paid)), overdue = open.filter(p => p.due && p.due < today);
    const types = [...App.cache.fee_types].sort((a, b) => (Number(a.sort) || 0) - (Number(b.sort) || 0) || (a.name || '').localeCompare(b.name || ''));
    view.innerHTML = `
      <div class="page-head"><h1>Beiträge<small>Beitragsarten und Mitgliedsbeiträge</small></h1>
        <div class="btn-row"><button class="btn keep" id="fe_csv">CSV exportieren</button>${ww ? '<button class="btn" id="fe_gen">Beiträge anlegen</button>' : ''}
          ${can('finanzen', 2) ? '<button class="btn" id="fe_book">Bezahlte buchen</button>' : ''}${ww ? '<button class="btn primary" id="fe_new">+ Einzelbeitrag</button>' : ''}</div></div>
      <div class="grid g4" style="margin-bottom:16px">
        <div class="card stat"><span>Forderungen ${esc(period || 'gesamt')}</span><b>${eur(sum(inP))}</b></div>
        <div class="card stat"><span>Bezahlt</span><b style="color:var(--ok)">${eur(sum(inP) - sum(open))}</b></div>
        <div class="card stat"><span>Offen</span><b>${eur(sum(open))}</b></div>
        <div class="card stat"><span>Überfällig</span><b style="color:${overdue.length ? 'var(--red-text)' : 'inherit'}">${overdue.length}</b></div>
      </div>
      <div class="card" style="margin-bottom:16px"><div class="page-head" style="margin:0 0 8px"><h2 style="margin:0">Beitragsarten</h2>${ww ? '<button class="btn sm" id="ft_new">+ Beitragsart</button>' : ''}</div>
        ${simpleTable(['Name', 'Betrag', 'Aufnahme', 'Alter', 'Familie', 'Webseite', 'Mitglieder'], types.map(t => ({ id: t.id, cells: [
          `<b>${esc(t.name)}</b>${Number(t.active) ? '' : ' <span class="badge">inaktiv</span>'}`, t.amount != null && t.amount !== '' ? `${eur(t.amount)} ${esc(t.interval || '')}` : '',
          t.admission_fee ? eur(t.admission_fee) : '', [t.age_from, t.age_to].some(x => x != null && x !== '') ? `${t.age_from ?? ''} bis ${t.age_to ?? ''}` : '',
          Number(t.per_family) ? 'ja' : '', Number(t.public) ? '<span class="badge ok">ja</span>' : '', App.cache.members.filter(m => m.fee_type_id == t.id && m.status !== 'Ausgetreten').length] })),
          'Noch keine Beitragsarten. Lege zuerst z. B. Kinder, Jugend, Erwachsene, Familie und passiv an.', 'data-ft')}</div>
      <div class="toolbar">${['offen', 'bezahlt', 'alle'].map(k => `<button class="btn sm ${k === tab ? 'dark' : ''}" data-t="${k}">${k[0].toUpperCase() + k.slice(1)}</button>`).join('')}
        <select class="input" id="fe_period" style="width:auto"><option value="">Alle Zeiträume</option>${periods.map(p => `<option ${p === period ? 'selected' : ''}>${esc(p)}</option>`).join('')}</select></div>
      ${simpleTable(['Mitglied', 'Zeitraum', 'Betrag', 'Fällig', 'Status'], [...list].sort((a, b) => (a.due || '').localeCompare(b.due || '')).map(p => ({ id: p.id, cells: [
        `<b>${esc(fullName(byId(App.cache.members, p.member_id)) || '(gelöscht)')}</b>`, esc(p.period || ''), eur(p.amount), fmtDate(p.due),
        Number(p.paid) ? `<span class="badge ok">bezahlt ${fmtDate(p.paid_on)}</span>${p.booked_tx ? ' <span class="badge">gebucht</span>' : ''}` : (p.due && p.due < today ? badge('überfällig', 'red') : badge('offen', 'warn'))
          + (ww ? ` <button class="btn sm keep" data-paid="${p.id}">bezahlt</button>` : '')] })),
        'Keine Beiträge in dieser Ansicht.', 'data-fp')}`;
    $$('[data-t]').forEach(b => b.onclick = () => { tab = b.dataset.t; draw(); });
    $('#fe_period').onchange = e => { period = e.target.value; draw(); };
    const reload = async () => { await load('fee_payments', true); App.pages.fees(view); };
    $$('[data-ft]').forEach(b => b.onclick = async () => { if (await editRecord('fee_types', F.fee_types, byId(App.cache.fee_types, b.dataset.ft), { editTitle: 'Beitragsart' })) { await load('fee_types', true); draw(); } });
    $$('[data-fp]').forEach(b => b.onclick = async () => { if (await editRecord('fee_payments', F.fee_payments, byId(payments, b.dataset.fp), { editTitle: 'Beitrag', allowWrite: ww })) reload(); });
    $$('[data-paid]').forEach(b => b.onclick = async () => { await api(`fee_payments/${b.dataset.paid}`, 'PUT', { paid: 1, paid_on: todayStr() }); toast('Als bezahlt markiert'); reload(); });
    if ($('#ft_new')) $('#ft_new').onclick = async () => { if (await editRecord('fee_types', F.fee_types, { interval: 'jährlich', public: 1, active: 1 }, { newTitle: 'Neue Beitragsart' })) { await load('fee_types', true); draw(); } };
    if ($('#fe_new')) $('#fe_new').onclick = async () => { if (await editRecord('fee_payments', F.fee_payments, { period: period || String(new Date().getFullYear()) }, { newTitle: 'Neuer Beitrag', allowWrite: true })) reload(); };
    if ($('#fe_gen')) $('#fe_gen').onclick = async () => {
      const p = prompt('Für welchen Zeitraum sollen die Beiträge angelegt werden? (z. B. 2027 oder 2027 H1)', String(new Date().getFullYear()));
      if (!p) return;
      const due = prompt('Fällig am (JJJJ-MM-TT, leer = ohne Datum)', `${p.slice(0, 4)}-03-01`);
      if (due === null) return;
      try { const r = await api('fees/generate', 'POST', { period: p.trim(), due: due.trim() }); period = p.trim(); toast(`${r.created} Beiträge angelegt`); reload(); } catch (e) { alert(e.message); }
    };
    if ($('#fe_book')) $('#fe_book').onclick = async () => {
      if (!confirmBox('Alle bezahlten, noch nicht gebuchten Beiträge als eine Einnahme in den Finanzen buchen?')) return;
      try { const r = await api('fees/book', 'POST', {}); toast(`${r.count} Beiträge gebucht: ${eur(r.amount)}`); invalidate('transactions'); reload(); } catch (e) { alert(e.message); }
    };
    $('#fe_csv').onclick = () => downloadCsv(`beitraege-${period || 'alle'}.csv`, ['Mitglied', 'Zeitraum', 'Betrag', 'Fällig', 'Bezahlt', 'Bezahlt am', 'Mandatsreferenz'],
      list.map(p => { const m = byId(App.cache.members, p.member_id); return [fullName(m), p.period, String(Number(p.amount) || 0).replace('.', ','), fmtDate(p.due), Number(p.paid) ? 'ja' : 'nein', fmtDate(p.paid_on), m?.mandate_ref || '']; }));
  };
  draw();
};

/* ====================================================================== */
/* Arbeits- und Stalldienste mit Pflichtstunden */
App.pages.duties = async (view) => {
  await loadAll('members');
  const duties = await load('duties', true);
  const ww = can('mitglieder|stall|veranstaltungen', 2);
  const year = App._dutyYear || String(new Date().getFullYear());
  let tab = App._dutyTab || 'plan';
  const draw = () => {
    App._dutyTab = tab;
    const inYear = duties.filter(d => (d.date || '').startsWith(year));
    const def = Number(App.settings.work_hours_default) || 0;
    const done = {};
    inYear.filter(d => d.status === 'erledigt' && Number(d.confirmed) && d.member_id).forEach(d => { done[d.member_id] = (done[d.member_id] || 0) + (Number(d.hours) || 0); });
    const active = App.cache.members.filter(m => ['Aktiv', 'Probezeit'].includes(m.status));
    const hours = active.map(m => ({ m, due: m.work_hours_due != null && m.work_hours_due !== '' ? Number(m.work_hours_due) : def, done: done[m.id] || 0 }))
      .sort((a, b) => (a.done - a.due) - (b.done - b.due));
    const upcoming = duties.filter(d => (d.date || '') >= todayStr() && d.status !== 'abgesagt').sort((a, b) => (a.date || '').localeCompare(b.date || '') || (a.start || '').localeCompare(b.start || ''));
    const who = d => esc(fullName(byId(App.cache.members, d.member_id)) || d.name || '') || '<span class="badge warn">offen</span>';
    view.innerHTML = `
      <div class="page-head"><h1>Arbeitsdienste<small>Stall- und Arbeitsdienste, Pflichtstunden ${esc(year)}</small></h1>
        <div class="btn-row"><button class="btn keep" id="du_csv">CSV exportieren</button>${ww ? '<button class="btn primary" id="du_new">+ Dienst</button>' : ''}</div></div>
      <div class="toolbar">${[['plan', 'Geplant'], ['stunden', 'Stundenkonto'], ['alle', 'Alle Einträge']].map(([k, l]) => `<button class="btn sm ${k === tab ? 'dark' : ''}" data-t="${k}">${l}</button>`).join('')}
        <input class="input" id="du_year" value="${esc(year)}" style="width:90px" aria-label="Jahr"></div>
      ${tab === 'plan' ? simpleTable(['Datum', 'Zeit', 'Art', 'Aufgabe', 'Wer'], upcoming.map(d => ({ id: d.id, cells: [fmtDate(d.date, true), [d.start, d.end].filter(Boolean).join(' bis '), esc(d.kind || ''), esc(d.task || ''), who(d)] })),
        'Keine geplanten Dienste.', 'data-du')
      : tab === 'stunden' ? `<div class="table-wrap"><table><thead><tr><th>Mitglied</th><th>Soll</th><th>Geleistet (bestätigt)</th><th>Offen</th></tr></thead><tbody>
          ${hours.map(h => `<tr><td><b>${esc(fullName(h.m))}</b></td><td>${h.due || '-'}</td><td>${h.done}</td><td>${h.due ? (h.done >= h.due ? badge('erfüllt', 'ok') : badge(String(h.due - h.done), 'warn')) : ''}</td></tr>`).join('')
            || '<tr><td colspan="4" class="empty">Keine aktiven Mitglieder.</td></tr>'}</tbody></table></div>
          <p class="hint">Standard-Pflichtstunden: ${def ? def + ' je Jahr' : 'keine (Einstellungen > Reitbetrieb)'}. Abweichungen je Mitglied unter Mitglieder.</p>`
      : simpleTable(['Datum', 'Art', 'Aufgabe', 'Wer', 'Stunden', 'Status'], [...inYear].sort((a, b) => (b.date || '').localeCompare(a.date || '')).map(d => ({ id: d.id, cells: [fmtDate(d.date), esc(d.kind || ''), esc(d.task || ''), who(d),
          d.hours ?? '', esc(d.status || '') + (Number(d.confirmed) ? ' ' + badge('bestätigt', 'ok') : '')] })), 'Keine Einträge im Jahr.', 'data-du')}`;
    $$('[data-t]').forEach(b => b.onclick = () => { tab = b.dataset.t; draw(); });
    $('#du_year').onchange = e => { App._dutyYear = e.target.value.trim(); draw(); };
    const edit = async rec => { if (await editRecord('duties', F.duties, rec, { newTitle: 'Neuer Dienst', editTitle: 'Dienst', allowWrite: ww })) App.pages.duties(view); };
    $$('[data-du]').forEach(b => b.onclick = () => edit(byId(duties, b.dataset.du)));
    if ($('#du_new')) $('#du_new').onclick = () => edit({ date: todayStr(), status: 'geplant', kind: 'Stalldienst' });
    $('#du_csv').onclick = () => downloadCsv(`dienste-${year}.csv`, ['Datum', 'Beginn', 'Ende', 'Art', 'Aufgabe', 'Wer', 'Stunden', 'Status', 'Bestätigt'],
      inYear.map(d => [fmtDate(d.date), d.start, d.end, d.kind, d.task, fullName(byId(App.cache.members, d.member_id)) || d.name, String(d.hours ?? '').replace('.', ','), d.status, Number(d.confirmed) ? 'ja' : 'nein']));
  };
  draw();
};

/* ====================================================================== */
/* Finanzen */
const TX_CATEGORIES = ['Mitgliedsbeiträge', 'Aufnahmegebühren', 'Reitunterricht', 'Boxenmiete', 'Turniere und Lehrgänge', 'Spenden', 'Zuschüsse', 'Futter und Einstreu',
  'Tierarzt und Hufschmied', 'Anlage und Unterhalt', 'Pferdekauf', 'Verband', 'Versicherung', 'Veranstaltungen', 'Sonstiges'];
F.transactions = [
  { key: 'date', label: 'Datum', type: 'date', default: todayStr() },
  { key: 'kind', label: 'Art', type: 'select', options: ['Ausgabe', 'Einnahme'] },
  { key: 'amount', label: 'Betrag (€)', type: 'number' },
  { key: 'category', label: 'Kategorie', list: TX_CATEGORIES },
  { key: 'description', label: 'Beschreibung', full: true },
  { key: 'status', label: 'Status', type: 'select', options: ['Erledigt', 'Offen'], hint: 'Offen = noch nicht bezahlt bzw. noch nicht eingegangen' },
  { key: 'paid_by', label: 'Bezahlt von / an' },
  { key: 'member_id', label: 'Mitglied', type: 'ref', ref: { table: 'members', label: m => fullName(m) } },
  { key: 'receipt', label: 'Beleg (Foto oder PDF)', type: 'file', full: true },
  { key: 'notes', label: 'Notizen', type: 'textarea', rows: 2 },
];

App.pages.finance = async (view) => {
  await loadAll('transactions', 'members');
  const years = [...new Set([String(new Date().getFullYear()), ...App.cache.transactions.map(t => (t.date || '').slice(0, 4)).filter(Boolean)])].sort().reverse();
  let year = App._finYear || years[0], kind = 'Alle', cat = 'Alle';
  const draw = () => {
    App._finYear = year;
    const inYear = App.cache.transactions.filter(t => year === 'alle' || (t.date || '').startsWith(year));
    let list = inYear;
    if (kind !== 'Alle') list = list.filter(t => t.kind === kind || (kind === 'Offen' && t.status === 'Offen'));
    if (cat !== 'Alle') list = list.filter(t => t.category === cat);
    list = [...list].sort((a, b) => (b.date || '').localeCompare(a.date || '') || b.id - a.id);
    const sum = (arr, k) => arr.filter(t => t.kind === k).reduce((a, t) => a + (Number(t.amount) || 0), 0);
    const inc = sum(inYear, 'Einnahme'), exp = sum(inYear, 'Ausgabe');
    const cats = {};
    inYear.forEach(t => { const k = (t.category || 'Ohne Kategorie'); cats[k] = cats[k] || { inc: 0, exp: 0 }; cats[k][t.kind === 'Einnahme' ? 'inc' : 'exp'] += Number(t.amount) || 0; });
    view.innerHTML = `
      <div class="page-head"><h1>Finanzen<small>Einnahmen und Ausgaben ${year === 'alle' ? 'gesamt' : year}</small></h1>
        <div class="btn-row"><button class="btn keep" id="f_csv">CSV exportieren</button><button class="btn primary" id="f_new">+ Buchung</button></div></div>
      <div class="toolbar">${[...years, 'alle'].map(y => `<button class="btn sm ${y === year ? 'dark' : ''}" data-y="${y}">${y === 'alle' ? 'Alle Jahre' : y}</button>`).join('')}</div>
      <div class="grid g4" style="margin-bottom:16px">
        <div class="card stat"><span>Einnahmen</span><b style="color:var(--ok)">${eur(inc)}</b></div>
        <div class="card stat"><span>Ausgaben</span><b>${eur(exp)}</b></div>
        <div class="card stat"><span>Saldo</span><b style="color:${inc - exp < 0 ? 'var(--red-text)' : 'var(--ok)'}">${eur(inc - exp)}</b></div>
        <div class="card stat"><span>Offen</span><b>${inYear.filter(t => t.status === 'Offen').length}</b></div>
      </div>
      <div class="card" style="margin-bottom:16px"><h2>Nach Kategorie</h2>
        ${Object.keys(cats).length ? Object.entries(cats).sort((a, b) => (b[1].inc + b[1].exp) - (a[1].inc + a[1].exp)).map(([k, v]) =>
          `<div class="sum-row"><span>${esc(k)}</span><span>${v.inc ? `<b style="color:var(--ok)">+${eur(v.inc)}</b> ` : ''}${v.exp ? `<b>-${eur(v.exp)}</b>` : ''}</span></div>`).join('') : '<div class="muted">Noch keine Buchungen.</div>'}
      </div>
      <div class="toolbar">
        ${['Alle', 'Einnahme', 'Ausgabe', 'Offen'].map(k => `<button class="btn sm ${k === kind ? 'dark' : ''}" data-k="${k}">${k === 'Alle' ? 'Alle' : k === 'Offen' ? 'Offen' : k + 'n'}</button>`).join('')}
        <select class="input" id="f_cat" style="width:auto">${['Alle', ...new Set([...TX_CATEGORIES, ...App.cache.transactions.map(t => t.category).filter(Boolean)])].map(c => `<option ${c === cat ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Datum</th><th>Beschreibung</th><th>Kategorie</th><th>Bezahlt von / an</th><th style="text-align:right">Betrag</th><th>Status</th><th>Beleg</th><th></th></tr></thead><tbody>
      ${list.map(t => `<tr>
        <td class="small" style="white-space:nowrap">${fmtDate(t.date)}</td>
        <td><b>${esc(t.description || '')}</b></td>
        <td class="small">${esc(t.category || '')}</td>
        <td class="small">${esc(t.paid_by || (t.member_id ? fullName(byId(App.cache.members, t.member_id)) : ''))}</td>
        <td style="text-align:right;white-space:nowrap"><b style="color:${t.kind === 'Einnahme' ? 'var(--ok)' : 'inherit'}">${t.kind === 'Einnahme' ? '+' : '-'}${eur(t.amount)}</b></td>
        <td>${t.status === 'Offen' ? '<span class="badge warn">offen</span>' : '<span class="badge ok">erledigt</span>'}</td>
        <td>${t.receipt ? `<a class="btn sm keep" href="${esc(t.receipt)}" target="_blank" rel="noopener">Ansehen</a>` : ''}</td>
        <td class="actions"><button class="btn sm ghost" data-e="${t.id}">Bearbeiten</button></td></tr>`).join('')
        || '<tr><td colspan="8" class="empty">Keine Buchungen in dieser Ansicht.</td></tr>'}
      </tbody></table></div>`;
    $$('[data-y]').forEach(b => b.onclick = () => { year = b.dataset.y; draw(); });
    $$('[data-k]').forEach(b => b.onclick = () => { kind = b.dataset.k; draw(); });
    $('#f_cat').onchange = e => { cat = e.target.value; draw(); };
    const edit = async rec => { const r = await editRecord('transactions', F.transactions, rec, { newTitle: 'Neue Buchung', editTitle: 'Buchung bearbeiten' }); if (r) { await load('transactions', true); draw(); } };
    $('#f_new').onclick = () => edit({ date: todayStr(), kind: 'Ausgabe', status: 'Erledigt' });
    $$('[data-e]').forEach(b => b.onclick = () => edit(byId(App.cache.transactions, b.dataset.e)));
    $('#f_csv').onclick = () => downloadCsv(`buchungen-${year}.csv`, ['Datum', 'Art', 'Betrag', 'Kategorie', 'Beschreibung', 'Bezahlt von / an', 'Status', 'Beleg'],
      list.map(t => [fmtDate(t.date), t.kind, String(Number(t.amount) || 0).replace('.', ','), t.category, t.description, t.paid_by, t.status, t.receipt ? location.origin + t.receipt : '']));
  };
  draw();
};
