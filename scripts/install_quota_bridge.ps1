# ==============================================================================
# One-Line Installer (Windows PowerShell):
# Antigravity Real Quota -> 24/7 LIVE RPG Office Full-Auto Bridge
# ==============================================================================
param(
    [string]$GithubToken = ""
)

$ErrorActionPreference = "Stop"
$DestDir = Join-Path $HOME ".gemini\antigravity-cli"
$DestScript = Join-Path $DestDir "agy_quota_sync.py"
$RawUrl = "https://raw.githubusercontent.com/howardKung/project-lifeblood-rpg-office/main/scripts/agy_quota_sync.py"

New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
Write-Host "⬇️  Downloading agy_quota_sync.py -> $DestScript ..."
Invoke-WebRequest -Uri $RawUrl -UseBasicParsing -OutFile $DestScript

if ($GithubToken -ne "") {
    python $DestScript --configure-agy --github-token $GithubToken
} else {
    python $DestScript --configure-agy
}

$GhCmd = Get-Command gh -ErrorAction SilentlyContinue
if ($GhCmd) {
    gh auth status *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ 偵測到本機 gh CLI 已登入 GitHub，免額外設定 Token，開箱即用！" -ForegroundColor Green
        exit 0
    }
}

$EnvFile = Join-Path $DestDir "rpg_office_env.json"
if ((Test-Path $EnvFile) -or ($env:GITHUB_TOKEN)) {
    Write-Host "✅ 已設定 GitHub Token，開箱即用！" -ForegroundColor Green
} else {
    Write-Host "⚠️  提醒：本機尚未登入 gh CLI。請執行 'gh auth login'，或設定 GitHub PAT：" -ForegroundColor Yellow
    Write-Host "   python `"$DestScript`" --configure-agy --github-token <YOUR_GITHUB_PAT>"
}
