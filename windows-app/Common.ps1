#requires -Version 5.1
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
if (-not ('MiMo.Windows.ManagedProcess' -as [type])) {
    Add-Type -Path (Join-Path $PSScriptRoot 'ManagedProcess.cs')
}

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }

function Refresh-PathFromRegistry {
    $segments = New-Object 'System.Collections.Generic.List[string]'
    foreach ($value in @($env:Path, [Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User'))) {
        foreach ($segment in ($value -split ';')) {
            if ($segment -and -not $segments.Contains($segment)) { $segments.Add($segment) }
        }
    }
    $env:Path = $segments -join ';'
}

function Find-ProjectRoot {
    $root = Split-Path -Parent $PSScriptRoot
    if (-not (Test-Path -LiteralPath (Join-Path $root 'backend\server.py')) -or
        -not (Test-Path -LiteralPath (Join-Path $root 'apps\desktop\package.json'))) {
        throw 'Keep windows-app inside the complete project folder.'
    }
    return $root
}

function Resolve-Tool([string[]]$Names) {
    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    throw "Required tool not found: $($Names[0]). Install it and reopen the terminal."
}

function Test-SensitiveName([string]$Name) { return $Name -match '(?i)(key|token|secret|password|credential)' }

function Get-Redactions([hashtable]$Environment = @{}) {
    $values = @(Get-ChildItem Env: | Where-Object { (Test-SensitiveName $_.Name) -and $_.Value } | ForEach-Object { $_.Value })
    $values += @($Environment.GetEnumerator() | Where-Object { (Test-SensitiveName $_.Key) -and $_.Value } | ForEach-Object { $_.Value })
    return ,([string[]]@($values | Sort-Object Length -Descending))
}

function New-ChildEnvironment([switch]$Backend, [hashtable]$Overrides = @{}) {
    $result = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        if (-not $Backend -and ((Test-SensitiveName $entry.Key) -or $entry.Key -match '^(MIMO_|VITE_)')) { continue }
        $result[$entry.Key] = [string]$entry.Value
    }
    $result['PYTHONIOENCODING'] = 'utf-8'
    $result['PYTHONUTF8'] = '1'
    $result['PYTHONUNBUFFERED'] = '1'
    foreach ($name in $Overrides.Keys) { $result[$name] = [string]$Overrides[$name] }
    return $result
}

function Get-BackendEnvironment([string]$ProjectRoot, [switch]$SkipCredentialFile) {
    $result = New-ChildEnvironment -Backend
    if ($SkipCredentialFile -or -not [string]::IsNullOrWhiteSpace($result['MIMO_API_KEY'])) { return $result }
    foreach ($directory in @($ProjectRoot, (Split-Path -Parent $ProjectRoot))) {
        $files = @(Get-ChildItem -LiteralPath $directory -File -Filter 'mimo*.txt' -ErrorAction Stop)
        if ($files.Count -gt 1) { throw 'Multiple MiMo credential files found in one directory. Keep one or set MIMO_API_KEY explicitly.' }
        if ($files.Count -eq 0) { continue }
        try { $lines = [IO.File]::ReadAllLines($files[0].FullName, [Text.Encoding]::UTF8) }
        catch { throw 'Cannot read the local MiMo credential file. Check its file permissions.' }
        $key = ''; $baseUrl = ''
        foreach ($line in $lines) {
            $value = $line.Trim()
            if (-not $value -or $value.StartsWith('#')) { continue }
            if ($value -match '^https?://') { if (-not $baseUrl) { $baseUrl = $value }; continue }
            if (-not $key) { $key = $value }
        }
        if (-not $key) { throw 'The local MiMo credential file does not contain a key.' }
        $result['MIMO_API_KEY'] = $key
        if (-not $result['MIMO_BASE_URL'] -and $baseUrl) { $result['MIMO_BASE_URL'] = $baseUrl }
        Write-Host 'Loaded local MiMo credentials for the backend only.'
        return $result
    }
    return $result
}

function New-BrowserStartInfo([string]$Url) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = Join-Path $env:SystemRoot 'explorer.exe'
    $info.Arguments = [MiMo.Windows.ManagedProcess]::Quote($Url)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = [Diagnostics.ProcessWindowStyle]::Hidden
    $info.EnvironmentVariables.Clear()
    foreach ($entry in (New-ChildEnvironment).GetEnumerator()) { $info.EnvironmentVariables[$entry.Key] = $entry.Value }
    return $info
}

function Start-ManagedProcess {
    param([string]$FilePath, [string[]]$Arguments = @(), [string]$WorkingDirectory,
        [hashtable]$Environment = (New-ChildEnvironment), [string]$LogPrefix)
    if (-not $LogPrefix) { $LogPrefix = $null }
    return [MiMo.Windows.ManagedProcess]::Start($FilePath, $Arguments, $WorkingDirectory, $Environment, (Get-Redactions -Environment $Environment), $LogPrefix)
}

function Invoke-CheckedCommand {
    param([string]$FilePath, [string[]]$Arguments = @(), [string]$WorkingDirectory = (Get-Location).Path,
        [string]$Label = 'Command', [int]$TimeoutSeconds = 600, [switch]$AllowFailure)
    $child = Start-ManagedProcess -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
        while (-not $child.HasExited) {
            if ([DateTime]::UtcNow -gt $deadline) { throw "$Label timed out." }
            Start-Sleep -Milliseconds 100
        }
        $code = $child.ExitCode
        $child.Finish()
        if ($code -ne 0 -and -not $AllowFailure) { throw "$Label failed (exit $code).`n$($child.ErrorOutput)`n$($child.Output)" }
        return [pscustomobject]@{ ExitCode = $code; Output = $child.Output; ErrorOutput = $child.ErrorOutput }
    } finally { $child.Dispose() }
}

