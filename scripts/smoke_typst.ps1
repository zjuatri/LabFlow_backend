param(
  [string]$Token = "",
  [string]$BaseUrl = "http://127.0.0.1:8000"
)

$headers = @{}
if ($Token -ne "") { $headers["Authorization"] = "Bearer $Token" }

$body = @{ code = "#set page(width: 200pt, height: 120pt)\nHello Typst" } | ConvertTo-Json

Write-Host "== SVG preview ==" -ForegroundColor Cyan
$svg = Invoke-WebRequest -Method POST -Uri "$BaseUrl/api/render-typst" -Headers ($headers + @{"Content-Type"="application/json"}) -Body $body
$svg.Content | Select-Object -First 400

Write-Host "== PDF download ==" -ForegroundColor Cyan
$pdf = Invoke-WebRequest -Method POST -Uri "$BaseUrl/api/render-typst/pdf" -Headers ($headers + @{"Content-Type"="application/json"}) -Body $body -OutFile "$PSScriptRoot\out.pdf"
Write-Host "Saved: $PSScriptRoot\out.pdf"
