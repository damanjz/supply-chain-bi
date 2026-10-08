# Open a workbook in a NEW Tableau Public window, capture it, read its log, then close only that window.
# Never touches Tableau windows that were already open.
# -Width 0 keeps the native resolution (used for the case-study images).
param([Parameter(Mandatory)][string]$Twbx, [string]$Out, [int]$Settle = 12, [int]$Width = 1600)
$exe = Get-ChildItem "C:\Program Files\Tableau" -Recurse -Filter tabpublic.exe | Sort-Object FullName -Descending | Select-Object -First 1
$before = @(Get-Process tabpublic -ErrorAction SilentlyContinue | ForEach-Object Id)
$tempDir = "$env:TEMP\TableauTemp"
$tempBefore = @(Get-ChildItem $tempDir -Force -ErrorAction SilentlyContinue | ForEach-Object Name)
$proc = Start-Process $exe.FullName -ArgumentList "`"$Twbx`"" -PassThru
$name = [IO.Path]::GetFileNameWithoutExtension($Twbx)
$deadline = (Get-Date).AddMinutes(3)
do { Start-Sleep -Seconds 2; $proc.Refresh() } until (($proc.MainWindowTitle -like "*$name*") -or $proc.HasExited -or (Get-Date) -gt $deadline)
if ($proc.HasExited) { "process exited early"; exit 1 }
if ($proc.MainWindowTitle -notlike "*$name*") {
    # never leave a failed window (or its error dialog) behind
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    "workbook did not open (title: $($proc.MainWindowTitle)); closed our window"; exit 1
}
"window: $($proc.MainWindowTitle)"
Start-Sleep -Seconds $Settle

# Capture only this window
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class TOpen { [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f); }
"@
$r = New-Object TOpen+RECT
$until = (Get-Date).AddSeconds(60)
do {   # wait until the window is drawn at full size (a capture can otherwise catch a tiny splash frame)
    $proc.Refresh(); $h = $proc.MainWindowHandle
    [TOpen]::GetWindowRect($h, [ref]$r) | Out-Null
    if (($r.R - $r.L) -lt 800) { Start-Sleep -Seconds 3 }
} until ((($r.R - $r.L) -ge 800) -or (Get-Date) -gt $until)
if (($r.R - $r.L) -lt 800) {
    $proc.CloseMainWindow() | Out-Null
    if (-not $proc.WaitForExit(15000)) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    "window never reached full size; closed our window"; exit 1
}
$bmp = New-Object System.Drawing.Bitmap ($r.R - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc(); [TOpen]::PrintWindow($h, $dc, 2) | Out-Null; $g.ReleaseHdc($dc)
if (-not $Out) { $Out = Join-Path (Split-Path $PSScriptRoot -Parent) ".captures\$name.png" }
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$w = if ($Width -gt 0) { $Width } else { $bmp.Width }; $s = New-Object System.Drawing.Bitmap $w, ([int]($bmp.Height * $w / $bmp.Width))
$g2 = [System.Drawing.Graphics]::FromImage($s); $g2.InterpolationMode = 'HighQualityBicubic'; $g2.DrawImage($bmp, 0, 0, $s.Width, $s.Height); $s.Save($Out)
"captured $Out"

# This window's log: Tableau writes one log file per running instance, tagged with the process id
$logs = Get-ChildItem (Join-Path ([Environment]::GetFolderPath("MyDocuments")) "My Tableau Repository\Logs") -Filter "log*.txt"
$mine = $logs | Where-Object { Select-String -Path $_.FullName -Pattern "`"pid`":$($proc.Id)," -SimpleMatch -Quiet } | Select-Object -First 1
if ($mine) {
    $cats = Select-String -Path $mine.FullName -Pattern '"query-category":"([^"]+)"' -AllMatches | ForEach-Object { $_.Matches } | ForEach-Object { $_.Groups[1].Value } | Group-Object | ForEach-Object { "$($_.Name)=$($_.Count)" }
    "log: $($mine.Name) | queries: $($cats -join ', ')"
    Select-String -Path $mine.FullName -Pattern 'errorcode=[a-z0-9]+' -AllMatches | ForEach-Object { $_.Matches.Value } | Select-Object -Unique
}

# Close only the window this script opened
$proc.CloseMainWindow() | Out-Null
if (-not $proc.WaitForExit(15000)) { "window did not close by itself (left open, pid $($proc.Id))"; exit 0 }
"closed pid $($proc.Id)"

# Tableau leaves temp files behind even on a clean close. Remove what appeared during this run, but only when
# no other Tableau window was open at any point: then every new item is provably ours.
if ($before.Count -eq 0 -and -not (Get-Process tabpublic -ErrorAction SilentlyContinue)) {
    $new = @(Get-ChildItem $tempDir -Force -ErrorAction SilentlyContinue | Where-Object { $tempBefore -notcontains $_.Name })
    $new | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }
    "removed $($new.Count) temp item(s) from this run"
} else { "another Tableau window was open; left TableauTemp alone" }
