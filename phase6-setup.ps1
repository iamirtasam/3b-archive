# ============================================================
#  Phase 6 setup — STEP 1
#  NOTE: If PowerShell says "running scripts is disabled", just
#  paste this one command into an ADMIN PowerShell instead:
#      wsl --install --no-launch
#  Installs the WSL system component. Does NOT install Ubuntu
#  yet, does NOT touch C: beyond ~100 MB, does NOT auto-reboot.
# ============================================================

# --- admin check ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Right-click this script and 'Run with PowerShell' as Administrator." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 1
}

Write-Host "==> Enabling WSL (system component only)..." -ForegroundColor Cyan
wsl --install --no-launch

Write-Host ""
Write-Host "==> Done. A REBOOT is required to finish enabling virtualization." -ForegroundColor Green
Write-Host "    Reboot whenever your game is done - nothing will restart on its own."
Write-Host ""
Write-Host "==> After the reboot, run:  phase6-step2.ps1  (NO admin needed)" -ForegroundColor Cyan
Read-Host "Press Enter to close"
