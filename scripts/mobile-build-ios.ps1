#Requires -Version 5.1
# Eternal iOS preview build. Run only from C:\Projects\eternal-packaging-mobile-build
# Does not request, store, or commit Apple credentials. Credentials stay on EAS remote.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\mobile-build-lib.ps1"
Invoke-EternalMobileEasPreviewBuild -Platform ios
