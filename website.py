"""RSC Frya Fresena: öffentliche Webseite (wird vom Server gerendert, ohne JavaScript-Pflicht).

Bekommt ausschließlich die öffentlichen Daten aus server.py (public_info, public_horses, public_fees, public_events,
public_arena, public_articles) und baut daraus HTML. Kein Datenbankzugriff, keine internen Felder.
Fehlende Inhalte erscheinen als deutlich markierter Platzhalter (Klasse .ph), siehe PLATZHALTER.md.
Keine externen Schriften, Skripte, Tracker oder Einbettungen.
"""
import json
import re
import threading
from datetime import date, datetime
from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path

WEB = Path(__file__).resolve().parent / "web"
IMG = WEB / "assets" / "img"
IMG_WIDTHS = (640, 960, 1280, 1920)

# Impressum und Datenschutz: eigene Texte aus dem Hub (Einstellungen site_impressum, site_datenschutz)
LEGAL = {"impressum": ("Impressum", "site_impressum"), "datenschutz": ("Datenschutzerklärung", "site_datenschutz")}
NAV = [("", "Start"), ("verein", "Verein"), ("reitunterricht", "Reitunterricht"), ("pferde", "Schulpferde"), ("anlage", "Anlage"),
       ("termine", "Termine"), ("news", "News"), ("mitgliedschaft", "Mitgliedschaft"), ("kontakt", "Kontakt")]
# Andere übliche Adressen leiten dauerhaft auf die Seiten weiter
REDIRECTS = {"unterricht": "/reitunterricht", "schulpferde": "/pferde", "vorstand": "/verein", "veranstaltungen": "/termine",
             "turniere": "/termine", "lehrgaenge": "/termine", "beitraege": "/mitgliedschaft", "mitglied-werden": "/mitgliedschaft"}
# Anliegen im Kontaktformular: Wert (wie CONTACT_TOPICS in server.py), Kurzname für ?anliegen=, Beschriftung, Hinweis
CONTACT_TOPICS = [("Probestunde", "probestunde", "Probestunde", "Reiten ausprobieren, für Kinder und Erwachsene"),
                  ("Mitgliedschaft", "mitgliedschaft", "Mitgliedschaft", "Mitglied werden, Beiträge, Warteliste"),
                  ("Einstellplatz", "einstellplatz", "Einstellplatz", "Box oder Platz für dein eigenes Pferd"),
                  ("Allgemeine Frage", "frage", "Allgemeine Frage", "Alles andere rund um den Verein")]
NL_CONSENT = ("Ich möchte den Newsletter des RSC Frya Fresena zu den gewählten Themen per E-Mail erhalten. "
              "Die Einwilligung kann ich jederzeit über den Link in jeder Ausgabe widerrufen.")
# Bilder, die im Hub (Webseite > Bilder) ersetzt werden können: Name -> (Bezeichnung, wo es erscheint)
IMAGE_SLOTS = {
    "hero": ("Titelbild Startseite", "Großes Bild ganz oben auf der Startseite, auch Vorschaubild beim Teilen von Links"),
    "verein": ("Verein", "Titelbild der Seite Verein und Abschnitt „Über uns“ auf der Startseite"),
    "unterricht": ("Reitunterricht", "Titelbild der Seite Reitunterricht und Abschnitt Angebote auf der Startseite"),
    "voltigieren": ("Voltigieren", "Abschnitt Voltigieren auf der Seite Reitunterricht (nur wenn eingeschaltet)"),
    "pferde": ("Schulpferde", "Titelbild der Seite Schulpferde"),
    "anlage": ("Anlage", "Titelbild der Seite Anlage"),
    "termine": ("Termine", "Titelbild der Seiten Termine und Hallenplan"),
    "news": ("News", "Titelbild der News-Übersicht"),
    "mitglied": ("Mitgliedschaft", "Titelbild der Seite Mitgliedschaft und Abschnitt „Mitglied werden“ auf der Startseite"),
    "kontakt": ("Kontakt", "Titelbild der Seiten Kontakt und Newsletter"),
    "wartung": ("Wartungsseite", "Hintergrund der Wartungsseite (im Hub unter Verwaltung > Wartungsmodus)"),
}
MAINT_SLOT = "wartung"   # gepflegt nur auf der Hub-Seite Wartungsmodus, nicht unter Webseite > Bilder
# Abschnitte der Startseite in der Standard-Reihenfolge (im Hub sortier- und ausblendbar). Titelbild bleibt immer oben.
START_SECTIONS = {
    "wer": "Über uns",
    "angebote": "Angebote (aus Reitunterricht)",
    "pferde": "Unsere Schulpferde (bis zu 4)",
    "termine": "Nächste Termine (bis zu 4)",
    "news": "News (die 3 neuesten Artikel)",
    "mitglied": "Mitglied werden",
}
# Texte, die auf der Webseite als Platzhalter erscheinen, solange sie leer sind (Checkliste im Hub)
REQUIRED_TEXTS = {
    "site_contact_email": "Kontakt-E-Mail", "site_address": "Anschrift der Anlage", "site_hero_text": "Startseite: Satz unter der Überschrift",
    "site_about": "Verein: Über uns", "site_history": "Verein: Geschichte", "site_board": "Verein: Vorstand",
    "site_lessons_intro": "Reitunterricht: Einleitung", "site_lessons_offers": "Reitunterricht: Angebote", "site_lessons_prices": "Reitunterricht: Preise",
    "site_horses_intro": "Schulpferde: Einleitung", "site_facility_text": "Anlage: Beschreibung", "site_facility_facts": "Anlage: Eckdaten",
    "site_membership_text": "Mitgliedschaft: Text", "site_impressum": "Impressum", "site_datenschutz": "Datenschutzerklärung",
}
_ctx = threading.local()   # eigene Bilder für die gerade gebaute Seite (der Server bearbeitet Anfragen parallel)
WD = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
WD_S = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


def e(t):
    return escape(str(t if t is not None else ""), quote=True)


def asset(path):
    """Link auf eine Datei unter web/assets mit Änderungsstempel, damit Browser nach Updates die neue Fassung laden."""
    f = WEB / "assets" / path
    v = int(f.stat().st_mtime) if f.is_file() else 0
    return f"/web/assets/{path}?v={v}"


_CSS = {}


def site_css():
    """site.css verkleinert für einen <style>-Block im Kopf (keine blockierende CSS-Anfrage). Neu eingelesen, wenn die Datei sich ändert."""
    f = WEB / "assets" / "site.css"
    try:
        mt = f.stat().st_mtime
    except OSError:
        return ""
    if _CSS.get("mt") != mt:
        css = re.sub(r"/\*.*?\*/", "", f.read_text(encoding="utf-8"), flags=re.S)
        css = re.sub(r"\s+", " ", css)
        css = re.sub(r"\s*([{};,>])\s*", r"\1", css).replace(";}", "}")
        _CSS.update(mt=mt, css=css.replace("</", r"<\/").strip())   # kein </style> im Block möglich
    return _CSS["css"]


def lines(t):
    return "<br>".join(e(x) for x in str(t or "").splitlines())


def placeholder(what):
    """Klar markierter Platzhalter für Inhalte, die im Hub noch fehlen."""
    return f'<span class="ph" title="Platzhalter, im Hub unter Webseite pflegen">Platzhalter: {e(what)}</span>'


def text_or_ph(value, what):
    return lines(value) if (value or "").strip() else placeholder(what)


def paras_or_ph(value, what):
    return paragraphs(value) if (value or "").strip() else f"<p>{placeholder(what)}</p>"


def pairs(text):
    """Zeilen „Links | Rechts“ (z. B. Vorstand, Angebote, Preise) als [(links, rechts)], leere Zeilen weg."""
    out = []
    for row in str(text or "").splitlines():
        a, _, b = row.partition("|")
        if a.strip() or b.strip():
            out.append((a.strip(), b.strip()))
    return out


def fmt_date(d, weekday=False):
    try:
        x = date.fromisoformat(d)
    except (TypeError, ValueError):
        return ""
    return (f"{WD[x.weekday()]}, " if weekday else "") + f"{x.day:02d}.{x.month:02d}.{x.year}"


