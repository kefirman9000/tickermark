param([string]$Dir)
$ws  = New-Object -ComObject WScript.Shell
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Stock Logo Downloader.lnk'
$s   = $ws.CreateShortcut($lnk)
$s.TargetPath       = Join-Path $Dir 'Run.bat'
$s.WorkingDirectory = $Dir
$s.WindowStyle      = 7
$s.Description      = 'Stock Logo Downloader'
$s.Save()
