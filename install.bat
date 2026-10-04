<# :
@echo off
chcp 65001 >nul
rem 二游腳本集合站 安裝程式：雙擊執行。這個檔案同時是批次檔與 PowerShell 腳本。
set "GH_SELF=%~f0"
set "GH_HERE=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=[IO.File]::ReadAllText($env:GH_SELF,[Text.Encoding]::UTF8); & ([ScriptBlock]::Create($s))"
set "GH_RC=%errorlevel%"
echo.
if not defined GACHAHUB_INSTALL_AUTO pause
exit /b %GH_RC%
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repo = 'sunfish1728/gachahub'
$UvUrl = 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
$Here = $env:GH_HERE.TrimEnd('\')

function Step($m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Info($m) { Write-Host "    $m" }
function Fail($m) { Write-Host "`n[錯誤] $m" -ForegroundColor Red; exit 1 }
function Ask($q, $default = $true) {
    if ($env:GACHAHUB_INSTALL_AUTO) { return $false }  # 無人值守（測試用）：一律不做選用動作
    $hint = if ($default) { '[Y/n]' } else { '[y/N]' }
    $a = Read-Host "$q $hint"
    if ([string]::IsNullOrWhiteSpace($a)) { return $default }
    return $a.Trim().ToLower().StartsWith('y')
}
function Invoke-Native($exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { Fail "$([IO.Path]::GetFileName($exe)) $($argv -join ' ') 失敗（代碼 $LASTEXITCODE）" }
}

Write-Host '二游腳本集合站 安裝程式' -ForegroundColor Magenta
Write-Host '所有檔案（Python、套件、快取、設定）都放在安裝資料夾內，不寫入系統或登錄檔。'

# ---- 決定安裝位置 ----
$inPlace = (Test-Path "$Here\pyproject.toml") -and (Test-Path "$Here\src\gachahub")
if ($inPlace) {
    $Root = $Here
    Step "在目前資料夾設定執行環境：$Root"
} else {
    $Root = Join-Path $Here 'gachahub'
    $custom = if ($env:GACHAHUB_INSTALL_DIR) { $env:GACHAHUB_INSTALL_DIR } else { Read-Host "安裝位置（直接按 Enter 使用 $Root）" }
    if (-not [string]::IsNullOrWhiteSpace($custom)) { $Root = $custom.Trim().Trim('"') }
    $Root = [IO.Path]::GetFullPath($Root)
}

# ---- 正在執行中的集合站必須先關閉 ----
$running = Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase) }
if ($running) {
    Write-Host "`n偵測到集合站正在執行（PID $($running.Id -join ', ')）。" -ForegroundColor Yellow
    if (-not (Ask '要結束它並繼續安裝嗎？')) { Fail '請先從托盤選「離開」關閉集合站後再執行安裝。' }
    $running | Stop-Process -Force
    Start-Sleep -Seconds 1
}

New-Item -ItemType Directory -Force -Path "$Root\runtime" | Out-Null
$Tmp = Join-Path $Root 'runtime\install-tmp'

