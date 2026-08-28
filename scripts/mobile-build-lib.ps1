# Shared Eternal mobile EAS preview-build helpers.
# Not invoked directly. Used by mobile-build-android.ps1 and mobile-build-ios.ps1.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:ExpectedWorktree = 'C:\Projects\eternal-packaging-mobile-build'
$script:ForbiddenMainRepo = 'C:\Projects\eternal-packaging'
$script:ProductionApiUrl = 'https://produksi.eternalpackaging.id'
$script:RequiredExpoSdkMajor = '54'
$script:ExpectedEasSlug = 'prasetya17'
$script:ExpectedEasOwner = 'eternal-packaging'
$script:ExpectedEasProjectId = '22d68768-64c1-4f3e-ab6a-a217157fabb5'

function Write-Fail {
    param([Parameter(Mandatory = $true)][string]$Message)
    Write-Host ""
    Write-Host "ERROR: $Message" -ForegroundColor Red
    Write-Host "STOPPED." -ForegroundColor Red
    exit 1
}

function Get-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return ([System.IO.Path]::GetFullPath($Path.TrimEnd('\', '/'))).ToLowerInvariant()
}

function Resolve-GitReportedPath {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)][string]$RawPath
    )
    if ([System.IO.Path]::IsPathRooted($RawPath)) {
        return Get-NormalizedPath $RawPath
    }
    return Get-NormalizedPath (Join-Path $RepoRoot $RawPath)
}

function Get-RepoRoot {
    $root = (git rev-parse --show-toplevel 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($root)) {
        Write-Fail "Not a git repository.`n$root"
    }
    return $root
}

function Assert-DedicatedMobileBuildWorktree {
    $repoRoot = Get-RepoRoot
    $normalizedRoot = Get-NormalizedPath $repoRoot
    $normalizedExpected = Get-NormalizedPath $script:ExpectedWorktree
    $normalizedMain = Get-NormalizedPath $script:ForbiddenMainRepo

    if ($normalizedRoot -eq $normalizedMain) {
        Write-Fail @"
Do not build from the mixed WIP repo.
Dedicated build worktree: $($script:ExpectedWorktree)
Current repo: $repoRoot
"@
    }

    if ($normalizedRoot -ne $normalizedExpected) {
        Write-Fail @"
This script only runs in the dedicated mobile build worktree.
Expected: $($script:ExpectedWorktree)
Current:  $repoRoot
Create it once with:
  git fetch origin
  git worktree add $($script:ExpectedWorktree) origin/main
"@
    }

    $gitDirRaw = (git rev-parse --git-dir 2>&1 | Out-String).Trim()
    $gitCommonRaw = (git rev-parse --git-common-dir 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "Unable to resolve git directories."
    }

    $gitDirFull = Resolve-GitReportedPath -RepoRoot $repoRoot -RawPath $gitDirRaw
    $gitCommonFull = Resolve-GitReportedPath -RepoRoot $repoRoot -RawPath $gitCommonRaw
    if ($gitDirFull -eq $gitCommonFull) {
        Write-Fail @"
This checkout looks like a primary working tree, not a dedicated git worktree.
Use: $($script:ExpectedWorktree)
"@
    }

    return $repoRoot
}

function Assert-CleanOriginMain {
    Write-Host "Fetching origin..."
    git fetch origin
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "git fetch origin failed."
    }

    $dirty = git status --porcelain
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "git status failed."
    }
    if (-not [string]::IsNullOrWhiteSpace($dirty)) {
        Write-Fail "Working tree is dirty. Dedicated worktree must stay clean.`n$dirty"
    }

    $originMain = (git rev-parse origin/main 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($originMain)) {
        Write-Fail "Unable to resolve origin/main."
    }

    $head = (git rev-parse HEAD 2>&1 | Out-String).Trim()
    if ($head -ne $originMain) {
        Write-Host "Refreshing dedicated worktree to origin/main..."
        git checkout --detach origin/main
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "Failed to check out origin/main. Source is not origin/main latest."
        }
        $head = (git rev-parse HEAD 2>&1 | Out-String).Trim()
        $dirtyAfter = git status --porcelain
        if ($head -ne $originMain -or -not [string]::IsNullOrWhiteSpace($dirtyAfter)) {
            Write-Fail "Source is not origin/main latest.`nHEAD=$head`norigin/main=$originMain"
        }
    }

    Write-Host "Source OK: origin/main @$originMain"
}

function Assert-PreviewApiUrl {
    param([Parameter(Mandatory = $true)][string]$MobileDir)

    $easPath = Join-Path $MobileDir 'eas.json'
    if (-not (Test-Path $easPath)) {
        Write-Fail "Missing $easPath"
    }

    $eas = Get-Content -Raw -Path $easPath | ConvertFrom-Json
    $previewUrl = ''
    $productionUrl = ''
    try { $previewUrl = [string]$eas.build.preview.env.EXPO_PUBLIC_API_URL } catch { $previewUrl = '' }
    try { $productionUrl = [string]$eas.build.production.env.EXPO_PUBLIC_API_URL } catch { $productionUrl = '' }

    if ([string]::IsNullOrWhiteSpace($previewUrl)) {
        Write-Fail "Preview EXPO_PUBLIC_API_URL is empty."
    }
    if ($previewUrl.TrimEnd('/') -ne $script:ProductionApiUrl) {
        Write-Fail "Preview EXPO_PUBLIC_API_URL must be $($script:ProductionApiUrl). Got: $previewUrl"
    }
    if ([string]::IsNullOrWhiteSpace($productionUrl)) {
        Write-Fail "Production EXPO_PUBLIC_API_URL is empty."
    }
    if ($productionUrl.TrimEnd('/') -ne $script:ProductionApiUrl) {
        Write-Fail "Production EXPO_PUBLIC_API_URL must be $($script:ProductionApiUrl). Got: $productionUrl"
    }

    Write-Host "API URL OK: $previewUrl"
}

