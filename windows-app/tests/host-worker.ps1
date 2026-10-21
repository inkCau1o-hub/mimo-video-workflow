param([string]$Common, [string]$Node, [string]$Fixture)
. $Common
$child = Start-ManagedProcess -FilePath $Node -Arguments @($Fixture, 'tree') -WorkingDirectory $PSScriptRoot
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while (-not $child.Output.Trim()) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Fixture startup timeout.' }
        Start-Sleep -Milliseconds 100
    }
    Write-Output $child.Output.Trim()
    while ($true) { Start-Sleep -Milliseconds 200 }
} finally { $child.Dispose() }
