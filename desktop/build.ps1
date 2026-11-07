<#
ValoHub desktop end-to-end build: venv -> decoder bundle -> backend folder (embeddable CPython) -> stage -> electron pack -> licences -> zip.
  powershell -NoProfile -ExecutionPolicy Bypass -File desktop/build.ps1 [-Version <desktop/electron/package.json version>] [-SkipElectron] [-NoZip]
      [-WheelHouse dir] [-DotnetSrc dir] [-DecoderSrc dir] [-Record file] [-TransformsSrc dir]
      [-BundleDir dir] [-AssetsSrc dir] [-EmbedZip file]
No admin, no registry writes, no shortcuts, no signing, no publishing. Network is used only for pip/npm (not with -WheelHouse).
#>
param(
  [string]$Version = (Get-Content (Join-Path $PSScriptRoot "electron/package.json") -Raw | ConvertFrom-Json).version,
  [switch]$SkipElectron,
  [switch]$NoZip,
  [string]$WheelHouse = "",
  [string]$DotnetSrc = "",
  [string]$DecoderSrc = "",
  [string]$Record = "",
  [string]$TransformsSrc = "",
  [string]$BundleDir = "",   # use an already-assembled bundle {runtime/dotnet, decoder} instead of running tools/bundle_decoder.py
  [string]$EmbedZip = "",    # local python-3.12.10-embed-amd64.zip (default: downloaded from python.org, md5-checked)
  [string]$AssetsSrc = ""    # external bot/web/static/replays_app/assets (gitignored art) when the checkout lacks it
)
$ErrorActionPreference = "Stop"
$t0 = Get-Date
function Fail($m) { Write-Host "BUILD FAILED: $m" -ForegroundColor Red; exit 1 }
function Step($m) { Write-Host "==> $m" }
function Run($exe, $argList) {
  & $exe @argList
  if ($LASTEXITCODE -ne 0) { Fail "'$exe $($argList -join ' ')' exited $LASTEXITCODE" }
}

$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$D = Join-Path $Repo "desktop"
$Venv = Join-Path $D ".venv"
$Build = Join-Path $D "build"
$Res = Join-Path $Build "resources"
$Bundle = Join-Path $Build "bundle"
$Sidecar = Join-Path $D "sidecar"
$Elec = Join-Path $D "electron"
$Dist = Join-Path $D "dist"

# ---- prerequisites (fail closed) ----
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { Fail "Python launcher 'py' not found; install Python 3.12 (py -3.12)." }
& py -3.12 --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Python 3.12 not available via 'py -3.12'." }
if (-not (Test-Path (Join-Path $Repo "bot/sidecar.py"))) { Fail "bot/sidecar.py missing (lane L3 not merged)." }
if (-not (Test-Path (Join-Path $Sidecar "requirements-sidecar.txt"))) { Fail "desktop/sidecar/requirements-sidecar.txt missing." }
if ($WheelHouse -and -not (Test-Path $WheelHouse)) { Fail "-WheelHouse '$WheelHouse' does not exist." }
if ($BundleDir) {
  foreach ($p in @("runtime/dotnet/dotnet.exe", "decoder/net10.0/CliReader.dll")) {
    if (-not (Test-Path (Join-Path $BundleDir $p))) { Fail "-BundleDir lacks $p" }
  }
} elseif (-not (Test-Path (Join-Path $Repo "tools/bundle_decoder.py"))) { Fail "tools/bundle_decoder.py missing (lane L2 not merged); pass -BundleDir <assembled bundle>." }
if (-not $SkipElectron) {
  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "npm not found on PATH (needed for Electron pack; use -SkipElectron to skip)." }
  if (-not (Test-Path (Join-Path $Elec "package.json"))) { Fail "desktop/electron/package.json missing (lane E1 not merged); use -SkipElectron." }
}

# ---- (a) venv ----
Step "venv desktop/.venv"
$Py = Join-Path $Venv "Scripts/python.exe"
if (-not (Test-Path $Py)) { Run "py" @("-3.12", "-m", "venv", $Venv) }
$pipArgs = @("-m", "pip", "install", "--disable-pip-version-check", "-r", (Join-Path $Sidecar "requirements-sidecar.txt"))
if ($WheelHouse) { $pipArgs += @("--no-index", "--find-links", $WheelHouse) }
Run $Py $pipArgs

# ---- (b) decoder bundle ----
if ($BundleDir) { $Bundle = (Resolve-Path $BundleDir).Path; Step "using prebuilt bundle $Bundle" }
else {
  Step "decoder bundle"
  $b = @((Join-Path $Repo "tools/bundle_decoder.py"), "--out", $Bundle, "--force")
  if ($DotnetSrc) { $b += @("--dotnet-src", $DotnetSrc) }
  if ($DecoderSrc) { $b += @("--decoder-src", $DecoderSrc) }
  if ($Record) { $b += @("--record", $Record) }
  if ($TransformsSrc) { $b += @("--transforms-src", $TransformsSrc) }
  Run "py" (@("-3.12") + $b)
}

