param([switch]$RebuildNative)
$ErrorActionPreference = 'Stop'
$repoDir = Split-Path -Parent $PSScriptRoot
Push-Location $repoDir
try {
    $pythonExe = Join-Path $repoDir '.venv/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        throw 'Create .venv and install server/requirements.txt plus pyinstaller==6.22.3 first (see README).'
    }
    if ($RebuildNative) {
        & $pythonExe tools/build_native.py
        if ($LASTEXITCODE -ne 0) { throw 'Native kernel build failed.' }
    }
    & $pythonExe -c "import sys; sys.path.insert(0, 'server'); import fast; fast.require(fast.MIN_VERSION, 'local Windows build'); print('earthfast', fast._ef.VERSION)"
    if ($LASTEXITCODE -ne 0) { throw 'Native kernels are missing or outdated. Re-run with -RebuildNative (requires Rust and MSVC).'; }
    # The bundled upstream release kernels are reused unless explicitly rebuilt.
    & $pythonExe tools/build_app.py --stock-bootloader
    if ($LASTEXITCODE -ne 0) { throw 'Windows packaging failed.' }
    $version = (Get-Content -LiteralPath VERSION -Raw).Trim()
    $exe = Join-Path $repoDir "dist/BeamNG.Earth-$version.exe"
    & $exe --server --help
    if ($LASTEXITCODE -ne 0) { throw 'Packaged server smoke test failed.' }
    Write-Host "Ready: $exe"
} finally {
    Pop-Location
}