def euro(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return ""
    s = f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return (s[:-3] if s.endswith(",00") else s) + " €"


def maps_url(info):
    v = (info.get("site_maps_url") or "").strip()
    return v if re.fullmatch(r"https://[^\s\"'<>]+", v) else ""


# ---------------------------------------------------------------------------
# Bilder: im Hub hochgeladen (data/site, Name mit Zufallsstempel), sonst web/assets/img/<name>-<breite>.webp mit bilder.json
# ---------------------------------------------------------------------------
def image_meta(standard=False):
    """Bildbeschreibungen aus bilder.json, darüber die im Hub ersetzten Bilder (mit eigenen Dateien unter "src")."""
    f = IMG / "bilder.json"
    try:
        meta = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = {}
    if not standard:
        for slot, m in (getattr(_ctx, "images", None) or {}).items():
            if m.get("src"):
                meta[slot] = m
            elif slot in meta and m.get("alt"):   # nur der Alt-Text wurde im Hub geändert
                meta[slot] = {**meta[slot], "alt": m["alt"]}
    return meta


def image_files(name, meta=None):
    meta = meta if meta is not None else image_meta()
    m = meta.get(name)
    if m and m.get("src"):   # im Hub ersetztes Bild
        return m, sorted(m["src"].items())
    return m, [(w, f"/web/assets/img/{name}-{w}.webp") for w in IMG_WIDTHS if (IMG / f"{name}-{w}.webp").is_file()]


def picture(name, sizes="100vw", cls="", eager=False, meta=None, ph=True):
    """Bild mit srcset. Fehlt es: gelber Platzhalter (ph=True) oder nichts (ph=False, z. B. Hintergrund mit Vereinsfarbe)."""
    m, files = image_files(name, meta)
    if not m or not files:
        return f'<div class="img-ph {e(cls)}">{placeholder("Foto " + IMAGE_SLOTS.get(name, (name,))[0])}</div>' if ph else ""
    srcset = ", ".join(f"{e(u)} {w}w" for w, u in files)
    mid = files[min(1, len(files) - 1)][1]
    load = 'fetchpriority="high"' if eager else 'loading="lazy"'   # das erste große Bild sofort, alle anderen erst beim Scrollen
    return (f'<img class="{e(cls)}" src="{e(mid)}" srcset="{srcset}" sizes="{e(sizes)}" '
            f'width="{m.get("w", 1600)}" height="{m.get("h", 1067)}" alt="{e(m.get("alt") or "Foto vom RSC Frya Fresena")}" {load} decoding="async">')


def page_hero(slot, eyebrow, title, lead=""):
    """Titelbereich einer Seite: Foto aus dem Hub, ohne Foto die Vereinsfarbe mit einem markierten Platzhalter."""
    img = picture(slot, "100vw", "hero-img", eager=True, ph=False)
    note = "" if img else f'<p class="hero-ph">{placeholder("Foto " + IMAGE_SLOTS[slot][0])}</p>'
    return (f'<section class="page-hero{"" if img else " no-img"}">{img}<div class="hero-text"><p class="eyebrow light">{e(eyebrow)}</p>'
            f'<h1>{e(title)}</h1>{f"<p class=lead>{lead}</p>" if lead else ""}{note}</div></section>')


# ---------------------------------------------------------------------------
# Rahmen: Kopf, Navigation, Fußzeile
# ---------------------------------------------------------------------------
SEO_DESC = {"": "site_seo_start", "verein": "site_seo_verein", "reitunterricht": "site_seo_reitunterricht", "pferde": "site_seo_pferde",
            "anlage": "site_seo_anlage", "termine": "site_seo_termine", "news": "site_seo_news", "mitgliedschaft": "site_seo_mitgliedschaft",
            "kontakt": "site_seo_kontakt"}


def seo_desc(info, slug, fallback):
    """Beschreibung für Suchmaschinen (im Hub unter Webseite > Texte > Suchmaschinen), sonst die feste Vorgabe."""
    v = " ".join((info.get(SEO_DESC.get(slug, "")) or "").split())
    return v[:300] or fallback


def verify_tags(info):
    """Bestätigung für Google Search Console und Bing Webmaster Tools (nur der Code, streng geprüft)."""
    out = []
    for key, name in (("site_verify_google", "google-site-verification"), ("site_verify_bing", "msvalidate.01")):
        v = (info.get(key) or "").strip()
        m = re.search(r'content="([^"]+)"', v)   # auch das ganze <meta>-Tag wird angenommen
        v = m.group(1) if m else v
        if re.fullmatch(r"[A-Za-z0-9_\-]{8,100}", v):
            out.append(f'<meta name="{name}" content="{e(v)}">')
    return "".join(out)


def structured_head(slug, info, canonical=None):
    """canonical-Link, Verein als SportsClub und die kommenden Termine als Event (Startseite, Termine)."""
    base = getattr(_ctx, "base", "")
    if not base or slug not in PAGES:
        return ""
    if canonical is not None:   # eigene Adresse (z. B. Artikel): nur diese, keine zweite
        return f'<link rel="canonical" href="{e(base)}{e(canonical)}">'
    out = [f'<link rel="canonical" href="{e(base)}/{e(slug)}">', jsonld(org_ld(info, base))]
    if slug == "":
        out.append(jsonld({"@context": "https://schema.org", "@type": "WebSite", "name": info.get("club_name") or "RSC Frya Fresena e. V.",
                           "url": base + "/", "inLanguage": "de-DE"}))
    if slug in ("", "termine"):
        events = [x for x in (event_ld(ev, info, base) for ev in getattr(_ctx, "events", [])[:12]) if x]
        if events:
            out.append(jsonld(events if len(events) > 1 else events[0]))
    return "\n".join(out)


def notice_bar(info):
    """Hinweis-Leiste oben (im Hub unter Webseite > Hinweis), nur bis einschließlich zum eingestellten Datum."""
    text, link, until = (info.get("site_notice_text") or "").strip(), (info.get("site_notice_link") or "").strip(), info.get("site_notice_until") or ""
    if not text or (until and until < date.today().isoformat()):
        return ""
    ok = link.startswith("/") and not link.startswith("//") or link.startswith(("https://", "http://"))
    target = "" if link.startswith("/") else ' target="_blank" rel="noopener"'
    more = f' <a href="{e(link)}"{target}>Mehr erfahren</a>' if ok else ""
    return f'<div class="notice"><p>{e(text)}{more}</p></div>'


def maint_bar():
    """Nur für Angemeldete mit Recht Wartung: Besucher sehen gerade die Wartungsseite."""
    if not getattr(_ctx, "maint_bar", False):
        return ""
    return ('<div class="maint-bar"><p><b>Wartungsmodus ist an.</b> Besucher sehen gerade die Wartungsseite, du siehst die Webseite, '
            'weil du angemeldet bist. <a href="/?wartung-vorschau=1">Wartungsseite ansehen</a> <a href="/hub#/wartung">Im Hub ändern</a></p></div>')


SOCIAL_ICONS = {   # eigene, einfache Symbole (nichts wird von Instagram, Facebook oder TikTok geladen)
    "instagram": ("Instagram", '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="12" cy="12" r="4"/><circle cx="17.5" cy="6.5" r="1" fill="currentColor" stroke="none"/>'),
    "facebook": ("Facebook", '<path d="M14 8h3V4h-3a4 4 0 0 0-4 4v3H7v4h3v6h4v-6h3l1-4h-4V8z"/>'),
    "tiktok": ("TikTok", '<path d="M14 3v11.5a3.5 3.5 0 1 1-3.5-3.5"/><path d="M14 3c.5 2.8 2.4 4.6 5 5"/>'),
}


def social_links(cls=""):
    menu = getattr(_ctx, "nav", None) or {}
    return "".join(f'<a class="{cls}" href="{e(s["url"])}" target="_blank" rel="noopener" aria-label="{SOCIAL_ICONS[s["kind"]][0]}" title="{SOCIAL_ICONS[s["kind"]][0]}">'
                   f'<svg viewBox="0 0 24 24" aria-hidden="true">{SOCIAL_ICONS[s["kind"]][1]}</svg><span>{SOCIAL_ICONS[s["kind"]][0]}</span></a>'
                   for s in menu.get("social", []) if s.get("kind") in SOCIAL_ICONS)


def robots_meta(noindex):
    return '<meta name="robots" content="noindex, nofollow">' if noindex else '<meta name="robots" content="index, follow, max-image-preview:large">'


def layout(slug, title, description, body, info, extra_head="", canonical=None, noindex=False):
    club = info.get("club_name") or "RSC Frya Fresena e. V."
    menu = getattr(_ctx, "nav", None)
    if menu:   # Menü aus dem Hub (Webseite > Menü): Reihenfolge, Namen, hervorgehobene Knöpfe
        nav = "".join(f'<a href="{e(x["href"])}" class="{"cta" if x.get("button") else ""}{" active" if x.get("slug") is not None and x["slug"] == slug else ""}">'
                      f'{e(x["label"])}</a>' for x in menu.get("items", []))
    else:
        nav = "".join(f'<a href="/{s}" class="{"active" if s == slug else ""}">{e(lbl)}</a>' for s, lbl in NAV)
    icons = social_links()
    nav += f'<span class="nav-social">{icons}</span>' if icons else ""
    mail = (info.get("site_contact_email") or "").strip()
    phone = (info.get("site_phone") or "").strip()
    addr = (info.get("site_address") or "").strip()
    maps = maps_url(info)
    hero = image_meta().get("hero") or {}
    og_img = (hero.get("src") or {}).get(1280) or ("/web/assets/img/hero-1280.webp" if (IMG / "hero-1280.webp").is_file() else "/static/logo.png")
    base = getattr(_ctx, "base", "")
    og_abs = og_img if og_img.startswith("http") else base + og_img   # Messenger und soziale Netzwerke brauchen eine volle Adresse
    tagline = " ".join((info.get("site_seo_title") or "").split())[:70] or "Reitverein"
    full_title = title + " | " + club if title else club + " | " + tagline
    own_robots = 'name="robots"' in extra_head
    site_noindex = info.get("site_noindex") == "1"   # vorläufige Domain: Suchmaschinen ausschließen (Hub > Webseite > Suchmaschinen)
    noindex = noindex or own_robots or slug not in PAGES or site_noindex
    url = base + (canonical if canonical is not None else "/" + slug) if base and not noindex else ""
    robots = "" if own_robots else robots_meta(noindex)
    return f"""<!doctype html>
<html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(full_title)}</title>
<meta name="description" content="{e(description)}">
{robots}
<meta property="og:site_name" content="{e(club)}"><meta property="og:locale" content="de_DE">
<meta property="og:title" content="{e(title or club)}"><meta property="og:description" content="{e(description)}">
<meta property="og:image" content="{e(og_abs)}"><meta property="og:type" content="{'article' if canonical else 'website'}">
{f'<meta property="og:url" content="{e(url)}">' if url else ''}
{verify_tags(info) if slug == "" and not site_noindex else ""}
<meta name="theme-color" content="#0D393A">
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="icon" type="image/png" sizes="192x192" href="/static/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
{"" if noindex else structured_head(slug, info, canonical)}
<style>{site_css()}</style>
{extra_head}
</head><body class="page-{e(slug or 'start')}">
<a class="skip-link" href="#inhalt">Zum Inhalt springen</a>
<header class="site-head">
  <a class="brand" href="/" aria-label="{e(club)} Startseite"><img src="{asset('logo.webp')}" alt="Logo {e(club)}" width="480" height="480"><span>{e(club)}</span></a>
  <input type="checkbox" id="menu" class="menu-toggle" aria-label="Menü öffnen">
  <label for="menu" class="menu-btn" aria-hidden="true"><span></span><span></span><span></span></label>
  <nav class="site-nav" aria-label="Hauptmenü">{nav}</nav>
</header>
<main id="inhalt" tabindex="-1">{maint_bar()}{notice_bar(info)}{body}</main>
<footer class="site-foot">
  <div class="foot-grid">
    <div><img class="foot-logo" src="{asset('logo.webp')}" alt="Logo {e(club)}" width="480" height="480" loading="lazy">
      <p><b>{e(club)}</b><br>{lines(addr) if addr else placeholder("Anschrift der Anlage")}</p>
      {f'<p><a class="more" href="{e(maps)}" target="_blank" rel="noopener">Anfahrt auf der Karte</a></p>' if maps else ''}</div>
    <div><h2 class="foot-h">Kontakt</h2>
      <p>{f'<a href="mailto:{e(mail)}">{e(mail)}</a>' if mail else placeholder("Kontakt-E-Mail")}
      {f'<br><a href="tel:{e(re.sub(r"[^0-9+]", "", phone))}">{e(phone)}</a>' if phone else ''}<br>
      <a href="/kontakt#formular">Kontaktformular</a></p>
      {f'<p class="foot-social">{icons}</p>' if icons else ''}</div>
    <div><h2 class="foot-h">Verein</h2><p><a href="/verein">Über uns</a><br><a href="/reitunterricht">Reitunterricht</a><br><a href="/pferde">Schulpferde</a><br>
      <a href="/termine">Termine</a><br><a href="/mitgliedschaft">Mitgliedschaft</a><br><a href="/news">News</a></p></div>
    <div><h2 class="foot-h">Newsletter</h2><p>Termine, Lehrgänge und Neuigkeiten aus dem Stall per E-Mail.</p>
      <form class="foot-nl" action="/newsletter#anmelden" method="get"><input name="email" type="email" maxlength="150" placeholder="Deine E-Mail" aria-label="E-Mail-Adresse für den Newsletter" autocomplete="email" required>
        <button class="btn gold" type="submit">Abonnieren</button></form></div>
    <div><h2 class="foot-h">Rechtliches</h2><p><a href="/impressum">Impressum</a><br><a href="/datenschutz">Datenschutz</a><br><a class="members" href="/hub">Interner Bereich</a></p></div>
  </div>
  <p class="copy">© {datetime.now().year} {e(club)}</p>
</footer>
<script src="{asset('site.js')}" defer></script>
</body></html>"""


def section_head(eyebrow, title, more=""):
    return f'<div class="sec-head"><p class="eyebrow">{e(eyebrow)}</p><h2>{e(title)}</h2>{more}</div>'


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------
def horse_card(h):
    facts = " · ".join(x for x in (h["breed"], h["sex"], (f"Jahrgang {h['birth_year']}" if h["birth_year"] else ""), (f"{h['height']}" if h["height"] else "")) if x)
    img = (f'<img src="{e(h["photo"])}" alt="Schulpferd {e(h["name"])}" loading="lazy" decoding="async">' if h["photo"]
           else f'<div class="hc-ph">{placeholder("Foto " + h["name"])}</div>')
    facts_html = f'<p class="hc-facts">{e(facts)}</p>' if facts else ""
    suited = f'<p class="hc-suited">Geeignet für: {e(h["suited_for"])}</p>' if h["suited_for"] else ""
    return (f'<article class="horse-card reveal"><div class="hc-img">{img}</div><div class="hc-text"><h3>{e(h["name"])}</h3>'
            f'{facts_html}{suited}{paragraphs(h["text"]) if h["text"] else ""}</div></article>')


def event_when(ev):
    d = fmt_date(ev["date"], True)
    if ev.get("end_date") and ev["end_date"] != ev["date"]:
        d += f" bis {fmt_date(ev['end_date'], True)}"
    return d + (f", {ev['time']} Uhr" if ev.get("time") else "")


def event_row(ev, full=False):
    try:
        x = date.fromisoformat(ev["date"])
        badge = f'<div class="ev-date"><b>{x.day:02d}</b><span>{MON[x.month - 1][:3]}</span></div>'
    except ValueError:
        badge = '<div class="ev-date"></div>'
    meta = " · ".join(v for v in (ev["kind"], ev["place"]) if v)
    extra = ""
    if full:
        bits = []
        if ev.get("entry_deadline"):
            bits.append(f"Nennungs- bzw. Anmeldeschluss: {e(fmt_date(ev['entry_deadline']))}")
        if ev.get("price"):
            bits.append(f"Kosten: {e(ev['price'])}")
        extra = (paragraphs(ev["description"]) if ev.get("description") else "") + (f'<p class="small">{"<br>".join(bits)}</p>' if bits else "")
        if ev.get("file"):
            extra += f'<p><a class="more" href="{e(ev["file"])}" target="_blank" rel="noopener">Ausschreibung ansehen</a></p>'
    return (f'<article class="event reveal" id="termin-{ev["id"]}">{badge}<div class="ev-text"><p class="ev-meta">{e(event_when(ev))}'
            f'{f" · {e(meta)}" if meta else ""}</p><h3>{e(ev["title"])}</h3>{extra}</div></article>')


def fee_rows(fees):
    out = []
    for f in fees:
        age = ""
        if f["age_from"] is not None and f["age_to"] is not None:
            age = f"{f['age_from']} bis {f['age_to']} Jahre"
        elif f["age_from"] is not None:
            age = f"ab {f['age_from']} Jahren"
        elif f["age_to"] is not None:
            age = f"bis {f['age_to']} Jahre"
        amount = euro(f["amount"])
        small = "".join(f"<small>{e(x)}</small>" for x in (age, f["description"]) if x)
        out.append(f'<tr><th scope="row">{e(f["name"])}{small}</th>'
                   f'<td>{e(amount) + " " + e(f["interval"]) if amount else placeholder("Betrag")}</td>'
                   f'<td>{e(euro(f["admission_fee"])) if f["admission_fee"] else "keine"}</td></tr>')
    return "".join(out)


def news_card(a):
    img = (f'<img src="{e(a["image"])}" alt="{e(a["image_alt"] or a["title"])}" loading="lazy" decoding="async">' if a["image"]
           else f'<div class="nc-logo"><img src="{asset("logo.webp")}" alt="" loading="lazy"></div>')
    teaser = a["teaser"] or (plain_text(a["body"], a.get("format")).strip().split("\n")[0][:180] if a["body"] else "")
    return (f'<a class="news-card reveal" href="/news/{e(a["slug"])}"><div class="nc-img">{img}</div><div class="nc-text">'
            f'<p class="nc-meta">{e(fmt_date(a["date"]))}</p>'
            f'<h3>{e(a["title"])}</h3>{f"<p>{e(teaser)}</p>" if teaser else ""}<span class="more">Weiterlesen</span></div></a>')


def offer_cards(info, limit=None):
    offers = pairs(info.get("site_lessons_offers"))[:limit] if limit else pairs(info.get("site_lessons_offers"))
    if not offers:
        return f"<p>{placeholder('Angebote, eine Zeile pro Angebot: Titel | Beschreibung')}</p>"
    return '<div class="offer-grid">' + "".join(f'<div class="offer reveal"><h3>{e(t)}</h3>{f"<p>{e(d)}</p>" if d else ""}</div>' for t, d in offers) + "</div>"


# ---------------------------------------------------------------------------
# Seiten
# ---------------------------------------------------------------------------
def page_start(info, horses, events, articles, layout_order):
    club = info.get("club_name") or "RSC Frya Fresena e. V."
    title = (info.get("site_hero_title") or "").strip() or club
    img = picture("hero", "100vw", "hero-img", eager=True, ph=False)
    hero = f"""<section class="hero{'' if img else ' no-img'}">{img}<div class="hero-inner">
  <img class="hero-logo" src="{asset('logo.webp')}" alt="" width="480" height="480">
  <h1>{e(title)}</h1>
  <p class="lead">{text_or_ph(info.get("site_hero_text"), "Satz unter der Überschrift")}</p>
  <div class="btns"><a class="btn gold" href="/kontakt?anliegen=probestunde#formular">Probestunde anfragen</a><a class="btn ghost" href="/reitunterricht">Unsere Angebote</a></div>
  {'' if img else f'<p class="hero-ph">{placeholder("Foto Titelbild")}</p>'}
</div></section>"""
    sec = {}
    sec["wer"] = f"""<section class="wrap split reveal"><div>{section_head("Über uns", club)}{paras_or_ph(info.get("site_about"), "Über uns")}
  <p><a class="more" href="/verein">Mehr über den Verein</a></p></div><div class="split-img">{picture("verein", "(min-width: 900px) 45vw, 100vw")}</div></section>"""
    sec["angebote"] = f"""<section class="band"><div class="wrap">{section_head("Reitunterricht", "Unsere Angebote", '<a class="more" href="/reitunterricht">Alle Angebote und Preise</a>')}
  {offer_cards(info, 6)}</div></section>"""
    sec["pferde"] = (f"""<section class="wrap">{section_head("Schulpferde", "Unsere Schulpferde", '<a class="more" href="/pferde">Alle Schulpferde</a>')}
  <div class="horse-grid">{"".join(horse_card(h) for h in horses[:4])}</div></section>""" if horses else "")
    sec["termine"] = f"""<section class="wrap">{section_head("Termine", "Was als Nächstes ansteht", '<a class="more" href="/termine">Alle Termine</a>')}
  {f'<div class="events">{"".join(event_row(ev) for ev in events[:4])}</div>' if events else '<p>Termine folgen.</p>'}</section>"""
    sec["news"] = (f"""<section class="band light"><div class="wrap">{section_head("Aktuelles", "News aus dem Verein", '<a class="more" href="/news">Alle News</a>')}
  <div class="news-grid">{"".join(news_card(a) for a in articles[:3])}</div></div></section>""" if articles else "")
    sec["mitglied"] = f"""<section class="wrap split reveal"><div class="split-img">{picture("mitglied", "(min-width: 900px) 45vw, 100vw")}</div><div>
  {section_head("Mitglied werden", "Werde Teil des Vereins")}{paras_or_ph(info.get("site_membership_text"), "Text zur Mitgliedschaft")}
  <div class="btns"><a class="btn dark" href="/mitgliedschaft">Mitgliedschaft und Beiträge</a><a class="btn line" href="/kontakt?anliegen=mitgliedschaft#formular">Frage stellen</a></div></div></section>"""
    order = layout_order or [[k, True] for k in START_SECTIONS]
    body = hero + "".join(sec.get(k, "") for k, on in order if on)
    return layout("", "", seo_desc(info, "", f"{club}: Reitverein mit Reitunterricht, Schulpferden, Terminen und Mitgliedschaft."), body, info)


def page_verein(info):
    board = pairs(info.get("site_board"))
    board_html = ('<div class="board">' + "".join(f'<div class="person"><p class="role">{e(r)}</p><p class="name">{e(n) if n else placeholder("Name")}</p></div>'
                                                  for r, n in board) + "</div>") if board else f"<p>{placeholder('Vorstand, eine Zeile pro Person: Funktion | Name')}</p>"
    body = f"""{page_hero("verein", "Der Verein", "Über uns")}
<section class="wrap narrow">{section_head("Wer wir sind", info.get("club_name") or "RSC Frya Fresena e. V.")}{paras_or_ph(info.get("site_about"), "Über uns")}</section>
<section class="band light"><div class="wrap narrow">{section_head("Geschichte", "Unsere Geschichte")}{paras_or_ph(info.get("site_history"), "Geschichte des Vereins")}</div></section>
<section class="wrap">{section_head("Vorstand", "Ansprechpersonen")}{board_html}
  <p><a class="more" href="/kontakt#formular">Zum Kontaktformular</a></p></section>"""
    return layout("verein", "Verein", seo_desc(info, "verein", "Der Verein RSC Frya Fresena: wer wir sind, Geschichte und Vorstand."), body, info)


def page_lessons(info, horses):
    prices = pairs(info.get("site_lessons_prices"))
    price_html = ('<table class="prices"><tbody>' + "".join(f'<tr><th scope="row">{e(a)}</th><td>{e(b) if b else placeholder("Preis")}</td></tr>' for a, b in prices)
                  + "</tbody></table>") if prices else f"<p>{placeholder('Preise, eine Zeile pro Preis: Leistung | Preis')}</p>"
    volti = ""
    if info.get("site_volti_on") == "1":
        volti = f"""<section class="wrap split reveal" id="voltigieren"><div>{section_head("Voltigieren", "Turnen auf dem Pferd")}
  {paras_or_ph(info.get("site_volti_text"), "Text zum Voltigieren")}
  <a class="btn line" href="/kontakt?anliegen=probestunde#formular">Schnuppern anfragen</a></div>
  <div class="split-img">{picture("voltigieren", "(min-width: 900px) 45vw, 100vw")}</div></section>"""
    body = f"""{page_hero("unterricht", "Reiten lernen", "Reitunterricht")}
<section class="wrap narrow">{paras_or_ph(info.get("site_lessons_intro"), "Einleitung Reitunterricht")}</section>
<section class="band light"><div class="wrap">{section_head("Angebote", "Unterricht und Kurse")}{offer_cards(info)}</div></section>
{volti}
<section class="wrap narrow">{section_head("Preise", "Was es kostet")}{price_html}
  <p class="small muted">Fragen zu Unterricht und Preisen klären wir gern persönlich.</p></section>
{f'''<section class="wrap">{section_head("Schulpferde", "Mit wem du reitest", '<a class="more" href="/pferde">Alle Schulpferde</a>')}
  <div class="horse-grid">{"".join(horse_card(h) for h in horses[:4])}</div></section>''' if horses else ""}
<section class="cta-band"><div class="wrap"><h2>Lust auf eine Probestunde?</h2><p>Schreib uns kurz, wir melden uns mit einem Termin.</p>
  <a class="btn gold" href="/kontakt?anliegen=probestunde#formular">Probestunde anfragen</a></div></section>"""
    return layout("reitunterricht", "Reitunterricht", seo_desc(info, "reitunterricht", "Reitunterricht beim RSC Frya Fresena: Angebote, Kurse und Preise."), body, info)


def page_horses(info, horses):
    grid = f'<div class="horse-grid">{"".join(horse_card(h) for h in horses)}</div>' if horses else "<p>Hier stellen wir bald unsere Schulpferde vor.</p>"
    body = f"""{page_hero("pferde", "Unsere Vierbeiner", "Schulpferde")}
<section class="wrap narrow">{paras_or_ph(info.get("site_horses_intro"), "Einleitung Schulpferde")}</section>
<section class="wrap">{grid}</section>"""
    return layout("pferde", "Schulpferde", seo_desc(info, "pferde", "Die Schulpferde des RSC Frya Fresena."), body, info)


def page_facility(info):
    facts = [x.strip() for x in (info.get("site_facility_facts") or "").splitlines() if x.strip()]
    facts_html = ('<ul class="facts">' + "".join(f"<li>{e(x)}</li>" for x in facts) + "</ul>") if facts else f"<p>{placeholder('Eckdaten der Anlage, eine pro Zeile')}</p>"
    addr, maps = (info.get("site_address") or "").strip(), maps_url(info)
    body = f"""{page_hero("anlage", "Unser Zuhause", "Die Anlage")}
<section class="wrap split"><div>{section_head("Anlage", "Halle, Platz und Stall")}{paras_or_ph(info.get("site_facility_text"), "Beschreibung der Anlage")}</div>
  <div class="side-box">{section_head("Eckdaten", "Auf einen Blick")}{facts_html}</div></section>
<section class="band light"><div class="wrap split"><div>{section_head("Einstellplätze", "Pensionspferde")}{paras_or_ph(info.get("site_boarding_text"), "Text zu Einstellplätzen")}
  <a class="btn dark" href="/kontakt?anliegen=einstellplatz#formular">Einstellplatz anfragen</a></div>
  <div class="side-box">{section_head("Anfahrt", "So findest du uns")}<p>{lines(addr) if addr else placeholder("Anschrift der Anlage")}</p>
  {f'<a class="more" href="{e(maps)}" target="_blank" rel="noopener">Auf der Karte öffnen</a>' if maps else ''}</div></div></section>"""
    return layout("anlage", "Anlage", seo_desc(info, "anlage", "Die Reitanlage des RSC Frya Fresena: Halle, Platz, Stall und Einstellplätze."), body, info)


def page_events(info, events):
    groups, cur = [], None
    for ev in events:
        key = (ev["date"] or "")[:7]
        if key != cur:
            try:
                label = f"{MON[int(key[5:7]) - 1]} {key[:4]}"
            except ValueError:
                label = ""
            groups.append((label, []))
            cur = key
        groups[-1][1].append(ev)
    lst = "".join(f'<h2 class="month">{e(lbl)}</h2><div class="events">{"".join(event_row(ev, True) for ev in evs)}</div>' for lbl, evs in groups)
    arena = '<p><a class="more" href="/hallenplan">Zum Hallenplan</a></p>' if info.get("site_arena_public") == "1" else ""
    body = f"""{page_hero("termine", "Turniere, Lehrgänge, Vereinsleben", "Termine")}
<section class="wrap narrow">{paragraphs(info.get("site_events_intro")) if (info.get("site_events_intro") or "").strip() else ""}{lst or "<p>Termine folgen.</p>"}{arena}</section>"""
    return layout("termine", "Termine", seo_desc(info, "termine", "Turniere, Lehrgänge und Termine des RSC Frya Fresena."), body, info)


def page_arena(info, arena):
    if info.get("site_arena_public") != "1":
        return page_404(info)
    days = {}
    for b in arena:
        days.setdefault(b["date"], []).append(b)
    def row(b):
        what = e(b["kind"]) + (": " + e(b["title"]) if b["title"] else "")
        return f'<tr><td class="t">{e(b["start"])} bis {e(b["end"])}</td><td>{e(b["place"])}</td><td>{what}</td></tr>'
    rows = "".join(f'<h2 class="month">{e(fmt_date(d, True))}</h2><table class="arena"><tbody>{"".join(row(b) for b in items)}</tbody></table>'
                   for d, items in sorted(days.items()))
    body = f"""{page_hero("termine", "Halle und Platz", "Hallenplan")}
<section class="wrap narrow"><p>Belegung der nächsten zwei Wochen. Freie Zeiten sind nicht aufgeführt.</p>{rows or "<p>In den nächsten zwei Wochen ist nichts eingetragen.</p>"}</section>"""
    return layout("hallenplan", "Hallenplan", "Hallen- und Platzbelegung des RSC Frya Fresena.", body, info)


def page_membership(info, fees, form_url):
    table = (f'<table class="prices fees"><thead><tr><th>Beitragsart</th><th>Beitrag</th><th>Aufnahmegebühr</th></tr></thead><tbody>{fee_rows(fees)}</tbody></table>'
             if fees else f"<p>{placeholder('Beitragsarten (im Hub unter Mitglieder > Beitragsarten, Haken „auf der Webseite“)')}</p>")
    form = (f'<a class="btn dark" href="{e(form_url)}" target="_blank" rel="noopener">Beitrittsantrag (PDF)</a>' if form_url
            else f'<p>{placeholder("Beitrittsantrag als PDF (Datei in der Mediathek, im Hub unter Webseite > Texte verknüpfen)")}</p>')
    body = f"""{page_hero("mitglied", "Mitglied werden", "Mitgliedschaft")}
<section class="wrap narrow">{paras_or_ph(info.get("site_membership_text"), "Text zur Mitgliedschaft")}</section>
<section class="wrap narrow">{section_head("Beiträge", "Beitragsarten")}{table}</section>
<section class="band light"><div class="wrap narrow">{section_head("So geht es", "Beitritt")}
  <p>Hier findest du den Beitrittsantrag. Fragen zur Mitgliedschaft beantworten wir gern vorher.</p>
  <div class="btns">{form}<a class="btn line" href="/kontakt?anliegen=mitgliedschaft#formular">Frage zur Mitgliedschaft</a></div></div></section>"""
    return layout("mitgliedschaft", "Mitgliedschaft", seo_desc(info, "mitgliedschaft", "Mitglied werden beim RSC Frya Fresena: Beitragsarten und Beitrittsantrag."), body, info)


def page_contact(info, sent=None, preselect=""):
    form_msg = {"ok": '<p class="msg ok">Danke! Deine Nachricht ist angekommen, wir melden uns bei dir.</p>',
                "fehler": '<p class="msg err">Das hat nicht geklappt. Bitte prüfe deine Angaben und versuche es noch einmal.</p>'}.get(sent, "")
    pick = next((v for v, short, _, _ in CONTACT_TOPICS if short == preselect), CONTACT_TOPICS[0][0])
    mail, phone = (info.get("site_contact_email") or "").strip(), (info.get("site_phone") or "").strip()
    addr, maps = (info.get("site_address") or "").strip(), maps_url(info)
    topics = "".join(f'<label class="topic"><input type="radio" name="anliegen" value="{e(v)}" {"checked" if v == pick else ""}>'
                     f'<span><b>{e(label)}</b><small>{e(hint)}</small></span></label>' for v, _, label, hint in CONTACT_TOPICS)
    body = f"""{page_hero("kontakt", "Kontakt", "Schreib uns", "Probestunde, Mitgliedschaft, Einstellplatz oder eine Frage: Wir melden uns so schnell wie möglich.")}
<section class="wrap contact-wrap" id="formular">
  <div class="contact-main">{section_head("Kontaktformular", "Deine Nachricht")}
    {form_msg}
    <form class="contact" method="post" action="/kontakt">
      <fieldset class="topics"><legend>Worum geht es?</legend>{topics}</fieldset>
      <div class="row2"><label>Name<input name="name" required maxlength="100" autocomplete="name"></label>
        <label>Telefon (optional)<input name="phone" maxlength="40" autocomplete="tel"></label></div>
      <label>E-Mail<input name="email" type="email" maxlength="150" autocomplete="email"></label>
      <div class="row2"><label>Alter der reitenden Person (optional)<input name="age" maxlength="20" inputmode="numeric"></label>
        <label>Reiterfahrung (optional)<input name="experience" maxlength="200" placeholder="z. B. keine, Anfänger, Fortgeschritten"></label></div>
      <label>Nachricht<textarea name="message" rows="6" required maxlength="3000" placeholder="Erzähl uns kurz, worum es geht."></textarea></label>
      <label class="hp" aria-hidden="true">Bitte leer lassen<input name="website" tabindex="-1" autocomplete="off"></label>
      <label class="check"><input type="checkbox" name="consent" value="1" required> <span>Ich bin einverstanden, dass meine Angaben zur Beantwortung meiner Anfrage gespeichert werden. Mehr in der <a href="/datenschutz">Datenschutzerklärung</a>.</span></label>
      <p class="small muted">E-Mail oder Telefon reicht. Bei Kindern und Jugendlichen melden sich bitte die Eltern bei uns.</p>
      <button class="btn dark" type="submit">Nachricht senden</button>
    </form></div>
  <aside class="contact-side">
    <div class="side-box green"><p class="eyebrow">Direkt</p>
      <p><b>E-Mail</b><br>{f'<a href="mailto:{e(mail)}">{e(mail)}</a>' if mail else placeholder("Kontakt-E-Mail")}</p>
      {f'<p><b>Telefon</b><br><a href="tel:{e(re.sub(r"[^0-9+]", "", phone))}">{e(phone)}</a></p>' if phone else ''}</div>
    <div class="side-box"><p class="eyebrow">Anlage</p><p>{lines(addr) if addr else placeholder("Anschrift der Anlage")}</p>
      {f'<a class="more" href="{e(maps)}" target="_blank" rel="noopener">Anfahrt auf der Karte</a>' if maps else ''}</div>
    <div class="side-box"><p class="eyebrow">Newsletter</p><p>Termine und Neuigkeiten direkt ins Postfach.</p>
      <form class="foot-nl light" action="/newsletter#anmelden" method="get"><input name="email" type="email" maxlength="150" placeholder="Deine E-Mail" aria-label="E-Mail-Adresse für den Newsletter" autocomplete="email" required>
        <button class="btn dark" type="submit">Abonnieren</button></form></div>
  </aside>
</section>"""
    return layout("kontakt", "Kontakt", seo_desc(info, "kontakt", "Kontakt zum RSC Frya Fresena: Probestunde, Mitgliedschaft, Einstellplatz und Fragen."), body, info)


def page_links(data):
    """Link-Seite (/links, z. B. für die Instagram-Bio): Logo, Knöpfe aus dem Hub. Ohne Menü, nicht in Sitemap und Suchmaschinen."""
    info = data.get("info") or {}
    club = info.get("club_name") or "RSC Frya Fresena e. V."
    def btn(x):
        ext = x["url"].startswith("https://")
        return (f'<a class="lt-link{" hot" if x.get("hot") else ""}" href="{e(x["url"])}"' + (' target="_blank" rel="noopener"' if ext else "")
                + f'>{e(x["label"])}</a>')
    links = "".join(btn(x) for x in data.get("links") or [])
    social = social_links()
    return f"""<!doctype html>
<html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Links | {e(club)}</title>
<meta name="description" content="Alle Links des {e(club)}.">
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#0D393A">
<link rel="icon" href="/favicon.ico" sizes="any"><link rel="apple-touch-icon" href="/apple-touch-icon.png">
<style>{site_css()}</style>
</head><body class="page-links">
<main id="inhalt" class="lt">
  <div class="lt-box">
    <a href="/" aria-label="{e(club)} Startseite"><img class="lt-logo" src="{asset('logo.webp')}" alt="Logo {e(club)}" width="480" height="480"></a>
    <h1>{e(club)}</h1>
    <nav class="lt-links" aria-label="Links">{links or '<p class="lt-sub">Hier stehen bald unsere Links.</p>'}</nav>
    {f'<p class="lt-social">{social}</p>' if social else ''}
  </div>
  <footer class="maint-foot"><span>© {datetime.now().year} {e(club)}</span>
    <a href="/">Webseite</a><a href="/impressum">Impressum</a><a href="/datenschutz">Datenschutz</a></footer>
</main>
</body></html>"""


def maint_until_text(v):
    """„2026-11-15T18:00“ oder „2026-11-15“ als lesbarer Zeitpunkt, sonst leer."""
    v = (v or "").strip()
    try:
        if len(v) == 10:
            return fmt_date(v, weekday=True)
        dt = datetime.fromisoformat(v[:16])
        return f"{fmt_date(dt.date().isoformat(), weekday=True)}, {dt.hour:02d}:{dt.minute:02d} Uhr"
    except ValueError:
        return ""


def page_maintenance(data):
    """Eigene Seite im Wartungsmodus: Bild, Logo, Text aus dem Hub. Ohne Navigation, nicht für Suchmaschinen."""
    info, m = data.get("info") or {}, data.get("maint") or {}
    club = info.get("club_name") or "RSC Frya Fresena e. V."
    meta = image_meta()
    name = next((n for n in (MAINT_SLOT, "hero") if image_files(n, meta)[1]), "")
    bg = picture(name, "100vw", "maint-img", eager=True, meta=meta) if name else ""
    mail = (info.get("site_contact_email") or "").strip()
    until = maint_until_text(m.get("until"))
    title = (m.get("title") or "").strip() or "Wir sind gleich wieder da"
    preview = '<p class="maint-preview">Vorschau der Wartungsseite <a href="/">Zurück zur Webseite</a></p>' if m.get("preview") else ""
    return f"""<!doctype html>
<html lang="de"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} | {e(club)}</title>
<meta name="description" content="{e(club)}: Die Webseite wird gerade gewartet.">
<meta name="robots" content="noindex">
<meta name="theme-color" content="#0D393A">
<link rel="icon" href="/static/icon.png">
<style>{site_css()}</style>
</head><body class="page-wartung">
{preview}
<main id="inhalt" class="maint">
  <div class="maint-bg">{bg}</div>
  <div class="maint-box">
    <img class="maint-logo" src="{asset('logo.webp')}" alt="Logo {e(club)}" width="480" height="480">
    <p class="eyebrow">Wartungsarbeiten</p>
    <h1>{e(title)}</h1>
    <p class="maint-text">{lines(m.get("text"))}</p>
    {f'<p class="maint-until">Voraussichtlich wieder da: <b>{e(until)}</b></p>' if until else ''}
    {f'<div class="btns"><a class="btn gold" href="mailto:{e(mail)}">E-Mail schreiben</a></div>' if mail else ''}
  </div>
  <footer class="maint-foot"><span>© {datetime.now().year} {e(club)}</span>
    <a href="/impressum">Impressum</a><a href="/datenschutz">Datenschutz</a><a href="/login">Interner Bereich</a></footer>
</main>
</body></html>"""


def page_newsletter(info, n):
    """Newsletter: anmelden (Themen und Einwilligung), bestätigen und abmelden (nur über Knopf, damit Linkprüfer in Mailprogrammen nichts auslösen)."""
    mode = n.get("mode") or "form"
    msgs = {"ok": '<p class="msg ok">Fast geschafft! Wir haben dir eine E-Mail geschickt. Bitte bestätige darin deine Anmeldung, erst dann bekommst du den Newsletter.</p>',
            "fehler": '<p class="msg err">Das hat nicht geklappt. Bitte gib eine gültige E-Mail-Adresse an und bestätige die Einwilligung.</p>',
            "gruppe": '<p class="msg err">Bitte wähle mindestens ein Thema aus.</p>',
            "bestaetigt": '<p class="msg ok">Danke! Deine Anmeldung ist bestätigt. Du bekommst ab jetzt unseren Newsletter.</p>',
            "abgemeldet": '<p class="msg ok">Du bist abgemeldet und bekommst keinen Newsletter mehr.</p>',
            "abgelaufen": '<p class="msg err">Dieser Bestätigungslink ist abgelaufen oder wurde schon benutzt. Melde dich einfach noch einmal an.</p>'}
    if mode == "confirm":
        title = "Anmeldung bestätigen"
        main = (f'<p>Bitte bestätige mit dem Knopf, dass du unseren Newsletter erhalten möchtest.</p><form method="post" action="/newsletter/bestaetigen/{e(n.get("token", ""))}">'
                '<button class="btn dark" type="submit">Anmeldung bestätigen</button></form>' if n.get("valid") else msgs["abgelaufen"] + '<p><a class="more" href="/newsletter#anmelden">Zur Anmeldung</a></p>')
    elif mode == "unsub":
        title = "Newsletter abbestellen"
        main = (f'<p>Möchtest du unseren Newsletter wirklich abbestellen? Du kannst dich jederzeit wieder anmelden.</p><form method="post" action="/newsletter/abmelden/{e(n.get("token", ""))}">'
                '<button class="btn dark" type="submit">Newsletter abbestellen</button></form>' if n.get("valid")
                else '<p class="msg err">Dieser Abmeldelink stimmt nicht. Bitte nutze den Link aus einer aktuellen Ausgabe oder schreib uns über das <a href="/kontakt?anliegen=frage#formular">Kontaktformular</a>.</p>')
    else:
        title = "Newsletter"
        groups = n.get("groups") or []
        choose = ("".join(f'<label class="topic"><input type="checkbox" name="gruppe" value="{g["id"]}"><span><b>{e(g["name"])}</b>'
                          + (f'<small>{e(g["description"])}</small>' if g["description"] else "") + '</span></label>' for g in groups))
        main = f"""<p>Termine, Lehrgänge und Neuigkeiten aus dem Verein direkt in dein Postfach. Abbestellen geht jederzeit mit einem Klick.</p>
  {msgs.get(n.get("sent") or "", "")}
  <form class="contact" method="post" action="/newsletter" id="anmelden">
    {f'<fieldset class="topics"><legend>Welche Themen interessieren dich?</legend>{choose}</fieldset>' if groups else ''}
    <div class="row2"><label>E-Mail<input name="email" type="email" required maxlength="150" autocomplete="email" value="{e(n.get("email", ""))}"></label>
      <label>Vorname (optional)<input name="name" maxlength="80" autocomplete="given-name"></label></div>
    <label class="hp" aria-hidden="true">Bitte leer lassen<input name="website" tabindex="-1" autocomplete="off"></label>
    <label class="check"><input type="checkbox" name="consent" value="1" required> <span>{e(NL_CONSENT)} Mehr in der <a href="/datenschutz">Datenschutzerklärung</a>.</span></label>
    <button class="btn dark" type="submit">Newsletter abonnieren</button>
    <p class="small muted">Du bekommst gleich eine E-Mail mit einem Bestätigungslink. Erst nach dem Klick bist du angemeldet.</p>
  </form>"""
    body = f"""{page_hero("kontakt", "Immer auf dem Laufenden", title)}
<section class="wrap contact-wrap"><div class="contact-main">{main}</div></section>"""
    return layout("newsletter", title, "Newsletter des RSC Frya Fresena: Termine und Neuigkeiten per E-Mail.", body, info)


def legal_inline(t):
    """Eine Zeile Rechtstext: escapen, [Platzhalter] gelb markieren, Adressen und E-Mails verlinken."""
    out, pos = [], 0
    pat = re.compile(r"\[[^\]\n]{1,200}\]|https?://[^\s<>\"')]+[^\s<>\"').,;:]|[\w.+-]+@[\w-]+(?:\.[\w-]+)+|(?<![\w/])/(?:kontakt|impressum|datenschutz)\b")
    for m in pat.finditer(t):
        out.append(e(t[pos:m.start()]))
        x = m.group(0)
        if x.startswith("["):
            out.append(f'<span class="ph">{e(x[1:-1])}</span>')
        elif "@" in x and not x.startswith("http"):
            out.append(f'<a href="mailto:{e(x)}">{e(x)}</a>')
        elif x.startswith("/"):
            out.append(f'<a href="{e(x)}">{e(x)}</a>')
        else:
            out.append(f'<a href="{e(x)}" target="_blank" rel="noopener">{e(x)}</a>')
        pos = m.end()
    out.append(e(t[pos:]))
    return "".join(out)


def legal_html(text, info):
    """Rechtstext aus dem Hub: "## " Überschrift, "### " Unterüberschrift, "- " Liste, Leerzeile = Absatz. Kein HTML aus dem Hub."""
    ig = (info.get("instagram") or "").lstrip("@")
    text = str(text or "").replace("\r", "").replace("{E-Mail}", (info.get("site_contact_email") or "").strip() or "[Kontakt-E-Mail]")
    text = text.replace("{Instagram}", f"https://www.instagram.com/{ig}/" if ig else "[Instagram-Profil]")
    out = []
    for block in re.split(r"\n\s*\n", text):
        rows = [r.rstrip() for r in block.strip().split("\n") if r.strip()]
        if not rows:
            continue
        if rows[0].startswith("### "):
            out.append(f"<h3>{legal_inline(rows[0][4:])}</h3>")
            rows = rows[1:]
        elif rows[0].startswith("## "):
            out.append(f"<h2>{legal_inline(rows[0][3:])}</h2>")
            rows = rows[1:]
        if rows and all(r.lstrip().startswith("- ") for r in rows):
            out.append("<ul>" + "".join(f"<li>{legal_inline(r.lstrip()[2:])}</li>" for r in rows) + "</ul>")
        elif rows:
            out.append("<p>" + "<br>".join(legal_inline(r) for r in rows) + "</p>")
    return "".join(out)


def page_legal(slug, info):
    title, key = LEGAL[slug]
    text = info.get(key) or ""
    content = legal_html(text, info) if text.strip() else f"<p>{placeholder(title)}</p>"
    body = f'<section class="wrap narrow legal"><h1>{e(title)}</h1><div class="legal-text">{content}</div></section>'
    return layout(slug, title, f"{title} des RSC Frya Fresena.", body, info)


def page_404(info):
    body = '<section class="wrap narrow legal"><h1>Seite nicht gefunden</h1><p>Diese Seite gibt es nicht. <a href="/">Zur Startseite</a></p></section>'
    return layout("", "Nicht gefunden", "", body, info, noindex=True)


PAGES = ("", "verein", "reitunterricht", "pferde", "anlage", "termine", "hallenplan", "news", "mitgliedschaft", "kontakt", "impressum", "datenschutz")


# ---------------------------------------------------------------------------
# News: Liste, Einzelartikel
# ---------------------------------------------------------------------------
class _HtmlFilter(HTMLParser):
    """Lässt nur einfache Textauszeichnung durch (Absätze, Überschriften, Listen, Fett, Kursiv, Links, Zitate).
    Skripte, Styles, Einbettungen, Bilder, Formulare und alle Attribute außer einem geprüften href fallen weg."""
    ALLOWED = {"p", "br", "strong", "b", "em", "i", "u", "h2", "h3", "h4", "ul", "ol", "li", "a", "blockquote", "hr"}
    VOID = {"br", "hr"}
    DROP = {"script", "style", "iframe", "object", "embed", "template", "svg", "math", "noscript", "textarea", "select", "title", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.DROP:
            self.skip += 1
            return
        if self.skip or tag not in self.ALLOWED:
            return
        if tag in self.VOID:
            self.out.append(f"<{tag}>")
            return
        extra = ""
        if tag == "a":
            href = re.sub(r"[\x00-\x20]", "", dict(attrs).get("href") or "")
            if re.match(r"(https?://|mailto:|/(?!/)|#)", href, re.I):
                ext = href.lower().startswith(("http://", "https://"))
                extra = f' href="{e(href)}"' + (' target="_blank" rel="noopener"' if ext else "")
        self.out.append(f"<{tag}{extra}>")
        self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if tag in self.VOID and not self.skip:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in self.DROP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag not in self.stack:
            return
        while self.stack:   # offene Tags bis zu diesem schließen, damit nichts aus dem Artikel herausragt
            t = self.stack.pop()
            self.out.append(f"</{t}>")
            if t == tag:
                break

    def handle_data(self, data):
        if not self.skip:
            self.out.append(e(data))

    def result(self):
        self.close()
        return "".join(self.out) + "".join(f"</{t}>" for t in reversed(self.stack))


def clean_html(t):
    """HTML aus dem Hub für die Webseite: nur erlaubte Tags, alles andere als Text oder entfernt."""
    f = _HtmlFilter()
    f.feed(str(t or ""))
    return f.result()


def render_blocks(blocks):
    """Blöcke aus dem Editor (vom Server schon geprüft). Fließtext darf einfache Auszeichnung enthalten (clean_html), sonst nur Text."""
    out = []
    for b in blocks:
        t = b["t"]
        if t == "p":
            out.append(f'<p>{clean_html(b["text"]).replace(chr(10), "<br>")}</p>')
        elif t == "h":
            out.append(f"<h2>{e(b['text'])}</h2>")
        elif t == "quote":
            by = f"<footer>{e(b['by'])}</footer>" if b.get("by") else ""
            out.append(f'<blockquote><p>{lines(b["text"])}</p>{by}</blockquote>')
        elif t == "list":
            out.append("<ul>" + "".join(f"<li>{clean_html(x)}</li>" for x in b["items"]) + "</ul>")
        elif t == "highlight":
            out.append(f'<p class="art-highlight">{e(b["text"])}</p>')
        elif t == "cta":
            btn = f'<a class="btn dark" href="{e(b["link"])}">{e(b["label"] or "Mehr erfahren")}</a>' if b.get("link") else ""
            out.append(f'<aside class="art-cta"><p>{lines(b["text"])}</p>{btn}</aside>')
        elif t == "image":
            cap = f"<figcaption>{e(b['caption'])}</figcaption>" if b.get("caption") else ""
            out.append(f'<figure class="art-img"><img src="{e(b["src"])}" alt="{e(b.get("alt"))}" loading="lazy" decoding="async">{cap}</figure>')
        elif t == "note":
            out.append(f'<p class="art-note">{e(b["text"])}</p>')
    return "".join(out)


def plain_text(t, fmt="Text"):
    """Reiner Text (für Vorspann, Beschreibung für Suchmaschinen): bei HTML ohne Tags."""
    if fmt not in ("HTML", "Blöcke"):
        return str(t or "")
    inline = re.sub(r"</?(strong|b|em|i|u|a)\b[^>]*>", "", clean_html(t))
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]*>", " ", inline))).strip()


def paragraphs(t):
    """Text aus dem Hub als Absätze: Leerzeile = neuer Absatz, einfacher Umbruch bleibt erhalten. Kein HTML."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", str(t or "").replace("\r", "")) if b.strip()]
    return "".join(f"<p>{lines(b)}</p>" for b in blocks)


def page_news(info, articles):
    body = f"""{page_hero("news", "Aktuelles", "News")}
<section class="wrap">{f'<div class="news-grid">{"".join(news_card(a) for a in articles)}</div>' if articles else '<p>Hier erscheinen bald die ersten Neuigkeiten aus dem Verein.</p>'}</section>"""
    return layout("news", "News", seo_desc(info, "news", "Neuigkeiten und Berichte aus dem RSC Frya Fresena."), body, info)


def article_footer(info, a):
    """Unter jedem Artikel: Kasten mit Knopf zur Probestunde (wenn ein Text gepflegt ist) und der Fotograf mit Link."""
    text = (info.get("site_news_cta_text") or "").strip()
    label = (info.get("site_news_cta_button") or "").strip() or "Probestunde anfragen"
    cta = (f'<aside class="art-cta"><p>{lines(text)}</p><a class="btn dark" href="/kontakt?anliegen=probestunde#formular">{e(label)}</a></aside>'
           if text else "")
    by = a.get("photo_by") or ""
    who = (f'<a href="{e(a["photo_url"])}" target="_blank" rel="noopener">{e(by)}</a>' if a.get("photo_url") else e(by)) if by else ""
    return cta + (f'<p class="art-credit">Foto: {who}</p>' if who else "")


def page_article(info, a):
    img = f'<figure class="art-img"><img src="{e(a["image"])}" alt="{e(a["image_alt"] or a["title"])}" decoding="async"></figure>' if a["image"] else ""
    content = (render_blocks(a.get("blocks") or []) if a.get("format") == "Blöcke" else clean_html(a["body"]) if a.get("format") == "HTML"
               else paragraphs(a["body"]))
    body = f"""<article class="wrap narrow article">
  <p class="eyebrow"><a href="/news">News</a></p>
  <h1>{e(a["title"])}</h1>
  <p class="nc-meta">{e(fmt_date(a["date"], True))}</p>
  {f'<p class="lead">{e(a["teaser"])}</p>' if a["teaser"] else ""}
  {img}
  <div class="art-body">{content}</div>
  {article_footer(info, a)}
  <p><a class="more" href="/news">Alle News</a></p>
</article>"""
    base = getattr(_ctx, "base", "")
    head = ""
    club = info.get("club_name") or "RSC Frya Fresena e. V."
    if base:
        head = jsonld({"@context": "https://schema.org", "@type": "NewsArticle", "headline": a["title"][:110], "datePublished": a["date"], "dateModified": a["date"],
                       "url": f"{base}/news/{a['slug']}", "image": [base + (a["image"] or "/static/logo.png")],
                       "publisher": {"@type": "SportsOrganization", "name": club, "logo": base + "/static/logo.png"},
                       "author": {"@type": "Organization", "name": club, "url": base + "/"}})
    desc = " ".join((a["teaser"] or plain_text(a["body"], a.get("format")).strip().split("\n")[0]).split())[:160]
    return layout("news", a["title"], desc, body, info, extra_head=head, canonical=f"/news/{a['slug']}")


def render(slug, data, sent=None):
    site = data.get("site") or {}
    _ctx.images = site.get("images") or {}
    _ctx.base = (data.get("base") or "").rstrip("/")   # absolute Adresse für Suchmaschinen (canonical, strukturierte Daten)
    _ctx.events = data.get("events") or []
    _ctx.maint_bar = bool(data.get("maint_bar"))
    _ctx.nav = data.get("nav")
    try:
        if slug == "wartung":
            return page_maintenance(data)
        return render_page(slug, data, sent, site)
    finally:
        _ctx.images, _ctx.base, _ctx.events, _ctx.maint_bar, _ctx.nav = {}, "", [], False, None


def render_page(slug, data, sent, site):
    info = data["info"]
    if slug == "":
        return page_start(info, data.get("horses") or [], data.get("events") or [], data.get("articles") or [], site.get("layout"))
    if slug == "verein":
        return page_verein(info)
    if slug == "reitunterricht":
        return page_lessons(info, data.get("horses") or [])
    if slug == "pferde":
        return page_horses(info, data.get("horses") or [])
    if slug == "anlage":
        return page_facility(info)
    if slug == "termine":
        return page_events(info, data.get("events") or [])
    if slug == "hallenplan":
        return page_arena(info, data.get("arena") or [])
    if slug == "mitgliedschaft":
        return page_membership(info, data.get("fees") or [], data.get("membership_form") or "")
    if slug == "news":
        return page_news(info, data.get("articles") or [])
    if slug == "news-artikel" and data.get("article"):
        return page_article(info, data["article"])
    if slug == "links":
        return page_links(data)
    if slug == "newsletter":
        return page_newsletter(info, data.get("nl") or {})
    if slug == "kontakt":
        return page_contact(info, sent, data.get("preselect") or "")
    if slug in LEGAL:
        return page_legal(slug, info)
    return page_404(info)


# ---------------------------------------------------------------------------
# Suchmaschinen: strukturierte Daten (schema.org als JSON-LD), Sitemap, robots.txt
# ---------------------------------------------------------------------------
def berlin_offset(d):
    """Deutsche Zeitzone: Sommerzeit vom letzten Sonntag im März bis zum letzten Sonntag im Oktober."""
    last_sun = lambda m: max(date(d.year, m, day) for day in range(25, 32) if date(d.year, m, day).weekday() == 6)
    return "+02:00" if last_sun(3) <= d < last_sun(10) else "+01:00"


def jsonld(obj):
    return '<script type="application/ld+json">' + json.dumps(obj, ensure_ascii=False).replace("</", "<\\/") + "</script>"


def org_ld(info, base):
    obj = {"@context": "https://schema.org", "@type": "SportsOrganization", "name": info.get("club_name") or "RSC Frya Fresena e. V.",
           "sport": "Reitsport", "url": base + "/", "logo": base + "/static/logo.png"}
    if (info.get("site_address") or "").strip():
        obj["address"] = " ".join(info["site_address"].split())
    same = [s["url"] for s in (getattr(_ctx, "nav", None) or {}).get("social", [])]
    if same:
        obj["sameAs"] = same
    return obj


def event_ld(ev, info, base):
    """Termin als Event; nur mit Datum. Ohne Uhrzeit nur das Datum."""
    try:
        d = date.fromisoformat(ev["date"])
    except (TypeError, ValueError):
        return None
    t = (ev.get("time") or "").strip()
    start = f"{ev['date']}T{t}:00{berlin_offset(d)}" if len(t) == 5 and t[2] == ":" else ev["date"]
    club = info.get("club_name") or "RSC Frya Fresena e. V."
    obj = {"@context": "https://schema.org", "@type": "Event", "name": ev["title"], "startDate": start,
           "eventStatus": "https://schema.org/EventScheduled", "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
           "location": {"@type": "Place", "name": ev.get("place") or club, **({"address": " ".join(info["site_address"].split())} if (info.get("site_address") or "").strip() else {})},
           "organizer": {"@type": "SportsOrganization", "name": club, "url": base + "/"}, "url": f"{base}/termine#termin-{ev['id']}"}
    if ev.get("end_date"):
        obj["endDate"] = ev["end_date"]
    if ev.get("description"):
        obj["description"] = " ".join(ev["description"].split())[:300]
    return obj


# Einträge der sitemap.xml mit Standard-Priorität; im Hub unter Webseite > Sitemap an/aus und Priorität (Einstellung web_sitemap)
SITEMAP_PAGES = [("", "Startseite", "1.0"), ("verein", "Verein", "0.7"), ("reitunterricht", "Reitunterricht", "0.9"), ("pferde", "Schulpferde", "0.7"),
                 ("anlage", "Anlage", "0.6"), ("termine", "Termine", "0.8"), ("news", "News (Übersicht)", "0.7"),
                 ("news/*", "News-Artikel (die 3 neuesten)", "0.6"), ("mitgliedschaft", "Mitgliedschaft", "0.8"), ("kontakt", "Kontakt", "0.6"),
                 ("impressum", "Impressum", "0.2"), ("datenschutz", "Datenschutz", "0.2")]
SITEMAP_PRIOS = tuple(f"{i / 10:.1f}" for i in range(11))


def sitemap(base, articles=(), cfg=None, noindex=False):
    """sitemap.xml mit den eingeschalteten öffentlichen Seiten und Artikeln; leer, solange Suchmaschinen ausgeschlossen sind."""
    rows = ""
    if not noindex:
        cfg = cfg or {}
        use = {k: cfg.get(k, [True, p]) for k, _, p in SITEMAP_PAGES}
        rows = "".join(f"<url><loc>{escape(base)}/{k}</loc><priority>{use[k][1]}</priority></url>"
                       for k, _, _ in SITEMAP_PAGES if use[k][0] and k != "news/*")
        if use["news/*"][0]:
            newest = sorted(articles, key=lambda a: a["date"] or "", reverse=True)[:3]
            rows += "".join(f"<url><loc>{escape(base)}/news/{escape(a['slug'])}</loc><lastmod>{escape(a['date'])}</lastmod>"
                            f"<priority>{use['news/*'][1]}</priority></url>" for a in newest)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>\n'


def robots(base, noindex=False):
    """robots.txt: solange die Domain vorläufig ist (site_noindex = 1), alles gesperrt."""
    if noindex:
        return "User-agent: *\nDisallow: /\n"
    return ("User-agent: *\nAllow: /\nAllow: /static/logo.png\nAllow: /static/icon.png\nAllow: /static/icon-192.png\nDisallow: /hub\nDisallow: /api/\n"
            "Disallow: /static/\nDisallow: /media/\nDisallow: /login\nDisallow: /bild/\nDisallow: /invite/\nDisallow: /passwort-\nDisallow: /links\n"
            + (f"Sitemap: {base}/sitemap.xml\n" if base else ""))
