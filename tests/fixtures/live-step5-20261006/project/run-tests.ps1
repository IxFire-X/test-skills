$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
$jdk = Join-Path $env:USERPROFILE '.jdks\openjdk-24.0.1'

if (-not (Test-Path (Join-Path $jdk 'bin\java.exe'))) {
    throw "JDK 24 not found: $jdk. Set JAVA_HOME to a JDK 17+ installation."
}

Set-Location $projectRoot
$env:JAVA_HOME = $jdk
$env:Path = "$(Join-Path $jdk 'bin');$env:Path"

Write-Host "Using JAVA_HOME=$env:JAVA_HOME"
& (Join-Path $projectRoot 'mvnw.cmd') -q test
exit $LASTEXITCODE