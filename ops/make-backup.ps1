<#
.SYNOPSIS
    Build a Google-Drive-ready backup zip of this project.

.DESCRIPTION
    Includes: all git-tracked files, all untracked-but-real work (the many
    new routers/domains that aren't committed yet), and the SQLite database
    so trading history, strategies and account state survive.

    EXCLUDES SECRETS BY DESIGN. .env files, credentials/*.json and the
    OpenRouter/Discord/Alpaca keys they hold are deliberately left out: this
    archive is meant to be safe to put in cloud storage, and those keys grant
    live real-money brokerage access. Restoring means re-entering them from a
    password manager. -IncludeSecrets overrides that, and prints a warning.

    Also excludes .venv, node_modules and build output -- reinstallable, and
    they dominate the archive size otherwise.

    NOTE: the database still contains ENCRYPTED broker credentials and session
    tokens. It is far less dangerous than plaintext keys, but the archive is
    not "public safe" -- keep the Drive folder private.
#>
[CmdletBinding()]
param(
    [string]$OutDir = "$env:USERPROFILE\Desktop",
    [switch]$IncludeSecrets
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$stamp   = Get-Date -Format 'yyyyMMdd-HHmm'
$suffix  = if ($IncludeSecrets) { 'WITH-SECRETS' } else { 'no-secrets' }
$zipPath = Join-Path $OutDir "NewWorldTrading-backup-$stamp-$suffix.zip"
$staging = Join-Path $env:TEMP "nwt-backup-$stamp"

# Paths that must never reach a cloud backup unless explicitly asked for.
$secretPatterns = @(
    '*.env', '.env', '.env.*', 'credentials/*.json'
)
# Reinstallable bulk / machine-local noise.
$bulkPrefixes = @(
    '.venv/', 'ops/logs/', 'dashboard/landing/node_modules/',
    'dashboard/landing/dist/', '__pycache__/', '.git/'
)

function Test-IsSecret([string]$rel) {
    if ($IncludeSecrets) { return $false }
    $leaf = Split-Path $rel -Leaf
    # .env.example / *.json.example are TEMPLATES, not secrets -- they carry
    # placeholder values and the restore instructions depend on them. Checked
    # first because '.env.*' below would otherwise swallow .env.example.
    if ($leaf -like '*.example') { return $false }
    if ($leaf -eq '.env' -or $leaf -like '.env.*' -or $leaf -like '*.env') { return $true }
    if ($rel -like 'credentials/*' -and $leaf -like '*.json') { return $true }
    return $false
}

function Test-IsBulk([string]$rel) {
    foreach ($p in $bulkPrefixes) { if ($rel -like "$p*" -or $rel -like "*/$p*") { return $true } }
    return $false
}

Write-Output "Collecting files..."
$tracked   = & git ls-files
$untracked = & git ls-files --others --exclude-standard

$all = @($tracked) + @($untracked) | Sort-Object -Unique

$included = New-Object System.Collections.Generic.List[string]
$skippedSecrets = New-Object System.Collections.Generic.List[string]
foreach ($rel in $all) {
    if ([string]::IsNullOrWhiteSpace($rel)) { continue }
    if (Test-IsBulk $rel) { continue }
    if (Test-IsSecret $rel) { $skippedSecrets.Add($rel); continue }
    if (Test-Path -LiteralPath $rel -PathType Leaf) { $included.Add($rel) }
}

if (Test-Path $staging) { Remove-Item $staging -Recurse -Force }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

foreach ($rel in $included) {
    $dest = Join-Path $staging $rel
    $destDir = Split-Path $dest -Parent
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
    Copy-Item -LiteralPath $rel -Destination $dest -Force
}

# A restore note travels with the archive so future-you knows what's missing.
$readme = @"
NewWorldTrading backup - $stamp
=====================================

Contents : project source + dashboard/storage/data/backtest.db
Excluded : .venv, node_modules, build output, ops/logs
Secrets  : $(if ($IncludeSecrets) { 'INCLUDED - treat this archive as a live credential. Do not share.' } else { 'EXCLUDED (.env files, credentials/*.json)' })

RESTORE
-------
1. Unzip, then from the repo root:
       python -m venv .venv
       .venv\Scripts\pip install -r requirements.txt
       .venv\Scripts\pip install -r requirements-discord.txt   (for the Discord bot)
2. Recreate dashboard/.env from your password manager. Keys needed:
       ALPACA_API_KEY / ALPACA_SECRET_KEY            (paper)
       ALPACA_LIVE_API_KEY / ALPACA_LIVE_SECRET_KEY  (LIVE - real money)
       DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DISCORD_CHANNEL_ID,
       DISCORD_SUPPORT_CHANNEL_ID, DISCORD_SUPPORT_ROLE_ID
       OPENROUTER_SUPPORT_API_KEY
   Use .env.example as the template - it lists every key with comments.
3. The Connections page in the app also stores broker keys (encrypted, in
   backtest.db). Those DO survive in this archive.
4. Re-register the scheduled tasks:
       ops\install-keepalive-task.ps1          (web app, every 4h)
       ops\install-discord-keepalive-task.ps1  (Discord bot, every 4h)

NOTE: backtest.db contains ENCRYPTED broker credentials and session tokens.
Keep this archive in a private folder.
"@
$readme | Out-File -FilePath (Join-Path $staging 'RESTORE-README.txt') -Encoding utf8

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $staging '*') -DestinationPath $zipPath -CompressionLevel Optimal
Remove-Item $staging -Recurse -Force

$size = '{0:N1} MB' -f ((Get-Item $zipPath).Length / 1MB)
Write-Output ""
Write-Output "Backup written: $zipPath  ($size)"
Write-Output "Files included: $($included.Count)"
if (-not $IncludeSecrets) {
    Write-Output "Secrets excluded: $($skippedSecrets.Count) file(s) -"
    foreach ($s in $skippedSecrets) { Write-Output "    $s" }
} else {
    Write-Warning "This archive CONTAINS PLAINTEXT SECRETS including live Alpaca keys."
}
