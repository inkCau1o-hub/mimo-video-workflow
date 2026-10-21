#requires -Version 5.1
[CmdletBinding()]
param([switch]$Integration)

$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'Common.ps1')
$root = Find-ProjectRoot
$windows = Split-Path -Parent $PSScriptRoot
$node = Resolve-Tool @('node.exe')
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$fixture = Join-Path $PSScriptRoot 'fixture.mjs'
$script:Passed = 0

function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Test-Case([string]$Name, [scriptblock]$Body) {
    & $Body
    $script:Passed++
    Write-Host "PASS $Name"
}
function Assert-Throws([scriptblock]$Body, [string]$Pattern) {
    $message = ''
    try { & $Body | Out-Null } catch { $message = $_.Exception.Message }
    Assert ($message -like $Pattern) "Expected failure: $Pattern"
}
function Get-FreePort {
    $socket = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    try { $socket.Start(); return $socket.LocalEndpoint.Port } finally { $socket.Stop() }
}
function Wait-Output($Child, [string]$Pattern = '*', [int]$Seconds = 10) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while (-not $Child.Output.Trim() -or $Child.Output -notlike $Pattern) {
        if ($Child.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw 'Fixture did not become ready.' }
        Start-Sleep -Milliseconds 100
    }
}
function Assert-Stopped([int[]]$Ids) {
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    do {
        $alive = @($Ids | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($alive.Count -eq 0) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'An owned process survived cleanup.'
}
function Invoke-Launcher([string[]]$Options) {
    Invoke-CheckedCommand $powershell (@('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $windows '启动开发版.ps1'), '-SkipCredentialFile') + $Options) -WorkingDirectory $env:TEMP -Label 'Launcher' -TimeoutSeconds 90 -AllowFailure
}

Test-Case 'PowerShell parser and Windows encodings' {
    foreach ($file in Get-ChildItem -LiteralPath $windows -Filter '*.ps1' -Recurse) {
        $errors = $null; $tokens = $null
        $null = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
        Assert ($errors.Count -eq 0) "Parse failed: $($file.Name)"
        $bytes = [IO.File]::ReadAllBytes($file.FullName)
        Assert ($bytes[0] -eq 239 -and $bytes[1] -eq 187 -and $bytes[2] -eq 191) "Missing UTF-8 BOM: $($file.Name)"
        Assert (-not ([IO.File]::ReadAllText($file.FullName) -match '(?<!\r)\n')) "Expected CRLF: $($file.Name)"
    }
}
Test-Case 'Nonzero native exit code is fatal' {
    Assert-Throws { Invoke-CheckedCommand $node @($fixture, 'exit', '23') -Label 'Deliberate failure' } '*exit 23*'
}
Test-Case 'Windows argument quoting round-trips' {
    $arguments = @('', 'space value', '中文路径', 'a"b', 'C:\folder with space\', 'a&b', '(one)', 'bang!', 'percent%value')
    $result = Invoke-CheckedCommand $node (@($fixture, 'args') + $arguments)
    $actual = $result.Output | ConvertFrom-Json
    Assert ($actual.Count -eq $arguments.Count) 'Argument count changed.'
    for ($i = 0; $i -lt $actual.Count; $i++) { Assert ($actual[$i] -ceq $arguments[$i]) "Argument $i changed." }
}
Test-Case 'Command timeout terminates descendants' {
    Assert-Throws { Invoke-CheckedCommand $node @($fixture, 'tree') -TimeoutSeconds 1 } '*timed out*'
}
Test-Case 'Existing Windows venv validates and POSIX copy is untouched' {
    Assert (Test-Venv (Resolve-VenvDir $root)) 'The installed Windows environment is not valid.'
    Assert (-not (Test-Venv (Join-Path $root '.venv'))) 'POSIX copy was considered usable.'
    Assert-Throws { Resolve-VenvDir $root '..\outside' } '*single folder*'
}
Test-Case 'Broken or moved venv is rejected by executable probe' {
    $sandbox = Join-Path $PSScriptRoot ('.runtime-' + [Guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Path (Join-Path $sandbox 'Scripts') | Out-Null
        Copy-Item -LiteralPath $node -Destination (Join-Path $sandbox 'Scripts\python.exe')
        Copy-Item -LiteralPath (Join-Path (Resolve-VenvDir $root) 'pyvenv.cfg') -Destination $sandbox
        Assert (-not (Test-Venv $sandbox)) 'Invalid executable passed as a virtual environment.'
    } finally {
        $resolved = [IO.Path]::GetFullPath($sandbox)
        Assert ($resolved.StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase)) 'Unsafe test cleanup path.'
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
Test-Case 'Backend-only secrets and redaction before disk writes' {
    $names = @('MIMO_API_KEY', 'MIMO_TEST_TOKEN', 'VITE_TEST_API_KEY')
    $saved = @{}; foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
    $prefix = Join-Path $PSScriptRoot ('.redaction-' + [Guid]::NewGuid().ToString('N'))
    try {
        $env:MIMO_API_KEY = 'synthetic-key-111'
        $env:MIMO_TEST_TOKEN = 'synthetic-key-222'
        $env:VITE_TEST_API_KEY = 'synthetic-key-333'
        $front = New-ChildEnvironment -Overrides @{ VITE_BACKEND_URL = 'http://127.0.0.1:9999' }
        foreach ($name in $names) { Assert (-not $front.ContainsKey($name)) 'Frontend inherited a credential.' }
        $browserInfo = New-BrowserStartInfo 'http://127.0.0.1:5173/'
        foreach ($name in $names) { Assert (-not $browserInfo.EnvironmentVariables.ContainsKey($name)) 'Browser helper inherited a credential.' }
        $child = Start-ManagedProcess $node @($fixture, 'env') $PSScriptRoot -Environment $front
        try {
            while (-not $child.HasExited) { Start-Sleep -Milliseconds 50 }
            $child.Finish()
            Assert ($child.Output -match '"hasKey":false' -and $child.Output -match '"hasOther":false') 'Frontend secret isolation failed.'
        } finally { $child.Dispose() }
        $child = Start-ManagedProcess $node @($fixture, 'env') $PSScriptRoot -Environment (New-ChildEnvironment -Backend) -LogPrefix $prefix
        try {
            while (-not $child.HasExited) { Start-Sleep -Milliseconds 50 }
            $child.Finish()
            Assert ($child.Output -match '"hasKey":true') 'Backend did not receive its credential.'
            $logs = (Get-Content -LiteralPath "$prefix.out.log", "$prefix.err.log" -Raw) -join ''
            Assert ($logs -notmatch 'synthetic-key-' -and $logs -match '\[REDACTED\]') 'Credential leaked to a log.'
        } finally { $child.Dispose() }
        Assert ($env:MIMO_API_KEY -ceq 'synthetic-key-111') 'Parent environment was changed.'
        Assert ([MiMo.Windows.ManagedProcess]::Redact('{"api_key":"fixture-only"}', @()) -notmatch 'fixture-only') 'Quoted credential fields were not redacted.'
    } finally {
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
        Remove-Item -LiteralPath "$prefix.out.log", "$prefix.err.log" -Force -ErrorAction SilentlyContinue
    }
}
Test-Case 'Wrong HTTP service is rejected and left alive' {
    $port = Get-FreePort
    $foreign = Start-ManagedProcess $node @($fixture, 'server', "$port") $PSScriptRoot
    try {
        Wait-Output $foreign '*READY*'
        Assert (-not (Test-ServiceIdentity "http://127.0.0.1:$port/health" 'backend')) 'Accepted wrong backend.'
        Assert (-not (Test-ServiceIdentity "http://127.0.0.1:$port/" 'frontend')) 'Accepted generic HTTP as frontend.'
        Assert-Throws { Assert-FreePort $port 'Test' } '*occupied*'
        $result = Invoke-Launcher @('-BackendPort', "$port", '-FrontendPort', "$(Get-FreePort)", '-SmokeOnly')
        Assert ($result.ExitCode -ne 0 -and $result.Output -match 'occupied') 'Launcher reused a foreign service.'
        Assert (-not $foreign.HasExited) 'Launcher terminated a foreign service.'
    } finally { $foreign.Dispose() }
}
Test-Case 'Local credential file stays backend-only, redacted, and environment takes precedence' {
    $sandbox = Join-Path $PSScriptRoot ('.runtime-credentials-' + [Guid]::NewGuid().ToString('N'))
    $project = Join-Path $sandbox 'project'
    $savedKey = $env:MIMO_API_KEY; $savedBase = $env:MIMO_BASE_URL
    try {
        New-Item -ItemType Directory -Path $project -Force | Out-Null
        $env:MIMO_API_KEY = $null; $env:MIMO_BASE_URL = $null
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'credential-fixture.txt') -Destination (Join-Path $sandbox 'mimo.fixture.txt')
        $backend = Get-BackendEnvironment $project
        Assert ($backend['MIMO_API_KEY'] -ceq 'synthetic-file-key-444') 'Local credential was not loaded.'
        Assert ($backend['MIMO_BASE_URL'] -ceq 'https://example.invalid/v1') 'Local base URL was not loaded.'
        Assert (-not $env:MIMO_API_KEY) 'File credential leaked to the parent environment.'
        Assert (-not (New-ChildEnvironment).ContainsKey('MIMO_API_KEY')) 'File credential leaked to frontend environment.'
        $prefix = Join-Path $sandbox 'backend'
        $child = Start-ManagedProcess $node @($fixture, 'env') $PSScriptRoot -Environment $backend -LogPrefix $prefix
        try {
            while (-not $child.HasExited) { Start-Sleep -Milliseconds 50 }
            $child.Finish()
            Assert ($child.Output -match '"hasKey":true') 'Backend did not receive file credential.'
            $logs = (Get-Content -LiteralPath "$prefix.out.log", "$prefix.err.log" -Raw) -join ''
            Assert ($logs -notmatch 'synthetic-file-key-444' -and $logs -match '\[REDACTED\]') 'File credential leaked to logs.'
        } finally { $child.Dispose() }
        Assert (-not (Get-BackendEnvironment $project -SkipCredentialFile).ContainsKey('MIMO_API_KEY')) 'SkipCredentialFile still read the file.'
        $env:MIMO_API_KEY = 'synthetic-env-key-555'
        $env:MIMO_BASE_URL = 'https://env.example.invalid/v1'
        $backend = Get-BackendEnvironment $project
        Assert ($backend['MIMO_API_KEY'] -ceq 'synthetic-env-key-555' -and $backend['MIMO_BASE_URL'] -ceq 'https://env.example.invalid/v1') 'Environment precedence changed.'
        $env:MIMO_API_KEY = $null
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'credential-fixture.txt') -Destination (Join-Path $sandbox 'mimo.second.txt')
        Assert-Throws { Get-BackendEnvironment $project } '*Multiple MiMo credential files*'
    } finally {
        $env:MIMO_API_KEY = $savedKey; $env:MIMO_BASE_URL = $savedBase
        $resolved = [IO.Path]::GetFullPath($sandbox)
        Assert ($resolved.StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase)) 'Unsafe test cleanup path.'
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
Test-Case 'Health identity and CORS must both match' {
    $port = Get-FreePort
    $child = Start-ManagedProcess $node @($fixture, 'server', "$port", 'valid') $PSScriptRoot
    try {
        Wait-Output $child '*READY*'
        Assert (Test-ServiceIdentity "http://127.0.0.1:$port/health" 'backend' 'http://127.0.0.1:5173') 'Expected identity not recognized.'
        Assert-Throws { Test-ServiceIdentity "http://127.0.0.1:$port/health" 'backend' 'http://127.0.0.1:5174' } '*CORS_CONTRACT*'
    } finally { $child.Dispose() }
}
Test-Case 'Frontend identity tolerates presentation-only title changes' {
    $port = Get-FreePort
    $child = Start-ManagedProcess $node @($fixture, 'server', "$port", 'frontend') $PSScriptRoot
    try {
        Wait-Output $child '*READY*'
        Assert (Test-ServiceIdentity "http://127.0.0.1:$port/" 'frontend') 'A changed MiMo title suffix failed service identity.'
    } finally { $child.Dispose() }
}
Test-Case 'Listener must belong to the launched process' {
    $port = Get-FreePort
    $foreign = Start-ManagedProcess $node @($fixture, 'server', "$port", 'valid') $PSScriptRoot
    $other = Start-ManagedProcess $node @($fixture, 'tree') $PSScriptRoot
    try {
        Wait-Output $foreign '*READY*'
        Assert-Throws { Wait-Service "http://127.0.0.1:$port/health" 'backend' $other 'http://127.0.0.1:5173' 3 } '*not owned*'
    } finally { $other.Dispose(); $foreign.Dispose() }
}
Test-Case 'Early child exit fails readiness immediately' {
    $child = Start-ManagedProcess $node @($fixture, 'exit', '19') $PSScriptRoot
    try { Assert-Throws { Wait-Service "http://127.0.0.1:$(Get-FreePort)/health" 'backend' $child '' 5 } '*exited*' }
    finally { $child.Dispose() }
}
Test-Case 'Peer exit during startup cannot produce a successful smoke result' {
    $peer = Start-ManagedProcess $node @($fixture, 'exit', '19') $PSScriptRoot
    $other = Start-ManagedProcess $node @($fixture, 'tree') $PSScriptRoot
    try {
        while (-not $peer.HasExited) { Start-Sleep -Milliseconds 50 }
        Assert-Throws { Wait-Service "http://127.0.0.1:$(Get-FreePort)/" 'frontend' $other -TimeoutSeconds 5 -WatchProcesses @($peer) } '*managed service exited during startup*'
    } finally { $other.Dispose(); $peer.Dispose() }
}
Test-Case 'Vite strictPort refuses conflicts without choosing the next port' {
    $port = Get-FreePort
    $foreign = Start-ManagedProcess $node @($fixture, 'server', "$port") $PSScriptRoot
    try {
        Wait-Output $foreign '*READY*'
        $vite = Join-Path $root 'apps\desktop\node_modules\vite\bin\vite.js'
        $result = Invoke-CheckedCommand $node @($vite, '--host', '127.0.0.1', '--port', "$port", '--strictPort') -WorkingDirectory (Join-Path $root 'apps\desktop') -TimeoutSeconds 10 -AllowFailure
        Assert ($result.ExitCode -ne 0) 'Vite silently chose a different port.'
        Assert (-not $foreign.HasExited) 'Vite stopped the occupied service.'
    } finally { $foreign.Dispose() }
}
Test-Case 'Disposal stops the complete owned process tree' {
    $child = Start-ManagedProcess $node @($fixture, 'tree') $PSScriptRoot
    try { Wait-Output $child; $ids = $child.Output | ConvertFrom-Json }
    finally { $child.Dispose() }
    Assert-Stopped @($ids.pid, $ids.child)
}
Test-Case 'Abrupt supervisor exit also closes its child job' {
    $hostProcess = Start-ManagedProcess $powershell @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $PSScriptRoot 'host-worker.ps1'), '-Common', (Join-Path $windows 'Common.ps1'), '-Node', $node, '-Fixture', $fixture) $PSScriptRoot
    try {
        Wait-Output $hostProcess
        $ids = $hostProcess.Output | ConvertFrom-Json
        Stop-Process -Id $hostProcess.Id -Force
        Assert-Stopped @($ids.pid, $ids.child)
    } finally { $hostProcess.Dispose() }
}
Test-Case 'Equal and out-of-range ports return failure' {
    Assert ((Invoke-Launcher @('-BackendPort', '5173', '-FrontendPort', '5173', '-SmokeOnly')).ExitCode -ne 0) 'Accepted equal ports.'
    Assert ((Invoke-Launcher @('-FrontendPort', '65536', '-SmokeOnly')).ExitCode -ne 0) 'Accepted invalid port.'
}
Test-Case 'Batch wrappers preserve arguments and exit codes after relocation' {
    $sandbox = Join-Path $PSScriptRoot ('.runtime-中文 space & (!)-' + [Guid]::NewGuid().ToString('N'))
    $project = Join-Path $sandbox '程序文件'
    $target = Join-Path $project 'windows-app'
    $oldPause = $env:MIMO_NO_PAUSE
    try {
        New-Item -ItemType Directory -Path $target -Force | Out-Null
        $env:MIMO_NO_PAUSE = '1'
        $stub = Join-Path $PSScriptRoot 'wrapper-target.ps1'
        Copy-Item -LiteralPath $stub -Destination (Join-Path $target '安装环境.ps1')
        Copy-Item -LiteralPath $stub -Destination (Join-Path $target '启动开发版.ps1')
        foreach ($name in @('安装环境.bat', '启动开发版App.bat')) {
            Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $root) $name) -Destination $sandbox
            & (Join-Path $sandbox $name) -Probe 'quoted value & (!)'
            Assert ($LASTEXITCODE -eq 37) "$name lost its argument or exit code."
        }
        Copy-Item -LiteralPath (Join-Path $root 'start-frontend.bat') -Destination $project
        & (Join-Path $project 'start-frontend.bat') -Probe 'quoted value & (!)'
        Assert ($LASTEXITCODE -eq 37) 'Frontend wrapper lost its argument or exit code.'
        Remove-Item -LiteralPath (Join-Path $target '安装环境.ps1'), (Join-Path $target '启动开发版.ps1')
        foreach ($name in @('安装环境.bat', '启动开发版App.bat')) {
            & (Join-Path $sandbox $name)
            Assert ($LASTEXITCODE -eq 1) 'Missing-script wrapper did not fail.'
        }
    } finally {
        $env:MIMO_NO_PAUSE = $oldPause
        $resolved = [IO.Path]::GetFullPath($sandbox)
        Assert ($resolved.StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase)) 'Unsafe test cleanup path.'
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
Test-Case 'Installer repairs broken venv, preserves backup, and propagates npm failure' {
    $sandbox = Join-Path $PSScriptRoot ('.runtime-install 中文 & (!)-' + [Guid]::NewGuid().ToString('N'))
    try {
        foreach ($dir in @('windows-app', 'backend', 'apps\desktop', '.venv-windows\Scripts')) {
            New-Item -ItemType Directory -Path (Join-Path $sandbox $dir) -Force | Out-Null
        }
        foreach ($name in @('安装环境.ps1', 'Common.ps1', 'ManagedProcess.cs')) {
            Copy-Item -LiteralPath (Join-Path $windows $name) -Destination (Join-Path $sandbox 'windows-app')
        }
        Copy-Item -LiteralPath (Join-Path $root 'backend\server.py') -Destination (Join-Path $sandbox 'backend')
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'installer-package.json') -Destination (Join-Path $sandbox 'apps\desktop\package.json')
        Copy-Item -LiteralPath $node -Destination (Join-Path $sandbox '.venv-windows\Scripts\python.exe')
        Copy-Item -LiteralPath (Join-Path (Resolve-VenvDir $root) 'pyvenv.cfg') -Destination (Join-Path $sandbox '.venv-windows')
        $options = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $sandbox 'windows-app\安装环境.ps1'), '-SkipPythonDeps', '-SkipNpmInstall')
        $result = Invoke-CheckedCommand $powershell $options -WorkingDirectory $env:TEMP -TimeoutSeconds 90 -AllowFailure
        Assert ($result.ExitCode -eq 0) "Repair failed: $($result.Output)"
        Assert (Test-Venv (Join-Path $sandbox '.venv-windows')) 'Repaired venv is unusable.'
        Assert (@(Get-ChildItem -LiteralPath $sandbox -Directory -Filter '.venv-windows.invalid-*' -Force).Count -eq 1) 'Old environment was not preserved.'
        $result = Invoke-CheckedCommand $powershell ($options + @('-BuildFrontend')) -WorkingDirectory $env:TEMP -TimeoutSeconds 90 -AllowFailure
        Assert ($result.ExitCode -ne 0 -and $result.Output -match 'Frontend build failed \(exit 29\)') 'Installer hid the npm build failure.'
        $result = Invoke-CheckedCommand $powershell ($options + @('-RunSmoke')) -WorkingDirectory $env:TEMP -TimeoutSeconds 90 -AllowFailure
        Assert ($result.ExitCode -ne 0 -and $result.Output -match 'smoke test is missing') 'Installer reported success for a missing requested smoke test.'
    } finally {
        $resolved = [IO.Path]::GetFullPath($sandbox)
        Assert ($resolved.StartsWith($PSScriptRoot + '\', [StringComparison]::OrdinalIgnoreCase)) 'Unsafe test cleanup path.'
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
if ($Integration) {
    Test-Case 'Real backend + Vite custom-port smoke and complete cleanup' {
        $backendPort = Get-FreePort; $frontendPort = Get-FreePort
        while ($frontendPort -eq $backendPort) { $frontendPort = Get-FreePort }
        $result = Invoke-Launcher @('-BackendPort', "$backendPort", '-FrontendPort', "$frontendPort", '-NoBrowser', '-SmokeOnly')
        Assert ($result.ExitCode -eq 0 -and $result.Output -match 'Smoke check passed') "Real launcher smoke failed: $($result.Output) $($result.ErrorOutput)"
        Assert-FreePort $backendPort 'Backend'; Assert-FreePort $frontendPort 'Frontend'
    }
    Test-Case 'Frontend-only smoke works without starting a backend' {
        $port = Get-FreePort
        $result = Invoke-Launcher @('-FrontendOnly', '-FrontendPort', "$port", '-NoBrowser', '-SmokeOnly')
        Assert ($result.ExitCode -eq 0) "Frontend-only smoke failed: $($result.Output)"
        Assert-FreePort $port 'Frontend'
    }
}
Write-Host "All $script:Passed reliability tests passed."
