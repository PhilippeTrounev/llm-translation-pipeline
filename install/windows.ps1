$ErrorActionPreference = "Stop"

$PackageSpec = if ($env:PACKAGE_SPEC) { $env:PACKAGE_SPEC } else { "llm-translation-pipeline[all] @ git+https://github.com/PhilippeTrounev/llm-translation-pipeline.git" }

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Error "Python launcher 'py' is required. Install Python 3.10+ from python.org first."
}

py -m pip install --user --upgrade pipx
py -m pipx ensurepath
py -m pipx install "$PackageSpec" --force

Write-Host ""
Write-Host "Installed. Restart PowerShell if llm-translate is not on PATH."
Write-Host "Run: llm-translate setup"
