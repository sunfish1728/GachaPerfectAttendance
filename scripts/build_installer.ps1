# 編譯安裝程式：. .\scripts\build_installer.ps1 → runtime\installer\GachaPerfectAttendance-Setup-<版本>.exe
# Inno Setup 6 編譯器放在 .local\innosetup（以免安裝模式解壓，不寫入系統）
$Root = Split-Path -Parent $PSScriptRoot
$iscc = "$Root\.local\innosetup\ISCC.exe"
if (-not (Test-Path $iscc)) { throw "找不到 $iscc，請先把 Inno Setup 6 以 /PORTABLE=1 安裝到 .local\innosetup" }
$init = Get-Content "$Root\src\gachahub\__init__.py" -Raw -Encoding UTF8
if ($init -notmatch '__version__ = "([^"]+)"') { throw '讀不到版本號' }
$ver = $Matches[1]
& $iscc "/DAppVer=$ver" "$Root\installer\GachaPerfectAttendance.iss"
if ($LASTEXITCODE -ne 0) { throw "ISCC 失敗（$LASTEXITCODE）" }
Get-Item "$Root\runtime\installer\GachaPerfectAttendance-Setup-$ver.exe"
