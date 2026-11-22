/* ====================================================================== */
/* Einstellungen */

App.pages.settings = async (view) => {
  const s = App.settings;
  const fields = [
    { key: 'club_name', label: 'Vereinsname' },
    { key: 'club_short', label: 'Kurzname', hint: 'Steht oben in der Seitenleiste.' },
    { key: 'instagram', label: 'Instagram-Name des Vereins (optional)', hint: 'Ohne @. Nur für Rechtstexte ({Instagram}).' },
    { key: 'arena_places', label: 'Orte für Unterricht und Belegung (einer pro Zeile)', type: 'textarea', rows: 3, hint: 'z. B. Reithalle, Reitplatz, Longierzirkel. Ein umbenannter Ort ändert bestehende Belegungen nicht mit.' },
    { key: 'work_hours_default', label: 'Pflicht-Arbeitsstunden je Mitglied und Jahr', type: 'number', hint: 'Leer = keine Pflichtstunden. Abweichungen je Mitglied unter Mitglieder.' },
    { key: 'health_remind_days', label: 'Erinnerung an Impfung, Schmied, Wurmkur: Tage vorher', type: 'number', hint: 'Zusätzlich am Tag selbst. Wer die Benachrichtigung unter „Mein Profil“ wählt, bekommt eine Mail.' },
  ];
  view.innerHTML = `
    <div class="page-head"><h1>Einstellungen<small>Vereinsdaten, Reitbetrieb, Datensicherung</small></h1>
      <div class="btn-row"><a class="btn" href="#/accounts">Konten</a><a class="btn" href="#/groups">Gruppen &amp; Rechte</a></div></div>
    <div class="card" style="margin-bottom:16px"><form class="form" id="st_form" onsubmit="return false">${fields.map(f => fieldHtml(f, s[f.key])).join('')}</form>
      <div class="btn-row" style="margin-top:16px"><button class="btn primary" id="st_save">Speichern</button></div></div>
    <div class="card" style="margin-bottom:16px"><h2>Webseite</h2>
      <p class="muted small">Texte, Bilder und Aufbau der öffentlichen Webseite pflegst du jetzt unter Medien > Webseite.</p>
      <div class="btn-row"><a class="btn primary" href="#/website">Zur Webseite im Hub</a><a class="btn" href="/" target="_blank" rel="noopener">Webseite ansehen</a></div></div>
    <div class="card"><h2>Datensicherung</h2>
      <p class="muted">Der Server legt jede Nacht automatisch eine Sicherung der Datenbank an. Das Backup-ZIP enthält alle Daten und Bilder, aber keine Passwörter.</p>
      <div class="btn-row"><a class="btn dark" href="/api/backup">Backup herunterladen (ZIP)</a><a class="btn" href="/api/export" download="rsc-frya-fresena-hub-export.json">Daten als JSON</a>
        <button class="btn" id="rs_btn">Backup einspielen</button><input type="file" id="rs_file" accept=".zip,.db" hidden></div>
      <div class="hint" style="margin-top:8px">Einspielen ersetzt alle Vereinsdaten durch das Backup (z. B. aus einer älteren Version). Vorher wird automatisch eine Sicherung angelegt. Konten, Gruppen und Rechte bleiben erhalten.</div>
      <div id="bk_list" class="small muted" style="margin-top:10px"></div></div>
    <div class="card" style="margin-top:16px"><h2>Serverumzug: Komplett-Export und -Import</h2>
      <p class="muted">Für den Umzug auf einen neuen Server. Die Datei enthält <b>alles</b>: Vereinsdaten, Bilder und Dateien, Konten mit Passwörtern, Gruppen und Rechte,
        E-Mail-Versand und das Protokoll. Sie ist mit dem Passwort verschlüsselt, das du hier festlegst. Ohne dieses Passwort lässt sie sich nicht öffnen.</p>
      <div class="grid g2">
        <div><h3>1. Auf dem alten Server exportieren</h3>
          <div class="form"><div class="field full"><label for="mg_pw1">Passwort für die Umzugsdatei (mind. 10 Zeichen)</label><input class="input" id="mg_pw1" type="password" autocomplete="new-password"></div>
            <div class="field full"><label for="mg_pw2">Passwort wiederholen</label><input class="input" id="mg_pw2" type="password" autocomplete="new-password"></div></div>
          <div class="btn-row" style="margin-top:10px"><button class="btn dark" id="mg_export">Komplett-Export herunterladen</button></div>
          <div class="hint" style="margin-top:6px">Die Datei lässt sich einmal herunterladen. Bewahre das Passwort getrennt von der Datei auf.</div></div>
        <div><h3>2. Auf dem neuen Server importieren</h3>
          <div class="form"><div class="field full"><label for="mg_file">Umzugsdatei (.rschub)</label><input class="input" id="mg_file" type="file" accept=".rschub"></div>
            <div class="field full"><label for="mg_pw3">Passwort der Umzugsdatei</label><input class="input" id="mg_pw3" type="password" autocomplete="off"></div></div>
          <div class="btn-row" style="margin-top:10px"><button class="btn danger" id="mg_import">Alles ersetzen und importieren</button></div>
          <div class="hint" style="margin-top:6px">Ersetzt den kompletten Stand dieses Hubs, auch Konten. Vorher wird automatisch eine Sicherung angelegt. Danach meldest du dich mit den Zugangsdaten des alten Servers an.</div></div>
      </div><div id="mg_status" class="small" style="margin-top:10px"></div></div>
    <div class="card" style="margin-top:16px" id="mail_card"><h2>E-Mail-Versand für Benachrichtigungen</h2><div class="muted">Lade ...</div></div>`;
  $('#mg_export').onclick = async () => {
    const pw = $('#mg_pw1').value;
    if (pw.length < 10) return alert('Das Passwort braucht mindestens 10 Zeichen.');
    if (pw !== $('#mg_pw2').value) return alert('Die Passwörter stimmen nicht überein.');
    $('#mg_status').textContent = 'Erstelle die Umzugsdatei, das kann bei vielen Bildern etwas dauern ...';
    try {
      const r = await api('migrate/export', 'POST', { password: pw });
      $('#mg_status').textContent = `Fertig (${(r.size / 1048576).toFixed(1).replace('.', ',')} MB). Der Download startet.`;
      $('#mg_pw1').value = $('#mg_pw2').value = '';
      location.href = r.download;
    } catch (e) { $('#mg_status').textContent = ''; alert(e.message); }
  };
  $('#mg_import').onclick = async () => {
    const f = $('#mg_file').files[0], pw = $('#mg_pw3').value;
    if (!f || !pw) return alert('Bitte Umzugsdatei auswählen und Passwort eingeben.');
    if (!confirmBox(`„${f.name}“ importieren? Der komplette Stand dieses Hubs wird ersetzt, auch alle Konten.`)) return;
    $('#mg_status').textContent = 'Lade hoch und prüfe die Datei ...';
    const r = await fetch('/api/migrate/import', { method: 'POST', body: f, headers: { 'X-TMH': '1', 'Content-Type': 'application/octet-stream',
      'X-Umzug-Passwort': btoa(String.fromCharCode(...new TextEncoder().encode(pw))) } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { $('#mg_status').textContent = ''; return alert('Import fehlgeschlagen: ' + (d.error || r.status)); }
    alert(`Umzug eingespielt: Stand vom ${fmtStamp(d.created)} (Version ${d.version}), ${d.users} Konten, ${d.media} Dateien.\nBitte jetzt mit den Zugangsdaten des alten Servers anmelden.`);
    location.href = '/login';
  };
  const mailCard = async () => {
    const c = await api('notify');
    $('#mail_card').innerHTML = `<h2>E-Mail-Versand für Benachrichtigungen</h2>
      <p class="muted small">Über dieses Postfach verschickt der Hub die Benachrichtigungen, die jeder unter „Mein Profil“ auswählt. Bei Gmail ein App-Passwort verwenden.</p>
      <div class="form">
        <div class="field"><label for="ml_host">Mailserver (SMTP)</label><input class="input" id="ml_host" value="${esc(c.smtp_host || '')}" placeholder="z. B. smtp.gmail.com"></div>
        <div class="field"><label for="ml_port">Port</label><input class="input" id="ml_port" value="${esc(c.smtp_port || '')}" placeholder="587"></div>
        <div class="field"><label for="ml_sec">Verschlüsselung</label><select class="input" id="ml_sec">${[['starttls', 'STARTTLS (Port 587)'], ['ssl', 'SSL/TLS (Port 465)'], ['none', 'keine']].map(([v, l]) => `<option value="${v}" ${(c.smtp_security || 'starttls') === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
        <div class="field"><label for="ml_user">Benutzername</label><input class="input" id="ml_user" value="${esc(c.smtp_user || '')}" autocomplete="off"></div>
        <div class="field"><label for="ml_pw">Passwort</label><input class="input" id="ml_pw" type="password" autocomplete="new-password" placeholder="${c.password_set ? 'hinterlegt, nur zum Ändern neu eintragen' : ''}"></div>
        <div class="field"><label for="ml_from">Absender</label><input class="input" id="ml_from" value="${esc(c.smtp_from || '')}" placeholder="z. B. RSC Frya Fresena"><small class="muted">Name, der als Absender erscheint. Ohne Adresse nimmt der Hub die Adresse des Postfachs (Benutzername). Eine andere Adresse nur, wenn sie zu diesem Postfach gehört.</small></div>
        <div class="field"><label for="ml_imap">Posteingang (IMAP-Server, optional)</label><input class="input" id="ml_imap" value="${esc(c.imap_host || '')}" placeholder="leer: aus SMTP abgeleitet, z. B. imap.strato.de"><small class="muted">Für die Seite Postfach. Gleicher Benutzer und gleiches Passwort, Port 993.</small></div>
        <div class="field full"><label for="ml_url">Adresse des Hubs für Links in der Mail</label><input class="input" id="ml_url" value="${esc(c.hub_url || '')}" placeholder="z. B. https://pferd.beispiel.de"></div></div>
      <div class="field full" style="margin-top:10px"><label>Bei neuen Mails benachrichtigen (Push und Zahl am App-Symbol)</label><div class="chk-grid" id="ml_groups">Lade ...</div>
        <small class="muted">Mitglieder dieser Gruppen bekommen jede neue Mail im Posteingang gemeldet, bis jemand sie erledigt. Die Gruppe braucht dafür mindestens „Lesen“ im Bereich Postfach.</small></div>
      <p class="muted small" style="margin-top:10px"><b>Signatur:</b> Mails aus dem Postfach bekommen die Vereinssignatur mit Logo. Der Name kommt aus dem Konto, die Rolle aus den Gruppen der Person (oder „Rolle in der Signatur“ unter Konten).</p>
      ${c.last_error ? `<div class="warnline" style="margin-top:8px">Letzter Fehler beim Versand: ${esc(c.last_error)}</div>` : ''}
      <div class="btn-row" style="margin-top:12px"><button class="btn primary" id="ml_save">Speichern</button>
        <input class="input" id="ml_to" placeholder="Test an E-Mail-Adresse" style="max-width:240px"><button class="btn" id="ml_test">Test-Mail senden</button></div>`;
    api('admin/groups').then(gs => {
      let sel = []; try { sel = JSON.parse(App.settings.pf_notify_groups || '[]'); } catch { sel = []; }
      $('#ml_groups').innerHTML = gs.map(g => `<label class="check"><input type="checkbox" class="ml_g" value="${esc(g.name)}" ${sel.includes(g.name) ? 'checked' : ''}> ${esc(g.name)}
        ${(g.perms.postfach || 0) < 1 ? '<span class="badge warn" title="Hat kein Recht im Bereich Postfach">ohne Postfach-Recht</span>' : ''}</label>`).join('');
    }).catch(() => {});
    $('#ml_save').onclick = async () => {
      await api('notify', 'PUT', { smtp_host: $('#ml_host').value, smtp_port: $('#ml_port').value, smtp_security: $('#ml_sec').value,
        smtp_user: $('#ml_user').value, smtp_password: $('#ml_pw').value, smtp_from: $('#ml_from').value, hub_url: $('#ml_url').value, imap_host: $('#ml_imap').value });
      if ($('#ml_groups .ml_g')) App.settings = await api('settings', 'PUT', { pf_notify_groups: JSON.stringify($$('#ml_groups .ml_g').filter(c => c.checked).map(c => c.value)) });
      toast('E-Mail-Versand gespeichert'); mailCard();
    };
    $('#ml_test').onclick = async () => {
      try { await api('notify/test', 'POST', { to: $('#ml_to').value }); toast('Test-Mail verschickt'); } catch (e) { alert(e.message); }
    };
  };
  mailCard().catch(() => {});
  $('#st_save').onclick = async () => {
    App.settings = await api('settings', 'PUT', readForm($('#st_form'), fields));
    applyBranding(); toast('Einstellungen gespeichert');
  };
  $('#rs_btn').onclick = () => $('#rs_file').click();
  $('#rs_file').onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    if (!confirmBox(`„${f.name}“ einspielen? Alle aktuellen Vereinsdaten werden durch das Backup ersetzt.`)) return;
    toast('Spiele Backup ein ...');
    const r = await fetch('/api/restore', { method: 'POST', headers: { 'X-TMH': '1', 'Content-Type': 'application/octet-stream' }, body: f });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) return alert('Einspielen fehlgeschlagen: ' + (d.error || r.status));
    App.cache = {}; await loadSettings();
    alert(`Backup eingespielt. ${d.media} Bilder übernommen.`); route();
  };
  api('backups').then(list => { const el = $('#bk_list'); if (el && list.length) el.innerHTML = 'Automatische Sicherungen auf dem Server: ' + list.slice(0, 5).map(b => esc(b.name)).join(', ') + (list.length > 5 ? ' ...' : ''); }).catch(() => {});
};