# ---- 下載程式本體（全新安裝或更新） ----
if (-not $inPlace) {
    Step '取得最新版本資訊'
    $zipUrl = "https://github.com/$Repo/archive/refs/heads/main.zip"
    $label = 'main 分支'
    try {
        $rel = Invoke-RestMethod -UseBasicParsing -Headers @{ 'User-Agent' = 'gachahub-installer' } `
            -Uri "https://api.github.com/repos/$Repo/releases/latest" -TimeoutSec 20
        if ($rel.tag_name) {
            $zipUrl = "https://github.com/$Repo/archive/refs/tags/$($rel.tag_name).zip"
            $label = $rel.tag_name
        }
    } catch { Info '查不到 Release，改用 main 分支。' }
    Info "版本：$label"

    Step '下載程式'
    if (Test-Path $Tmp) { Remove-Item -Recurse -Force $Tmp }
    New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
    $zip = Join-Path $Tmp 'src.zip'
    try { Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $zip -TimeoutSec 300 }
    catch { Fail "下載失敗：$($_.Exception.Message)" }
    Expand-Archive -Path $zip -DestinationPath "$Tmp\x" -Force
    $srcDir = Get-ChildItem "$Tmp\x" -Directory | Select-Object -First 1
    if (-not $srcDir -or -not (Test-Path "$($srcDir.FullName)\pyproject.toml")) { Fail '下載的壓縮檔內容不正確。' }

    Step "安裝到 $Root"
    # 程式碼資料夾整個換新；data（你的設定、任務鏈、歷史）與 runtime 保留
    foreach ($d in 'src', 'tests') { if (Test-Path "$Root\$d") { Remove-Item -Recurse -Force "$Root\$d" } }
    Get-ChildItem $srcDir.FullName -Force | ForEach-Object {
        Copy-Item -Path $_.FullName -Destination $Root -Recurse -Force
    }
    Remove-Item -Recurse -Force $Tmp
    Info '已保留 data\ 中的設定與紀錄。'
}

Set-Location $Root

# ---- uv（放在 .local\uv） ----
$Uv = "$Root\.local\uv\uv.exe"
if (-not (Test-Path $Uv)) {
    Step '下載 uv（Python 套件管理工具）'
    New-Item -ItemType Directory -Force -Path "$Root\.local\uv" | Out-Null
    $uvZip = Join-Path $Root 'runtime\uv.zip'
    try { Invoke-WebRequest -UseBasicParsing -Uri $UvUrl -OutFile $uvZip -TimeoutSec 300 }
    catch { Fail "下載 uv 失敗：$($_.Exception.Message)" }
    Expand-Archive -Path $uvZip -DestinationPath "$Root\.local\uv" -Force
    Remove-Item -Force $uvZip
    if (-not (Test-Path $Uv)) {
        $found = Get-ChildItem "$Root\.local\uv" -Recurse -Filter uv.exe | Select-Object -First 1
        if (-not $found) { Fail '解壓縮後找不到 uv.exe。' }
        Copy-Item $found.FullName $Uv
    }
}

# 所有快取與 Python 都限制在安裝資料夾內
$env:UV_CACHE_DIR = "$Root\.local\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$Root\.local\python"
$env:UV_PYTHON_PREFERENCE = 'only-managed'
$env:UV_PYTHON_INSTALL_BIN = '0'
$env:UV_TOOL_DIR = "$Root\.local\uv-tools"
$env:UV_NO_CONFIG = '1'
$env:PIP_CACHE_DIR = "$Root\.local\pip-cache"
$env:PYTHONPYCACHEPREFIX = "$Root\.local\pycache"
$env:PYTHONUTF8 = '1'

Step '安裝 Python 3.12（只放在 .local\python）'
Invoke-Native $Uv @('python', 'install', '3.12')

$Py = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Step '建立虛擬環境'
    Invoke-Native $Uv @('venv', '--python', '3.12', "$Root\.venv")
}

Step '安裝相依套件'
Invoke-Native $Uv @('pip', 'install', '--python', $Py, '-e', $Root)

Step '檢查'
Invoke-Native $Py @('-c', 'import gachahub, PySide6, qfluentwidgets; print("gachahub", gachahub.__version__)')

# ---- 捷徑（選用） ----
$pyw = "$Root\.venv\Scripts\pythonw.exe"
if (Ask '要在桌面建立捷徑嗎？') {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path $desktop '二游腳本集合站.lnk'))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = '-m gachahub'
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = "$Root\assets\icon.ico"
    $lnk.Description = '二游腳本集合站'
    $lnk.Save()
    Info '已建立桌面捷徑。'
}

Write-Host "`n安裝完成！之後用 $Root\start.bat 或桌面捷徑開啟。" -ForegroundColor Green
Info '更新：重新執行這個安裝檔即可（設定與紀錄會保留）。'
Info '解除安裝：結束集合站後刪除整個資料夾（與桌面捷徑）即可。'
if (Ask '現在開啟集合站嗎？') {
    Start-Process -FilePath "$Root\start.bat" -WorkingDirectory $Root -WindowStyle Hidden
}
exit 0
