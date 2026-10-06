# Crop a Tableau window capture to the dashboard canvas: the largest block of the dashboard's paper colour.
param([Parameter(Mandatory)][string]$In, [Parameter(Mandatory)][string]$Out, [string]$Paper = "#fbfaf7")
Add-Type -AssemblyName System.Drawing
$bmp = [System.Drawing.Bitmap]::FromFile($In)
$p = [System.Drawing.ColorTranslator]::FromHtml($Paper)
function IsPaper($c) { [Math]::Abs($c.R - $p.R) -le 2 -and [Math]::Abs($c.G - $p.G) -le 2 -and [Math]::Abs($c.B - $p.B) -le 2 }
# Paper runs along a row and a column through the canvas centre give the canvas edges
$cy = [int]($bmp.Height * 0.12); $cx = [int]($bmp.Width * 0.5)
$xs = 0..($bmp.Width - 1) | Where-Object { IsPaper $bmp.GetPixel($_, $cy) }
$ys = 0..($bmp.Height - 1) | Where-Object { IsPaper $bmp.GetPixel($cx, $_) }
$left = ($xs | Measure-Object -Minimum).Minimum; $right = ($xs | Measure-Object -Maximum).Maximum
$top = ($ys | Measure-Object -Minimum).Minimum; $bottom = ($ys | Measure-Object -Maximum).Maximum
$rect = New-Object System.Drawing.Rectangle $left, $top, ($right - $left + 1), ($bottom - $top + 1)
$crop = $bmp.Clone($rect, $bmp.PixelFormat); $bmp.Dispose()
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$crop.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
"cropped $($rect.Width) x $($rect.Height) at ($left, $top) -> $Out"
# Tableau sometimes draws its editor zone outlines (dashed boxes) into the capture. They sit at the zone edges,
# inside the dashboard's 24 px gutter, which is pure paper in a clean capture (content starts at x 24). Any
# other pixel in the gutter band on either side means outlines: report it with exit code 2.
$marks = 0
$band = @(2..22) + @(($crop.Width - 23)..($crop.Width - 3))
foreach ($y in 0..($crop.Height - 1)) { foreach ($x in $band) { if (-not (IsPaper $crop.GetPixel($x, $y))) { $marks++ } } }
$crop.Dispose()
if ($marks -gt 0) { "editor outlines in capture ($marks edge pixels)"; exit 2 }
exit 0