function Assert-ExpoSdk54 {
    param([Parameter(Mandatory = $true)][string]$MobileDir)

    $packagePath = Join-Path $MobileDir 'package.json'
    $pkg = Get-Content -Raw -Path $packagePath | ConvertFrom-Json
    $expo = [string]$pkg.dependencies.expo
    if ([string]::IsNullOrWhiteSpace($expo) -or ($expo -notmatch $script:RequiredExpoSdkMajor)) {
        Write-Fail "Expo SDK must be $($script:RequiredExpoSdkMajor). package.json expo=$expo"
    }
    Write-Host "Expo SDK OK: $expo"
}

function Assert-EasProjectLink {
    param([Parameter(Mandatory = $true)][string]$MobileDir)

    $appPath = Join-Path $MobileDir 'app.json'
    if (-not (Test-Path $appPath)) {
        Write-Fail "Missing $appPath"
    }

    $app = Get-Content -Raw -Path $appPath | ConvertFrom-Json
    $slug = [string]$app.expo.slug
    $owner = [string]$app.expo.owner
    $projectId = ''
    try { $projectId = [string]$app.expo.extra.eas.projectId } catch { $projectId = '' }

    if ($slug -eq 'eternal-packaging-mobile') {
        Write-Fail "EAS slug eternal-packaging-mobile does not match the existing remote project. Use $($script:ExpectedEasSlug)."
    }
    if ($slug -ne $script:ExpectedEasSlug) {
        Write-Fail "EAS slug must be $($script:ExpectedEasSlug). Got: $slug"
    }
    if ($owner -ne $script:ExpectedEasOwner) {
        Write-Fail "EAS owner must be $($script:ExpectedEasOwner). Got: $owner"
    }
    if ($projectId -ne $script:ExpectedEasProjectId) {
        Write-Fail "EAS projectId must be $($script:ExpectedEasProjectId). Got: $projectId"
    }

    Write-Host "EAS project OK: @$owner/$slug ($projectId)"
}

function Install-MobileDependencies {
    param([Parameter(Mandatory = $true)][string]$MobileDir)

    Write-Host "Installing dependencies with npm ci..."
    Push-Location $MobileDir
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "npm ci failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Invoke-ExpoDoctor {
    param([Parameter(Mandatory = $true)][string]$MobileDir)

    Write-Host "Running expo-doctor..."
    Push-Location $MobileDir
    try {
        npx --yes expo-doctor
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "expo-doctor failed."
        }
    }
    finally {
        Pop-Location
    }
}

function Assert-EasLoggedIn {
    param([Parameter(Mandatory = $true)][string]$MobileDir)

    Write-Host "Checking EAS login..."
    Push-Location $MobileDir
    try {
        $whoami = npx --yes eas-cli@latest whoami 2>&1 | Out-String
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($whoami) -or ($whoami -match 'Not logged in|Log in with')) {
            Write-Host $whoami
            Write-Fail @"
EAS is not logged in. This script will not log in for you.

From the dedicated worktree, run once:
  cd mobile
  npx eas-cli@latest login

Then rerun the build script.
"@
        }
        Write-Host "EAS account: $($whoami.Trim())"
    }
    finally {
        Pop-Location
    }
}

function Invoke-EternalMobileEasPreviewBuild {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('android', 'ios')]
        [string]$Platform
    )

    Write-Host "Eternal mobile EAS preview build ($Platform)"
    Write-Host "Dedicated worktree: $($script:ExpectedWorktree)"

    $repoRoot = Assert-DedicatedMobileBuildWorktree
    Set-Location $repoRoot
    Assert-CleanOriginMain

    $mobileDir = Join-Path $repoRoot 'mobile'
    if (-not (Test-Path (Join-Path $mobileDir 'package.json'))) {
        Write-Fail "mobile/package.json not found."
    }
    if (-not (Test-Path (Join-Path $mobileDir 'eas.json'))) {
        Write-Fail "mobile/eas.json not found."
    }

    Assert-ExpoSdk54 -MobileDir $mobileDir
    Assert-EasProjectLink -MobileDir $mobileDir
    Assert-PreviewApiUrl -MobileDir $mobileDir
    Install-MobileDependencies -MobileDir $mobileDir
    Invoke-ExpoDoctor -MobileDir $mobileDir
    Assert-EasLoggedIn -MobileDir $mobileDir

    Write-Host "Starting EAS build: $Platform / preview"
    Push-Location $mobileDir
    try {
        npx --yes eas-cli@latest build -p $Platform --profile preview
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "EAS build command failed."
        }
    }
    finally {
        Pop-Location
    }
}
