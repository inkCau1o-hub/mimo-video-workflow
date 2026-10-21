param([string]$Probe)
if ($Probe -ceq 'quoted value & (!)' -or $args -contains 'quoted value & (!)') { exit 37 }
exit 9
