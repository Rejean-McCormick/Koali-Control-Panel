[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$launch = Join-Path $root 'LAUNCH.cmd'
if (-not (Test-Path $launch)) { throw "Missing launcher: $launch" }
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'Koali Control Panel.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $launch
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'Koali Control Panel'
$shortcut.Save()
Write-Host "Created: $link"