function Resolve-NodeTools {
    $node = Resolve-Tool @('node.exe')
    $npmCommand = Resolve-Tool @('npm.cmd')
    $npmCli = Join-Path (Split-Path -Parent $npmCommand) 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path -LiteralPath $npmCli)) { throw 'npm-cli.js missing beside npm.cmd. Repair the Node.js installation.' }
    $version = (Invoke-CheckedCommand $node @('--version') -Label 'Node.js check' -TimeoutSeconds 20).Output.Trim()
    $npmVersion = (Invoke-CheckedCommand $node @($npmCli, '--version') -Label 'npm check' -TimeoutSeconds 20).Output.Trim()
    Write-Host "Node: $version; npm: $npmVersion"
    return @{ Node = $node; NpmCli = $npmCli }
}

function Resolve-VenvDir([string]$ProjectRoot, [string]$RequestedName = '') {
    if ($RequestedName) {
        if ($RequestedName -in @('.', '..') -or $RequestedName -match '[\\/:*?"<>|]') { throw 'VenvDirName must be a single folder name inside the project.' }
        return Join-Path $ProjectRoot $RequestedName
    }
    $windows = Join-Path $ProjectRoot '.venv-windows'
    if (Test-Path -LiteralPath $windows) { return $windows }
    $legacy = Join-Path $ProjectRoot '.venv'
    if (Test-Path -LiteralPath (Join-Path $legacy 'Scripts\python.exe')) { return $legacy }
    return $windows
}

function Test-Venv([string]$Directory) {
    $python = Join-Path $Directory 'Scripts\python.exe'
    $config = Join-Path $Directory 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $config)) { return $false }
    try {
        $probe = 'import os,sys; norm=lambda p:os.path.normcase(os.path.realpath(p)); assert sys.version_info >= (3,10); assert sys.prefix != sys.base_prefix; assert norm(sys.prefix)==norm(sys.argv[1]); assert norm(sys.executable)==norm(os.path.join(sys.argv[1],"Scripts","python.exe")); assert os.path.isfile(sys._base_executable); import pip'
        $result = Invoke-CheckedCommand $python @('-I', '-c', $probe, $Directory) -Label 'Virtual environment check' -TimeoutSeconds 20 -AllowFailure
        return $result.ExitCode -eq 0
    } catch { return $false }
}

function Assert-FreePort([int]$Port, [string]$Name) {
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Parse('127.0.0.1'), $Port)
    try { $listener.Server.ExclusiveAddressUse = $true; $listener.Start() }
    catch { throw "$Name port $Port is occupied. No existing service will be reused or stopped. Choose another port or close its owner yourself." }
    finally { $listener.Stop() }
}

function Test-ServiceIdentity([string]$Url, [ValidateSet('backend', 'frontend')][string]$Kind, [string]$Origin) {
    try {
        $headers = @{}; if ($Origin) { $headers['Origin'] = $Origin }
        $response = Invoke-WebRequest -Uri $Url -Headers $headers -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
        if ($response.StatusCode -ne 200) { return $false }
        if ($Kind -eq 'backend') {
            $data = $response.Content | ConvertFrom-Json
            if ($data.ok -isnot [bool] -or -not $data.ok -or $data.service -cne 'mimo-workflow-backend') { return $false }
            if ($Origin -and $response.Headers['Access-Control-Allow-Origin'] -cne $Origin) {
                throw 'CORS_CONTRACT: Backend must support --frontend-port and return the requested loopback origin.'
            }
            return $true
        }
        if ($response.Content -notmatch '<title>\s*MiMo Workflow(?:\s[^<]*)?</title>' -or $response.Content -notmatch '/@vite/client') { return $false }
        $vite = Invoke-WebRequest -Uri ($Url.TrimEnd('/') + '/@vite/client') -UseBasicParsing -TimeoutSec 2 -MaximumRedirection 0
        return $vite.StatusCode -eq 200 -and $vite.Content -match 'vite'
    } catch {
        if ($_.Exception.Message -like '*CORS_CONTRACT:*') { throw }
        return $false
    }
}

function Wait-Service([string]$Url, [string]$Kind, $Process, [string]$Origin, [int]$TimeoutSeconds = 60, $WatchProcesses = @()) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        foreach ($peer in $WatchProcesses) {
            if ($peer.HasExited) { throw "A managed service exited during startup (exit $($peer.ExitCode)). See the redacted logs." }
        }
        if ($Process.HasExited) { throw "$Kind exited (exit $($Process.ExitCode)). See the redacted logs." }
        if (Test-ServiceIdentity $Url $Kind $Origin) {
            $owners = @(Get-NetTCPConnection -State Listen -LocalPort ([uri]$Url).Port -ErrorAction SilentlyContinue)
            if (-not ($owners | Where-Object { $Process.OwnsProcess($_.OwningProcess) })) {
                throw "$Kind identity responded, but its listener is not owned by this launch."
            }
            if ($Process.HasExited) { throw "$Kind exited during readiness checks. See the redacted logs." }
            return
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for the expected $Kind service at $Url."
}
