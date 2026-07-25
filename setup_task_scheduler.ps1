# setup_task_scheduler.ps1
# ============================================================
# Sets up a Windows Task Scheduler job to run the BMS watcher
# every 30 minutes automatically on YOUR laptop.
#
# This is MORE RELIABLE than GitHub Actions because:
#   - Your laptop uses a residential IP (BMS allows it)
#   - GitHub Actions uses Azure datacenter IPs (BMS blocks them)
#
# HOW TO RUN THIS SCRIPT:
#   Right-click -> "Run with PowerShell" (as Administrator)
# ============================================================

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonExe  = Join-Path $ScriptDir ".venv\Scripts\pythonw.exe"  # pythonw = no console window popup
$WatcherPy  = Join-Path $ScriptDir "watcher.py"
$TaskName   = "BMS Odyssey IMAX Watcher"

# Verify venv exists
if (-not (Test-Path $PythonExe)) {
    Write-Host ""
    Write-Host "ERROR: Virtual environment not found." -ForegroundColor Red
    Write-Host "Please run 'run_watcher.bat' first to set up the environment." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  BMS Odyssey IMAX Watcher" -ForegroundColor Cyan
Write-Host "  Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Script folder : $ScriptDir"
Write-Host "Python exe    : $PythonExe"
Write-Host "Watcher script: $WatcherPy"
Write-Host ""

# Remove existing task if it exists
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing task '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# Action: run pythonw watcher.py --single-check
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$WatcherPy --single-check" `
    -WorkingDirectory $ScriptDir

# Trigger: every 30 minutes, starting now, until Aug 2 2026
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date) `
    -RepetitionInterval  (New-TimeSpan -Minutes 30) `
    -RepetitionDuration  ([TimeSpan]::FromDays(8))   # Run for 8 days to cover Aug 1

# Settings: run even if on battery, wake to run, allow parallel if needed
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew

# Register the task for the current user
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host ""
Write-Host "SUCCESS! Task '$TaskName' is registered." -ForegroundColor Green
Write-Host ""
Write-Host "What happens now:" -ForegroundColor White
Write-Host "  - Windows will run the watcher every 30 minutes" -ForegroundColor Gray
Write-Host "  - It runs silently in the background (no window popup)" -ForegroundColor Gray
Write-Host "  - All checks are logged to: $ScriptDir\watcher.log" -ForegroundColor Gray
Write-Host "  - You will get an email the moment IMAX Odyssey tickets go live" -ForegroundColor Gray
Write-Host ""
Write-Host "To check logs anytime:" -ForegroundColor White
Write-Host "  notepad $ScriptDir\watcher.log" -ForegroundColor Gray
Write-Host ""
Write-Host "To stop the watcher:" -ForegroundColor White
Write-Host "  Open Task Scheduler -> find '$TaskName' -> Disable or Delete" -ForegroundColor Gray
Write-Host ""

# Trigger it once immediately to verify
Write-Host "Running first check now to verify everything works..." -ForegroundColor Yellow
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
Write-Host "First check started. Watch watcher.log for results." -ForegroundColor Green
Write-Host ""

Read-Host "Press Enter to exit"
