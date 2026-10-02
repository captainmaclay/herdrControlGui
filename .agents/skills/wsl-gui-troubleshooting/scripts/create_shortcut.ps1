<#
.SYNOPSIS
    Создает бесшумный ярлык на рабочем столе Windows для запуска Linux GUI приложения из WSL2.
.PARAMETER ShortcutName
    Имя ярлыка (например, "Claude Desktop (WSL)")
.PARAMETER Distro
    Имя дистрибутива WSL (например, "Ubuntu")
.PARAMETER WslCommand
    Команда внутри WSL (например, "/usr/local/bin/claude-gui")
.PARAMETER IconPath
    Путь к .ico файлу на Windows (опционально)
.PARAMETER WorkingDirectory
    Рабочая директория (например, "C:\MyFiles\herdrCenter")
#>

param(
    [Parameter(Mandatory = $true)]
    [string]$ShortcutName,

    [Parameter(Mandatory = $false)]
    [string]$Distro = "Ubuntu",

    [Parameter(Mandatory = $true)]
    [string]$WslCommand,

    [Parameter(Mandatory = $false)]
    [string]$IconPath = "",

    [Parameter(Mandatory = $false)]
    [string]$WorkingDirectory = "C:\MyFiles\herdrCenter"
)

$desktop = [System.IO.Path]::Combine($env:USERPROFILE, "Desktop")
if (-not (Test-Path $WorkingDirectory)) {
    New-Item -ItemType Directory -Path $WorkingDirectory -Force | Out-Null
}

# 1. Создаем универсальный VBS скрипт запуска (WindowStyle = 0, скрытое окно)
$safeCmdName = ($ShortcutName -replace '[^a-zA-Z0-9_]', '_').ToLower()
$vbsPath = Join-Path $WorkingDirectory "run_${safeCmdName}.vbs"

$vbsContent = @"
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "wsl.exe -d $Distro bash -lc ""$WslCommand""", 0, False
"@

[System.IO.File]::WriteAllText($vbsPath, $vbsContent, [System.Text.Encoding]::ASCII)
Write-Host "VBS launcher created: $vbsPath"

# 2. Создаем ярлык .lnk на рабочем столе
$wsh = New-Object -ComObject WScript.Shell
$lnkPath = Join-Path $desktop "$ShortcutName.lnk"
$shortcut = $wsh.CreateShortcut($lnkPath)
$shortcut.TargetPath = "C:\Windows\system32\wscript.exe"
$shortcut.Arguments = "`"$vbsPath`""
$shortcut.WorkingDirectory = $WorkingDirectory
$shortcut.Description = "$ShortcutName (WSL2 GUI via WSLg)"

if ($IconPath -and (Test-Path $IconPath)) {
    $shortcut.IconLocation = $IconPath
}

$shortcut.Save()
Write-Host "Desktop shortcut created: $lnkPath"
