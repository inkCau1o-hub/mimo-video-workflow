#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$BackendPort = 8765,
    [ValidateRange(1, 65535)][int]$FrontendPort = 5173,
    [switch]$NoBrowser,
    [switch]$SmokeOnly,
    [switch]$FrontendOnly,
    [switch]$SkipCredentialFile,
    [string]$VenvDirName = ''
)

. (Join-Path $PSScriptRoot 'Common.ps1')
$children = New-Object 'System.Collections.Generic.List[MiMo.Windows.ManagedProcess]'
$backendEnvironment = @{}
$resultCode = 0
try {
    if ($BackendPort -eq $FrontendPort) { throw 'BackendPort and FrontendPort must differ.' }
    Refresh-PathFromRegistry
    $root = Find-ProjectRoot
    $frontend = Join-Path $root 'apps\desktop'
    $node = Resolve-NodeTools
    $vite = Join-Path $frontend 'node_modules\vite\bin\vite.js'
    if (-not (Test-Path -LiteralPath $vite)) { throw 'Local Vite is missing. Run the Windows installer first.' }
    Assert-FreePort $FrontendPort 'Frontend'
    if (-not $FrontendOnly) {
        Assert-FreePort $BackendPort 'Backend'
        $venv = Resolve-VenvDir $root $VenvDirName
        if (-not (Test-Venv $venv)) { throw 'The Windows virtual environment is missing or unusable after moving. Run the Windows installer to repair it.' }
        $python = Join-Path $venv 'Scripts\python.exe'
    }
    $runId = '{0}-{1}-{2}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $PID, ([Guid]::NewGuid().ToString('N').Substring(0, 6))
    $logs = Join-Path $PSScriptRoot "logs\$runId"
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $backendUrl = "http://127.0.0.1:$BackendPort"
    $frontendUrl = "http://127.0.0.1:$FrontendPort"
    Write-Host "Project: $root"
    Write-Host "Redacted logs: $logs"
    if (-not $FrontendOnly) {
        Write-Step 'Starting backend'
        $backendEnvironment = Get-BackendEnvironment $root -SkipCredentialFile:$SkipCredentialFile
        $backendProcess = Start-ManagedProcess -FilePath $python -Arguments @('-m', 'backend.server', '--host', '127.0.0.1', '--port', "$BackendPort", '--frontend-port', "$FrontendPort", '--quiet') -WorkingDirectory $root -Environment $backendEnvironment -LogPrefix (Join-Path $logs 'backend')
        $children.Add($backendProcess)
        Wait-Service "$backendUrl/health" 'backend' $backendProcess $frontendUrl
    }
    Write-Step 'Starting frontend'
    $frontendProcess = Start-ManagedProcess -FilePath $node.Node -Arguments @($vite, '--host', '127.0.0.1', '--port', "$FrontendPort", '--strictPort') -WorkingDirectory $frontend -Environment (New-ChildEnvironment -Overrides @{ VITE_BACKEND_URL = $backendUrl; BROWSER = 'none' }) -LogPrefix (Join-Path $logs 'frontend')
    $children.Add($frontendProcess)
    Wait-Service "$frontendUrl/" 'frontend' $frontendProcess -WatchProcesses $children
    foreach ($child in $children) {
        if ($child.HasExited) { throw 'A managed service exited before startup completed. See the redacted logs.' }
    }
    $url = "$frontendUrl/?auto_open=latest&start_page=voiceover"
    Write-Host "URL: $url"
    if (-not $NoBrowser -and -not $SmokeOnly) {
        $browser = [Diagnostics.Process]::Start((New-BrowserStartInfo $url))
        if ($browser) { $browser.Dispose() }
    }
    if ($SmokeOnly) { Write-Step 'Smoke check passed' }
    else {
        Write-Step 'Running. Press Ctrl+C or close this window to stop owned services.'
        while ($true) {
            foreach ($child in $children) {
                if ($child.HasExited) { throw "A child process exited (exit $($child.ExitCode)). See the redacted logs." }
            }
            Start-Sleep -Milliseconds 500
        }
    }
} catch {
    $redactions = Get-Redactions
    if ($backendEnvironment) { $redactions = Get-Redactions -Environment $backendEnvironment }
    Write-Host ([MiMo.Windows.ManagedProcess]::Redact($_.Exception.Message, $redactions)) -ForegroundColor Red
    $resultCode = 1
} finally {
    for ($i = $children.Count - 1; $i -ge 0; $i--) {
        try { $children[$i].Dispose() } catch { Write-Warning 'Child cleanup failed.'; $resultCode = 1 }
    }
}
exit $resultCode
