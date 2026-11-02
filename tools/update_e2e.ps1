# The in-place upgrade test for the Windows installer, for CI only (a throw-away Windows machine: it installs, upgrades and
# uninstalls SkillDesk in the current user's profile). Never run it on a PC whose SkillDesk you use.
#
#   tools\update_e2e.ps1 -Old dist\SkillDesk-Setup-v0.0.1.exe -OldVersion 0.0.1 -New dist\SkillDesk-Setup-v1.4.0.exe -NewVersion 1.4.0
#
# 1. installs the OLD version, then starts it hidden (the tray start-up) with SKILLDESK_UPDATE_TEST_ORIGIN pointing at a
#    local web server that plays GitHub (the release answer, SHA256SUMS.txt and the NEW installer);
# 2. expects the app to download and verify the NEW installer by itself, quit, have the installer upgrade the same folder,
#    and the NEW version to start again, hidden;
# 3. then installs the OLD one again and serves a wrong hash: the app must not run the download, and keep running.
param(
    [Parameter(Mandatory)][string]$Old,
    [Parameter(Mandatory)][string]$OldVersion,
    [Parameter(Mandatory)][string]$New,
    [Parameter(Mandatory)][string]$NewVersion
)
$ErrorActionPreference = "Stop"
$Old = (Resolve-Path $Old).Path
$New = (Resolve-Path $New).Path
$repo = "sgustjd2/skill-manage-viewer"
$app = Join-Path $env:LOCALAPPDATA "Programs\SkillDesk"
$exe = Join-Path $app "SkillDesk.exe"
$store = Join-Path $HOME ".skill-manager"
$port = 18765
$newName = "SkillDesk-Setup-v$NewVersion.exe"

function Show-Logs {
    foreach ($n in "error.log", "update-setup.log", "update-check.log") {
        $f = Join-Path $store $n
        if (Test-Path $f) { Write-Host "---- $n"; Get-Content $f -Tail 60 }
    }
}
function Fail($m) { Show-Logs; throw $m }
function Installed-Version { (Get-Content (Join-Path $app "_internal\VERSION")).Trim() }
function Install($setup) { Start-Process $setup -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait }
function Running { @(Get-Process SkillDesk -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }) }
function Stop-App { Running | Stop-Process -Force; Start-Sleep -Seconds 2 }
function Wait-Until([scriptblock]$cond, [int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) { if (& $cond) { return $true }; Start-Sleep -Seconds 1 }
    return $false
}

# ---- the fake GitHub ---------------------------------------------------------------------------------------
$srv = Join-Path $env:TEMP "fake-github"
$dl = Join-Path $srv "$repo\releases\download\v$NewVersion"
$api = Join-Path $srv "repos\$repo\releases"
New-Item -ItemType Directory -Force $dl, $api | Out-Null
Copy-Item $New (Join-Path $dl $newName)
$answer = @{ tag_name = "v$NewVersion"; draft = $false; prerelease = $false; assets = @(@{ name = $newName }, @{ name = "SHA256SUMS.txt" }) }
[IO.File]::WriteAllText((Join-Path $api "latest"), ($answer | ConvertTo-Json -Depth 4))
function Serve-Sums($hash) { [IO.File]::WriteAllText((Join-Path $dl "SHA256SUMS.txt"), "$hash  $newName`n") }
Serve-Sums (Get-FileHash $New -Algorithm SHA256).Hash.ToLower()
$web = Start-Process python -ArgumentList "-m", "http.server", "$port", "--bind", "127.0.0.1", "--directory", $srv -PassThru -WindowStyle Hidden
$env:SKILLDESK_UPDATE_TEST_ORIGIN = "http://127.0.0.1:$port"
if (-not (Wait-Until { try { (Invoke-WebRequest "http://127.0.0.1:$port/repos/$repo/releases/latest" -UseBasicParsing).StatusCode -eq 200 } catch { $false } } 20)) { Fail "the fake release server did not start" }

try {
    # ---- 1. the good update ---------------------------------------------------------------------------------
    Install $Old
    if ((Installed-Version) -ne $OldVersion) { Fail "the old build says $(Installed-Version), not $OldVersion" }
    Remove-Item (Join-Path $store "update-setup.log"), (Join-Path $store "update-attempt.json") -ErrorAction SilentlyContinue
    $first = Start-Process $exe -ArgumentList "--hidden" -PassThru
    if (-not (Wait-Until { (Installed-Version) -eq $NewVersion } 180)) { Fail "the app did not upgrade itself to $NewVersion within 3 minutes (still $(Installed-Version))" }
    if (-not (Wait-Until { $first.HasExited } 30)) { Fail "the old app is still running after the upgrade" }
    if (-not (Wait-Until { (Running | Where-Object { $_.Id -ne $first.Id }).Count -ge 1 } 60)) { Fail "the new version was not started again after the upgrade" }
    $second = Running | Where-Object { $_.Id -ne $first.Id } | Select-Object -First 1
    Start-Sleep -Seconds 8
    if ($second.HasExited) { Fail "the new version started and then quit within 8 seconds" }
    $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($second.Id)").CommandLine
    if ($cmdline -notmatch "--hidden") { Fail "the new version was not started hidden: $cmdline" }
    $check = Start-Process $exe -ArgumentList "--check" -Wait -PassThru
    if ($check.ExitCode -ne 0) { Fail "the upgraded SkillDesk.exe --check exited with $($check.ExitCode)" }
    if (-not (Test-Path (Join-Path $store "update-setup.log"))) { Fail "no installer log: the upgrade did not go through update-setup" }
    Write-Host "OK: $OldVersion upgraded itself to $NewVersion and started again hidden"
    Stop-App

    # ---- 2. a download that does not match the list is never run ------------------------------------------------
    Install $Old
    if ((Installed-Version) -ne $OldVersion) { Fail "could not put the old build back" }
    Remove-Item (Join-Path $store "update-setup.log") -ErrorAction SilentlyContinue
    Serve-Sums ("0" * 64)
    $third = Start-Process $exe -ArgumentList "--hidden" -PassThru
    Start-Sleep -Seconds 60  # the check starts 15 seconds after the app does
    if ($third.HasExited) { Fail "the app quit although the download did not match its hash" }
    if ((Installed-Version) -ne $OldVersion) { Fail "a download that did not match its hash was installed" }
    if (Test-Path (Join-Path $store "update-setup.log")) { Fail "the installer was started for a download that did not match its hash" }
    if (Get-Process -Name "SkillDesk-Setup*" -ErrorAction SilentlyContinue) { Fail "an installer is running for a download that did not match its hash" }
    if (Get-ChildItem $env:TEMP -Directory -Filter "skilldesk-update-*" -ErrorAction SilentlyContinue) { Fail "the rejected download was not deleted" }
    Write-Host "OK: a download with a wrong hash was dropped and the old app kept running"
}
finally {
    Stop-App
    $unins = Join-Path $app "unins000.exe"
    if (Test-Path $unins) { Start-Process $unins -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait }
    Stop-Process -Id $web.Id -Force -ErrorAction SilentlyContinue
}