# ---- (c) backend folder: official embeddable CPython + site-packages + bot package (no freezer) ----
Step "assemble backend (embeddable CPython 3.12)"
$tf = Get-Date
$VrbDir = Join-Path $Build "backend-asm"
$mb = @((Join-Path $Sidecar "make_backend.py"), "--repo", $Repo, "--out", $VrbDir, "--venv-python", $Py, "--requirements", (Join-Path $Sidecar "requirements-sidecar.txt"))
if ($EmbedZip) { $mb += @("--embed-zip", (Resolve-Path $EmbedZip).Path) }
if ($AssetsSrc) { $mb += @("--assets-src", (Resolve-Path $AssetsSrc).Path) }
if ($WheelHouse) { $env:PIP_NO_INDEX = "1"; $env:PIP_FIND_LINKS = (Resolve-Path $WheelHouse).Path }
Run $Py $mb
$freezeSec = [math]::Round(((Get-Date) - $tf).TotalSeconds, 1)
if (-not (Test-Path (Join-Path $VrbDir "python.exe"))) { Fail "backend assembly produced no python.exe" }

# ---- (d) stage ----
Step "stage resources"
if (Test-Path $Res) { Remove-Item -LiteralPath $Res -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Res | Out-Null
Copy-Item -LiteralPath $VrbDir -Destination (Join-Path $Res "backend") -Recurse
Copy-Item -LiteralPath (Join-Path $Bundle "decoder") -Destination (Join-Path $Res "decoder") -Recurse
Copy-Item -LiteralPath (Join-Path $Bundle "runtime") -Destination (Join-Path $Res "runtime") -Recurse

# ---- licences ----
Step "licences"
$Lic = Join-Path $Build "LICENSES"
if (Test-Path $Lic) { Remove-Item -LiteralPath $Lic -Recurse -Force }
$g = @((Join-Path $D "LICENSES/gen_licenses.py"), "--repo", $Repo, "--out", $Lic, "--venv-python", (Join-Path $VrbDir "python.exe"))
$dn = if ($DotnetSrc) { $DotnetSrc } elseif (Test-Path (Join-Path $Repo "dotnet10-sdk")) { Join-Path $Repo "dotnet10-sdk" } else { "" }
if ($dn) { $g += @("--dotnet-src", $dn) }
Run "py" (@("-3.12") + $g)

# ---- (e) electron ----
$Unpacked = Join-Path $Elec "dist/win-unpacked"
$electronVer = ""; $builderVer = ""
if (-not $SkipElectron) {
  Step "electron npm ci + pack"
  Push-Location $Elec
  try {
    Run "npm" @("ci")
    Run "npm" @("run", "pack", "--", "--config.extraMetadata.version=$Version")
  } finally { Pop-Location }
  if (-not (Test-Path (Join-Path $Unpacked "ValoHub.exe"))) { Fail "electron pack produced no win-unpacked/ValoHub.exe" }
  $pj = Get-Content (Join-Path $Elec "package.json") -Raw | ConvertFrom-Json
  $electronVer = $pj.devDependencies.electron; $builderVer = $pj.devDependencies."electron-builder"
  # ---- (f) licences + BUILD-INFO into package ----
  Copy-Item -LiteralPath $Lic -Destination (Join-Path $Unpacked "LICENSES") -Recurse -Force
}

# ---- BUILD-INFO.json ----
$commit = (& git -C $Repo rev-parse HEAD 2>$null)
$dirty = [bool](& git -C $Repo status --porcelain 2>$null)
if ($dirty) { Write-Warning "working tree is dirty (modified or untracked files): BUILD-INFO.json will record git_dirty=true and the artifact will not be reproducible from a commit" }
function Sha($p) { if (Test-Path $p) { (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLower() } else { $null } }
$ver = { param($m) (& $Py -c "import importlib.metadata as m;print(m.version('$m'))" 2>$null) }
$exeElectron = Join-Path $Unpacked "ValoHub.exe"
$info = [ordered]@{
  product = "ValoHub"; version = $Version
  git_commit = $commit; git_dirty = $dirty
  python = (& $Py --version 2>&1) -join ""
  aiohttp = (& $ver "aiohttp")
  electron = $electronVer; electron_builder = $builderVer
  built_utc = (Get-Date).ToUniversalTime().ToString("o")
  freeze_seconds = $freezeSec
  sha256 = [ordered]@{ "ValoHub.exe" = (Sha $exeElectron); "backend/python.exe" = (Sha (Join-Path $Res "backend/python.exe")) }
}
$infoJson = $info | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText((Join-Path $Build "BUILD-INFO.json"), $infoJson)
if (-not $SkipElectron) { [IO.File]::WriteAllText((Join-Path $Unpacked "BUILD-INFO.json"), $infoJson) }

# ---- (g) zip ----
if (-not $SkipElectron -and -not $NoZip) {
  Step "zip"
  New-Item -ItemType Directory -Force -Path $Dist | Out-Null
  $zip = Join-Path $Dist "ValoHub-$Version-win64.zip"
  if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
  Compress-Archive -Path (Join-Path $Unpacked "*") -DestinationPath $zip -CompressionLevel Optimal
  $h = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
  [IO.File]::WriteAllText((Join-Path $Dist "SHA256SUMS.txt"), "$h  ValoHub-$Version-win64.zip`n")
  Write-Host "zip: $zip  sha256=$h"
}

# ---- (h) smoke ----
if (-not $SkipElectron -and (Test-Path $exeElectron)) {
  Step "smoke: ValoHub.exe --check"
  & $exeElectron --home (Join-Path $Build "check-home") --check
  if ($LASTEXITCODE -ne 0) { Fail "ValoHub.exe --check exited $LASTEXITCODE" }
}
Write-Host ("BUILD OK in {0:N1}s  (sidecar: {1})" -f ((Get-Date) - $t0).TotalSeconds, $VrbDir)
