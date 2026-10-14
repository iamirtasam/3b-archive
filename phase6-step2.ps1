# ============================================================
#  Phase 6 setup — STEP 2 (normal PowerShell, NO admin needed)
#  Run this AFTER the reboot. Installs Ubuntu onto H: (not C:)
#  and creates the default user.
# ============================================================

Write-Host "==> Installing Ubuntu to H:\WSL\Ubuntu (virtual disk lands there, not C:)..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path "H:\WSL" | Out-Null
wsl --install -d Ubuntu --location H:\WSL\Ubuntu --no-launch
if ($LASTEXITCODE -ne 0) {
    Write-Host "Install failed (exit $LASTEXITCODE). Common cause: reboot still pending." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host ""
Write-Host "==> First launch: Ubuntu will open in a console window." -ForegroundColor Green
Write-Host "    It asks for a UNIX username (suggest: dev) and a password (any, remember it)."
Write-Host "    That password is only for sudo inside Ubuntu - the project doesn't use it."
Write-Host ""
wsl -d Ubuntu

Write-Host ""
Write-Host "Ubuntu ready. Tell the assistant to continue Phase 6." -ForegroundColor Green
Read-Host "Press Enter to close"
