$ErrorActionPreference = 'Stop'

# Always run from this script's directory
Set-Location -Path $PSScriptRoot

function Resolve-VenvExePath {
  param(
    [Parameter(Mandatory=$true)][string]$VenvDir,
    [Parameter(Mandatory=$true)][string]$ExeName
  )

  $candidates = @(
    (Join-Path $VenvDir (Join-Path 'Scripts' $ExeName)),
    (Join-Path $VenvDir (Join-Path 'bin' $ExeName)),
    (Join-Path $VenvDir (Join-Path 'Scripts' ($ExeName + '.exe'))),
    (Join-Path $VenvDir (Join-Path 'bin' ($ExeName + '.exe')))
  )

  foreach ($p in $candidates) {
    if (Test-Path $p) { return $p }
  }

  return $null
}

# Refresh PATH (helps the backend find newly-installed tools like typst)
$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')

# Ensure Typst CLI path is available to the backend process.
# On some Windows setups, `typst` may be callable from a shell but not resolvable by Python's shutil.which.
if (-not $env:TYPST_BIN) {
  $typstPath = $null
  try {
    $cmd = Get-Command typst -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Path) { $typstPath = $cmd.Path }
  } catch { }

  if (-not $typstPath) {
    try {
      $typstPath = (where.exe typst 2>$null | Select-Object -First 1)
    } catch { }
  }

  if ($typstPath) {
    $env:TYPST_BIN = $typstPath
    Write-Host "Using Typst CLI: $env:TYPST_BIN" -ForegroundColor DarkCyan
  } else {
    Write-Host "[WARN] Typst CLI not found. Install typst or set TYPST_BIN to typst.exe full path." -ForegroundColor Yellow
  }
}

$repoRoot = Split-Path -Parent $PSScriptRoot

$venvCandidates = @(
  (Join-Path $PSScriptRoot '.venv'),
  (Join-Path $repoRoot '.venv')
)

$python = $null
$pip = $null
foreach ($venvDir in $venvCandidates) {
  $py = Resolve-VenvExePath -VenvDir $venvDir -ExeName 'python'
  $pp = Resolve-VenvExePath -VenvDir $venvDir -ExeName 'pip'
  if ($py -and $pp) {
    $python = $py
    $pip = $pp
    break
  }
}

if (-not $python) {
  $expected1 = Join-Path $PSScriptRoot '.venv'
  $expected2 = Join-Path $repoRoot '.venv'
  Write-Host "[ERROR] Cannot find venv python in: $expected1 or $expected2" -ForegroundColor Red
  Write-Host "Create it first (recommended under LabFlow_backend):" -ForegroundColor Yellow
  Write-Host "  python -m venv .venv" -ForegroundColor Yellow
  Write-Host "  .\\.venv\\Scripts\\Activate.ps1" -ForegroundColor Yellow
  Write-Host "  pip install -r requirements.txt" -ForegroundColor Yellow
  exit 1
}

# Check and install missing dependencies
$requirementsFile = Join-Path $PSScriptRoot 'requirements.txt'

Write-Host "Checking dependencies..." -ForegroundColor Cyan
$output = & $pip list --format json | ConvertFrom-Json
$installed = @{}
foreach ($pkg in $output) {
  $installed[$pkg.name.ToLower()] = $pkg.version
}

$missingPkgs = @()
foreach ($line in (Get-Content $requirementsFile)) {
  $line = $line.Trim()
  if ([string]::IsNullOrEmpty($line) -or $line.StartsWith('#')) { continue }
  
  # Parse package name (handle >=, ==, <, etc.)
  if ($line -match '^([a-zA-Z0-9\-]+)') {
    $pkgName = $matches[1].ToLower()
    if (-not $installed.ContainsKey($pkgName)) {
      $missingPkgs += $line
      Write-Host "  Missing: $line" -ForegroundColor Yellow
    }
  }
}

if ($missingPkgs.Count -gt 0) {
  Write-Host "Installing missing packages..." -ForegroundColor Cyan
  & $pip install @missingPkgs
  Write-Host "Dependencies installed." -ForegroundColor Green
} else {
  Write-Host "All dependencies are installed." -ForegroundColor Green
}

$hostAddr = if ($env:HOST) { $env:HOST } else { '0.0.0.0' }
$port = if ($env:PORT) { $env:PORT } else { '8000' }

Write-Host "Starting backend on http://$hostAddr`:$port" -ForegroundColor Cyan

& $python -m uvicorn app.main:app --reload --host $hostAddr --port $port --reload-dir app --reload-exclude .venv
