/* ====================================================================== */
/* Übersicht: Widgets nach Rechten, Auswahl und Reihenfolge je Konto (users.dash_layout) */
App.pages.dashboard = async (view) => {
  const dash = await api('dashboard');
  const me = App.me;
  const c = dash.counts || {};
  const stall = can('stall'), unterricht = can('unterricht'), mitglieder = can('mitglieder'), medien = can('medien');
  const belegung = can('unterricht|stall|veranstaltungen');
  const contactsOk = can('mitglieder|verein|unterricht|stall');

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
  const arrow = '<svg viewBox="0 0 24 24"><path d="M7 17 17 7M9 7h8v8"/></svg>';
  const goBtn = (href, label) => `<a class="dash-go" href="${href}" aria-label="${label}">${arrow}</a>`;
  const rows = list => list.map(([l, v, st]) => `<div class="sum-row"><span>${l}</span><b${st ? ` style="${st}"` : ''}>${v}</b></div>`).join('');
  const loading = '<p class="muted small">Lädt ...</p>';
  const nextEv = (dash.next_events || [])[0];
  const days = nextEv ? daysUntil(nextEv.date) : null;

  const pills = [
    belegung ? ['Halle heute', `${(dash.arena_today || []).length} Belegung${(dash.arena_today || []).length === 1 ? '' : 'en'}`, 'b1'] : null,
    stall ? ['Pferde', `${(dash.health_due || []).length} Termin${(dash.health_due || []).length === 1 ? '' : 'e'} in 14 Tagen`, 'b2'] : null,
    nextEv ? ['Nächster Termin', days === 0 ? 'heute' : days === 1 ? 'morgen' : `in ${days} Tagen`, 'b3'] : null,
    ['Hinweise', `${notes.length} offen`, 'b4'],
  ].filter(Boolean);
  const bigs = [
    c.members !== undefined ? ['team', c.members, 'Mitglieder'] : null,
    c.horses !== undefined ? ['horse', c.horses, 'Pferde'] : null,
    c.lessons !== undefined ? ['star', c.lessons, 'Stunden und Kurse'] : null,
    c.boxes_free !== undefined ? ['box', c.boxes_free, 'Freie Boxen'] : null,
  ].filter(Boolean).slice(0, 3);

  const W = {
    events: { t: 'Nächste Termine', w: 1, html: () => `<h3>Nächste Termine</h3>${goBtn('#/events', 'Zu den Veranstaltungen')}
      ${(dash.next_events || []).length ? `<div class="list" style="margin-top:12px">${dash.next_events.slice(0, 5).map(e => `<a class="list-item" href="#/events/${e.id}" style="text-decoration:none;color:inherit">${dateBox(e.date)}
        <div class="grow"><b>${esc(e.title)}</b><div class="muted small">${esc([e.kind, e.time, e.place].filter(Boolean).join(' | '))}</div></div></a>`).join('')}</div>` : '<p class="muted">Keine Termine geplant.</p>'}` },
    countdown: { t: 'Bis zum nächsten Termin', w: 1, html: () => {
      const pct = days === null ? 0 : Math.max(4, Math.min(100, Math.round((30 - Math.min(days, 30)) / 30 * 100)));
      return `<h3>Nächster Termin</h3>${goBtn('#/events', 'Zu den Veranstaltungen')}
        <div class="dash-ring" style="--p:${pct}%"><div><b>${days === null ? '-' : days === 0 ? 'Heute' : days}</b><small>${days === null ? 'kein Termin' : days === 0 ? esc(nextEv.kind || '') : days === 1 ? 'Tag' : 'Tage'}</small></div></div>
        <div class="muted small" style="text-align:center">${nextEv ? esc(nextEv.title) + ', ' + fmtDate(nextEv.date, true) : 'Termine pflegen'}</div>`; } },
    arena: { t: 'Halle und Platz heute', w: 1, ok: () => belegung, html: () => `<h3>Halle und Platz heute</h3>${goBtn('#/arena', 'Zur Hallenbelegung')}
      ${(dash.arena_today || []).length ? rows(dash.arena_today.map(b => [`${esc(b.start || '')} bis ${esc(b.end || '')} <span class="muted small">${esc(b.place || '')}</span>`, esc(b.kind + (b.title ? ': ' + b.title : ''))]))
        : '<p class="muted">Heute ist nichts eingetragen.</p>'}` },
    lessons: { t: 'Reitstunden heute', w: 1, ok: () => unterricht, html: () => `<h3>Reitstunden heute</h3>${goBtn('#/lessons', 'Zu den Reitstunden')}
      ${(dash.lessons_today || []).length ? rows(dash.lessons_today.map(l => [`<a href="#/lessons/${l.id}">${esc(l.name)}</a>`, esc([l.time, l.place, l.instructor].filter(Boolean).join(', '))]))
        : '<p class="muted">Heute keine festen Stunden.</p>'}` },
    health: { t: 'Pferde: fällige Termine', w: 1, ok: () => stall, html: () => `<h3>Impfung, Schmied, Wurmkur</h3>${goBtn('#/horses', 'Zu den Pferden')}
      ${(dash.health_due || []).length ? (dash.health_due || []).slice(0, 8).map(h => `<div class="sum-row"><span><a href="#/horses/${h.horse_id}"><b>${esc(h.name)}</b></a> ${esc(h.kind)}</span>${dueBadge(h.next_due)}</div>`).join('')
        : '<p class="muted">Nichts fällig in den nächsten 14 Tagen.</p>'}` },
    notes: { t: 'Hinweise', w: 1, tall: true, html: () => `<div class="dash-dark"><div class="dash-dh"><span>Hinweise</span><b>${notes.length}</b></div>
      ${notes.length ? notes.map(n => `<div class="dash-note"><span class="dn-dot"></span><div>${n}</div></div>`).join('') : '<div class="dash-note"><span class="dn-dot ok"></span><div>Alles im grünen Bereich.</div></div>'}</div>` },
    fees: { t: 'Beiträge', w: 1, cls: 'dash-acc', ok: () => !!dash.fees_open, html: () => `<h3>Beiträge</h3>${goBtn('#/fees', 'Zu den Beiträgen')}
      ${rows([['Offene Forderungen', dash.fees_open.count], ['Offener Betrag', eur(dash.fees_open.sum)], ['Überfällig', dash.fees_open.overdue, dash.fees_open.overdue ? 'color:var(--red-text)' : '']])}` },
    club: { t: 'Verein in Zahlen', w: 1, cls: 'dash-acc', ok: () => Object.keys(c).length > 0, html: () => `<h3>Verein in Zahlen</h3>${rows([
      c.members !== undefined ? ['Mitglieder (aktiv)', c.members] : null, c.waiting !== undefined ? ['Warteliste', c.waiting] : null,
      c.horses !== undefined ? ['Pferde im Stall', c.horses] : null, c.school_horses !== undefined ? ['davon Schulpferde', c.school_horses] : null,
      c.boxes_free !== undefined ? ['Freie Boxen', c.boxes_free] : null, c.lessons !== undefined ? ['Aktive Stunden und Kurse', c.lessons] : null].filter(Boolean))}` },
    finance: { t: 'Finanzen', w: 1, cls: 'dash-acc', ok: () => !!dash.finance, html: () => `<h3>Finanzen ${dash.finance.year}</h3>${goBtn('#/finance', 'Zu den Buchungen')}
      ${rows([['Einnahmen', eur(dash.finance.income)], ['Ausgaben', eur(dash.finance.expense)],
        ['Saldo', eur(dash.finance.income - dash.finance.expense), `color:${dash.finance.income - dash.finance.expense < 0 ? 'var(--red-text)' : 'var(--ok)'}`],
        ['Offene Buchungen', dash.finance.open]])}` },
    calendar: { t: 'Wochenkalender', w: 2, cls: 'dash-cal', html: () => `<div class="dc-h"><a class="dc-nav" href="#/events">Termine</a><b>${fmtDate(monday)} bis ${fmtDate(week[6])}</b>${belegung ? '<a class="dc-nav" href="#/arena">Halle</a>' : '<span></span>'}</div>
      <div class="dc-week">${week.map((d, i) => `<div class="dc-day${d === today ? ' today' : ''}"><div class="dc-d">${WDS[i]}<b>${d.slice(8, 10)}</b></div><div data-cal="${d}">${(dash.next_events || [])
        .filter(e => e.date <= d && (e.end_date || e.date) >= d).map(e => `<a class="dc-ev red" href="#/events/${e.id}"><b>${esc(e.title)}</b><small>${esc(e.kind || '')}${e.time ? ', ' + e.time : ''}</small></a>`).join('')}</div></div>`).join('')}</div>`,
      later: belegung ? async el => {
        const items = expandArena(await load('arena_bookings', true), week[0], week[6]);
        week.forEach(d => { const box = $(`[data-cal="${d}"]`, el.closest('.dash-w')); if (box) box.insertAdjacentHTML('beforeend', items.filter(x => x.day === d).slice(0, 4)
          .map((x, i) => `<a class="dc-ev ${i % 2 ? 'light' : 'dark'}" href="#/arena"><b>${esc(x.start || '')} ${esc(x.kind || '')}</b><small>${esc(x.place || '')}${x.title ? ', ' + esc(x.title) : ''}</small></a>`).join('')); });
      } : null },
    contacts: { t: 'Neue Anfragen', w: 1, ok: () => contactsOk, html: () => `<h3>Anfragen</h3>${goBtn('#/contacts', 'Zu den Anfragen')}<div data-fill>${loading}</div>`,
      later: async el => { const r = (await load('contact_requests', true)).filter(x => x.status === 'Neu');
        el.innerHTML = `<div class="dash-big">${r.length}</div><div class="muted small">neue über die Webseite</div>${r.slice(0, 4).map(x => `<div class="sum-row"><span>${esc(x.name || '')}</span><span class="muted small">${esc(x.source || '')}</span></div>`).join('')}`; } },
    mail: { t: 'Postfach', w: 1, ok: () => can('postfach|ownmail'), html: () => `<h3>Postfach</h3>${goBtn('#/postfach', 'Zum Postfach')}<div data-fill>${loading}</div>`,
      later: async el => { const boxes = [...(can('postfach') ? [['info', 'Vereinspostfach']] : []), ...(me.mail_boxes || []).map(b => [b.key, b.address])];
        const res = await Promise.all(boxes.map(([k, l]) => api('postfach/status' + (k === 'info' ? '' : '?box=' + k)).then(r => [l, Number(r.unseen) || 0]).catch(() => [l, '?'])));
        el.innerHTML = `<div class="dash-big">${res.reduce((n, [, v]) => n + (Number(v) || 0), 0)}</div><div class="muted small">ungelesene Mails</div>${rows(res.map(([l, v]) => [esc(l), v]))}`; } },
    newsletter: { t: 'Newsletter', w: 1, ok: () => medien, html: () => `<h3>Newsletter</h3>${goBtn('#/newsletter', 'Zum Newsletter')}<div data-fill>${loading}</div>`,
      later: async el => { const d = await api('newsletter'); const act = d.subscribers.filter(x => x.status === 'aktiv').length, last = d.issues.find(i => i.status === 'gesendet');
        el.innerHTML = `<div class="dash-big">${act}</div><div class="muted small">aktive Abonnenten</div>${rows([['Unbestätigt', d.subscribers.filter(x => x.status === 'offen').length], ['Entwürfe', d.issues.filter(i => i.status === 'entwurf').length]])}
          ${last ? `<p class="muted small" style="margin-top:8px">Zuletzt: ${esc(last.subject)} (${fmtDate((last.sent_at || '').slice(0, 10))}, ${last.sent_count} erreicht)</p>` : ''}`; } },
    quick: { t: 'Schnellzugriff', w: 1, html: () => `<h3>Schnellzugriff</h3><div class="dash-quick">${tabChoices().filter(x => x.page !== 'dashboard').slice(0, 12).map(x => `<a href="${x.href}">${tabIcon(x.icon)}<span>${esc(x.full)}</span></a>`).join('')}</div>` },
  };
  const DEFAULT = ['events', 'arena', 'health', 'notes', 'lessons', 'fees', 'calendar', 'club', 'finance'];
  const avail = k => W[k] && (!W[k].ok || W[k].ok());
  const layout = (Array.isArray(me.dash_layout) && me.dash_layout.length ? me.dash_layout : DEFAULT.map(k => ({ k, w: W[k].w }))).filter(x => avail(x.k));

  view.innerHTML = `
    <div id="dashMaint"></div><div id="dashTasks"></div>
    <section class="dash-hello">
      <div><h1>Willkommen, ${first}</h1>
        <p class="muted">${fmtDate(dash.today, true)}${me.groups?.length ? ' | ' + esc(me.groups.join(', ')) : ''}</p>
        <div class="dash-prog">${pills.map(([l, v, cl]) => `<div><span>${l}</span><b class="dp ${cl}">${v}</b></div>`).join('')}</div></div>
      <div class="dash-stats">${bigs.map(([ic, n, l]) => `<div><span class="ds-ic">${tabIcon(ic)}</span><div><b>${n ?? 0}</b><small>${esc(l)}</small></div></div>`).join('')}</div>
    </section>
    <div class="btn-row" style="margin:-6px 0 16px">${can('unterricht|stall|veranstaltungen', 2) ? '<a class="btn primary" href="#/arena">+ Halle belegen</a>' : ''}
      ${can('stall', 2) ? '<a class="btn" href="#/horses">Pferde</a>' : ''}<button class="btn keep" id="dashEdit" style="margin-left:auto">Anpassen</button></div>
    <section class="dash-grid">${layout.map(x => `<article class="card dash-w ${W[x.k].cls || ''}${x.w === 2 ? ' w2' : ''}${W[x.k].tall ? ' tall' : ''}" data-w="${x.k}">${W[x.k].html()}</article>`).join('')
      || '<div class="card muted">Keine Widgets ausgewählt. Über „Anpassen“ welche hinzufügen.</div>'}</section>`;
  layout.forEach(x => { const art = $(`[data-w="${x.k}"]`, view), box = art && ($('[data-fill]', art) || art);
    if (W[x.k].later && box) W[x.k].later(box).catch(() => { const f = $('[data-fill]', art); if (f) f.innerHTML = '<p class="muted small">Gerade nicht verfügbar.</p>'; }); });

  $('#dashEdit').onclick = () => {   // Widgets an und aus, Reihenfolge, Breite; gespeichert im eigenen Konto
    let items = [...layout.map(x => ({ k: x.k, w: x.w, on: true })), ...Object.keys(W).filter(k => avail(k) && !layout.some(x => x.k === k)).map(k => ({ k, w: W[k].w, on: false }))];
    const draw = () => {
      openModal(`<h2>Übersicht anpassen</h2>
        <p class="muted small">Haken = Widget anzeigen, Pfeile = Reihenfolge, Breite = eine oder zwei Spalten. Es erscheinen nur Widgets, für die du Rechte hast. Gilt für dein Konto auf allen Geräten.</p>
        <div class="list dash-edit">${items.map((x, i) => `<div class="list-item${x.on ? '' : ' off'}">
          <label class="check grow"><input type="checkbox" data-on="${i}" ${x.on ? 'checked' : ''}> ${esc(W[x.k].t)}</label>
          <select class="input" data-wd="${i}" style="width:auto" aria-label="Breite von ${esc(W[x.k].t)}"><option value="1" ${x.w !== 2 ? 'selected' : ''}>Normal</option><option value="2" ${x.w === 2 ? 'selected' : ''}>Breit</option></select>
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
