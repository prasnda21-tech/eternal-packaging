#Requires -Version 5.1
# Eternal Android preview build. Run only from C:\Projects\eternal-packaging-mobile-build
# Does not log in to EAS and does not read or store signing credentials.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\mobile-build-lib.ps1"
Invoke-EternalMobileEasPreviewBuild -Platform android
