# RSC Frya Fresena Hub: Installation auf dem VPS mit einem Befehl vom PC aus (Windows PowerShell 5.1 oder neuer).
# Liest die Repository-Adresse aus Git, prueft ob alles gepusht ist, kopiert deploy/install-vps.sh auf den VPS
# und startet es dort. Alle Rueckfragen (sudo-Passwort, Admin-Passwort, Proxy-Umstellung) beantwortest du im selben Fenster.
#
# Aufruf im Projektordner (ExecutionPolicy nur fuer diesen einen Aufruf):
#   powershell -ExecutionPolicy Bypass -File deploy\installiere-vps.ps1 -Server <ip> -Benutzer <benutzer> -Schluessel <name>
# Schluessel = Name der Datei in %USERPROFILE%\.ssh (ohne Endung) oder ein voller Pfad.

param(
  [string]$Server,
  [string]$Benutzer,
  [string]$Schluessel,
  [string]$Domain = "reiten.silasgrbwskfotografie.de"
)
$ErrorActionPreference = "Continue"   # Fehler werden unten selbst geprueft (Stop bricht in PowerShell 5.1 schon bei Meldungen von git ab)

function Frage([string]$text) { $a = Read-Host $text; return $a.Trim() }

if (-not $Server)     { $Server = Frage "IP oder Name des VPS" }
if (-not $Benutzer)   { $Benutzer = Frage "SSH-Benutzer auf dem VPS" }
if (-not $Schluessel) { $Schluessel = Frage "SSH-Schluessel (Dateiname in $env:USERPROFILE\.ssh, ohne Endung)" }
if ($Schluessel -notmatch '[\\/]') { $Schluessel = Join-Path "$env:USERPROFILE\.ssh" $Schluessel }
if (-not (Test-Path $Schluessel)) { throw "Schluessel nicht gefunden: $Schluessel" }
foreach ($v in @($Server, $Benutzer, $Domain)) {
  if ($v -notmatch '^[A-Za-z0-9._-]+$') { throw "Ungueltige Angabe: $v" }
}

# Git finden (PATH oder GitHub Desktop)
$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if (-not $git) {
  $git = Get-ChildItem "$env:LOCALAPPDATA\GitHubDesktop\app-*\resources\app\git\cmd\git.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $git) { throw "Git nicht gefunden (weder im PATH noch in GitHub Desktop)." }

$root = Split-Path $PSScriptRoot -Parent
$script = Join-Path $PSScriptRoot "install-vps.sh"
if (-not (Test-Path $script)) { throw "deploy\install-vps.sh fehlt." }

# Repository-Adresse und Stand pruefen
$repo = (& $git -C $root remote get-url origin 2>$null)
if (-not $repo) { $repo = Frage "Repository-Adresse (z. B. https://github.com/<konto>/<repository>.git)" }
$repo = "$repo".Trim()
if ($repo -notmatch '^[A-Za-z0-9@:/._-]+$') { throw "Ungueltige Repository-Adresse: $repo" }
& $git -C $root fetch --quiet 2>$null
$stand = (& $git -C $root status -sb 2>$null | Select-Object -First 1)
if ($stand -match 'ahead') {
  Write-Host "ACHTUNG: Es gibt Commits, die noch nicht gepusht sind ($stand)." -ForegroundColor Yellow
  Write-Host "Der VPS holt den Stand von GitHub. Bitte zuerst in GitHub Desktop 'Push origin' klicken."
  if ((Frage "Trotzdem weitermachen? (j/N)") -notmatch '^[jJ]$') { exit 1 }
}

Write-Host ""
Write-Host "Server:     $Benutzer@$Server"
Write-Host "Domain:     $Domain"
Write-Host "Repository: $repo"
Write-Host ""

Write-Host "1/2 Kopiere install-vps.sh auf den VPS ..."
& scp -i $Schluessel $script "${Benutzer}@${Server}:/tmp/install-vps.sh"
if ($LASTEXITCODE -ne 0) { throw "scp fehlgeschlagen (Server, Benutzer oder Schluessel pruefen)." }

Write-Host "2/2 Starte die Installation (sudo fragt nach dem Passwort von $Benutzer) ..."
& ssh -t -i $Schluessel "${Benutzer}@${Server}" "sudo bash /tmp/install-vps.sh --repo '$repo' --domain '$Domain'; rc=`$?; rm -f /tmp/install-vps.sh; exit `$rc"
if ($LASTEXITCODE -ne 0) { Write-Host "Die Installation wurde mit einem Fehler beendet (Meldung oben)." -ForegroundColor Yellow; exit $LASTEXITCODE }
Write-Host ""
Write-Host "Fertig: https://$Domain  und  https://$Domain/hub" -ForegroundColor Green
