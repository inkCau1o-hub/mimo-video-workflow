#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$BuildFrontend,
    [switch]$RunSmoke,
    [switch]$SkipPythonDeps,
    [switch]$SkipNpmInstall,
    [string]$VenvDirName = ''
)

. (Join-Path $PSScriptRoot 'Common.ps1')
try {
    Refresh-PathFromRegistry
    $root = Find-ProjectRoot
    $frontend = Join-Path $root 'apps\desktop'
    Write-Host "Project: $root"
    Write-Step 'Checking installed tools (no system installation)'
    $node = Resolve-NodeTools
    foreach ($tool in @('ffmpeg.exe', 'ffprobe.exe')) {
        $path = Resolve-Tool @($tool)
        $check = Invoke-CheckedCommand $path @('-version') -Label "$tool check" -TimeoutSeconds 20
        Write-Host (($check.Output -split '\r?\n')[0])
    }
    $venv = Resolve-VenvDir $root $VenvDirName
    if (-not (Test-Venv $venv)) {
        Write-Step 'Creating or repairing the Windows virtual environment'
        $launcher = $null
        foreach ($candidate in @(@{ Name = 'python.exe'; Prefix = @() }, @{ Name = 'py.exe'; Prefix = @('-3') })) {
            try {
                $candidate.File = Resolve-Tool @($candidate.Name)
                $null = Invoke-CheckedCommand $candidate.File ($candidate.Prefix + @('-I', '-c', 'import sys; assert sys.version_info >= (3,10)')) -Label 'Python 3.10+ check' -TimeoutSeconds 20
                $launcher = $candidate; break
            } catch { continue }
        }
        if (-not $launcher) { throw 'No working Python 3.10+ found. Install Python and reopen the terminal.' }
        if (Test-Path -LiteralPath $venv) {
            $resolvedVenv = (Resolve-Path -LiteralPath $venv).Path
            if ((Split-Path -Parent $resolvedVenv) -ne $root -or ((Get-Item -LiteralPath $venv -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'Refusing to move a virtual environment outside the project or through a junction.'
            }
            if (Test-Path -LiteralPath (Join-Path $venv 'bin\python')) { throw 'Refusing to alter a macOS/Linux virtual environment. Choose a different VenvDirName.' }
            # Preserve the old environment; never delete user files during repair.
            $backup = "$venv.invalid-$(Get-Date -Format 'yyyyMMddHHmmss')-$([Guid]::NewGuid().ToString('N').Substring(0, 6))"
            Move-Item -LiteralPath $venv -Destination $backup
            Write-Host "Old virtual environment preserved: $backup"
        }
        $null = Invoke-CheckedCommand $launcher.File ($launcher.Prefix + @('-m', 'venv', $venv)) -WorkingDirectory $root -Label 'Virtual environment creation'
        if (-not (Test-Venv $venv)) { throw 'The new virtual environment failed validation.' }
    } else { Write-Host 'Existing Windows virtual environment passed validation.' }
    $python = Join-Path $venv 'Scripts\python.exe'
    if (-not $SkipPythonDeps) {
        Write-Step 'Checking Python dependencies'
        $probe = Join-Path $PSScriptRoot 'check_requirements.py'
        $check = Invoke-CheckedCommand $python @($probe, (Join-Path $root 'requirements.txt')) -Label 'Python dependency check' -AllowFailure
        if ($check.ExitCode -ne 0) {
            $null = Invoke-CheckedCommand $python @('-m', 'pip', 'install', '-r', (Join-Path $root 'requirements.txt')) -WorkingDirectory $root -Label 'pip install' -TimeoutSeconds 1800
        } else { Write-Host 'Installed Python requirements are already satisfied.' }
        $null = Invoke-CheckedCommand $python @($probe, (Join-Path $root 'requirements.txt')) -Label 'Python requirements verification'
        $null = Invoke-CheckedCommand $python @('-m', 'pip', 'check') -Label 'pip dependency consistency'
    } else { Write-Host 'Python dependency checks/install skipped by request.' }
    if (-not $SkipNpmInstall) {
        Write-Step 'Checking frontend dependencies and native modules'
        $probe = Join-Path $PSScriptRoot 'check_frontend.mjs'
        $tree = Invoke-CheckedCommand $node.Node @($node.NpmCli, 'ls', '--depth=0') -WorkingDirectory $frontend -Label 'npm dependency versions' -AllowFailure
        $check = Invoke-CheckedCommand $node.Node @($probe, $frontend) -Label 'Frontend dependency check' -AllowFailure
        if ($tree.ExitCode -ne 0 -or $check.ExitCode -ne 0) {
            $verb = 'install'
            if (Test-Path -LiteralPath (Join-Path $frontend 'package-lock.json')) { $verb = 'ci' }
            $null = Invoke-CheckedCommand $node.Node @($node.NpmCli, $verb, '--include=optional') -WorkingDirectory $frontend -Label "npm $verb" -TimeoutSeconds 1800
        } else { Write-Host 'Installed frontend dependencies are already usable.' }
        $null = Invoke-CheckedCommand $node.Node @($node.NpmCli, 'ls', '--depth=0') -WorkingDirectory $frontend -Label 'npm dependency verification'
        $null = Invoke-CheckedCommand $node.Node @($probe, $frontend) -Label 'Frontend dependency verification'
    } else { Write-Host 'Frontend dependency checks/install skipped by request.' }
    Write-Step 'AI animation module (optional)'
    $animationDir = $env:MIMO_AI_ANIMATION_DIR
    if (-not $animationDir) {
        foreach ($candidate in @((Join-Path $root 'modules\ai-animation'), (Join-Path (Split-Path -Parent $root) 'ai-animation'))) {
            if (Test-Path -LiteralPath (Join-Path $candidate 'launcher.json')) { $animationDir = $candidate; break }
        }
    }
    if ($animationDir -and (Test-Path -LiteralPath (Join-Path $animationDir 'package.json'))) {
        Write-Host "AI animation: $animationDir"
        try {
            $pnpmTool = Resolve-Tool @('pnpm.cmd', 'corepack.cmd')
            $pnpmArgs = @('install', '--frozen-lockfile')
            if ((Split-Path -Leaf $pnpmTool) -like 'corepack*') { $pnpmArgs = @('pnpm') + $pnpmArgs }
            $result = Invoke-CheckedCommand $pnpmTool $pnpmArgs -WorkingDirectory $animationDir -Label 'AI animation install' -TimeoutSeconds 1800 -AllowFailure
            if ($result.ExitCode -ne 0) { Write-Warning 'AI animation dependencies failed to install; its home card will show as not installed. The feed workflow is unaffected.' }
        } catch {
            Write-Warning "AI animation install skipped: $($_.Exception.Message)"
        }
    } else { Write-Host 'AI animation folder not found (set MIMO_AI_ANIMATION_DIR); skipped.' }
    if ($BuildFrontend) {
        Write-Step 'Building frontend'
        $null = Invoke-CheckedCommand $node.Node @($node.NpmCli, 'run', 'build') -WorkingDirectory $frontend -Label 'Frontend build'
    }
    if ($RunSmoke) {
        Write-Step 'Running project smoke tests'
        $null = Invoke-CheckedCommand $python @('-m', 'compileall', 'backend', 'workflow_state.py', 'production_schema.py', 'tests') -WorkingDirectory $root -Label 'Python compilation'
        foreach ($test in @('frontend_prototype_smoke.py', 'backend_api_smoke.py')) {
            $testPath = Join-Path $root "tests\$test"
            if (-not (Test-Path -LiteralPath $testPath)) { throw "Requested smoke test is missing: $test" }
            $null = Invoke-CheckedCommand $python @($testPath) -WorkingDirectory $root -Label $test
        }
    }
    Write-Step 'Requested setup and checks completed'
    exit 0
} catch {
    Write-Host ([MiMo.Windows.ManagedProcess]::Redact($_.Exception.Message, (Get-Redactions))) -ForegroundColor Red
    exit 1
}
