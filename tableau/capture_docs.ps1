# Case-study images: the dashboard (everything) and two filtered views, captured at native size, cropped to the
# 1440 x 900 canvas, retaken when Tableau's editor outlines show. Scratch files are removed.
$root = Split-Path $PSScriptRoot -Parent
$img = Join-Path $root "docs\img"; $tmp = Join-Path $root ".captures"
New-Item -ItemType Directory -Force $img, $tmp | Out-Null
$py = Join-Path $root ".venv\Scripts\python.exe"
function Capture($twbx, $name) {
    foreach ($try in 1..4) {
        & (Join-Path $PSScriptRoot "open_twb.ps1") -Twbx $twbx -Width 0 -Out (Join-Path $tmp "$name-raw.png") | Out-Null
        if ($LASTEXITCODE -eq 1) { continue }
        & (Join-Path $PSScriptRoot "crop_dashboard.ps1") -In (Join-Path $tmp "$name-raw.png") -Out (Join-Path $img "$name.png") | Out-Null
        if ($LASTEXITCODE -ne 2) { "$name ok (try $try)"; return }
    }
    throw "${name}: no clean capture after 4 tries"
}
Capture (Join-Path $root "tableau\Supply Chain Network.twbx") "01-dashboard"
$views = @(@("02-kolkata", @("Warehouse=Kolkata")),
           @("03-diwali-2024", @("Period=FY 2024-25", "Category=Snacks", "As-of week=2024-10-28")))
foreach ($v in $views) {
    $copy = Join-Path $tmp "$($v[0]).twbx"
    & $py (Join-Path $PSScriptRoot "build_twb.py") --out $copy --preset @($v[1]) | Out-Null
    Capture $copy $v[0]
}
Remove-Item -Recurse -Force $tmp
"scratch removed"
