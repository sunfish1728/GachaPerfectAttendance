<#
二遊全勤君：建立／更新執行環境（安裝程式與開發者共用，不需互動）。

  -Root        安裝資料夾
  -Tag         要下載的版本標籤（例如 v0.2.0）；空白＝最新 Release
  -Local       不下載程式，只在 Root（git clone 的資料夾）建立環境
  -StatusFile  目前步驟寫入此檔（UTF-8），結束時寫 <StatusFile>.done（內容為結束碼），供安裝精靈顯示進度

所有檔案（uv、Python、套件、快取）都放在 Root 底下，不寫入系統或登錄檔。
#>
param(
    [Parameter(Mandatory = $true)][string]$Root,
    [string]$Tag = "",
    [switch]$Local,
    [string]$StatusFile = ""
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$Repo = 'sunfish1728/GachaPerfectAttendance'
$UvUrl = 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip'
$Root = [IO.Path]::GetFullPath($Root.Trim().Trim('"').TrimEnd('\'))
$Utf8Bom = New-Object Text.UTF8Encoding $true

New-Item -ItemType Directory -Force -Path "$Root\runtime" | Out-Null
$LogFile = "$Root\runtime\setup.log"
"==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 安裝開始 Root=$Root Tag=$Tag Local=$Local" | Out-File -FilePath $LogFile -Encoding utf8 -Append

function Log($m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -FilePath $LogFile -Encoding utf8 -Append }
function Status($m) {
    Log $m
    Write-Host "==> $m"
    if ($StatusFile) { [IO.File]::WriteAllText($StatusFile, $m, $Utf8Bom) }
}
function Invoke-Native($exe, [string[]]$argv) {
    Log "> $([IO.Path]::GetFileName($exe)) $($argv -join ' ')"
    # PowerShell 5.1 在 Stop 模式下會把原生程式的 stderr（uv 的進度訊息）當成錯誤，這裡暫時放寬
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = & $exe @argv 2>&1; $code = $LASTEXITCODE } finally { $ErrorActionPreference = $old }
    $out | ForEach-Object { "$_" } | Out-File -FilePath $LogFile -Encoding utf8 -Append
    if ($code -ne 0) { throw "$([IO.Path]::GetFileName($exe)) $($argv -join ' ') 失敗（代碼 $code）" }
}
function Get-File($url, $dest) {
    Log "下載 $url"
    $tries = 0
    while ($true) {
        try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dest -TimeoutSec 300; return }
        catch {
            $tries++
            if ($tries -ge 3) { throw "下載失敗：$url（$($_.Exception.Message)）" }
            Start-Sleep -Seconds (3 * $tries)
        }
    }
}

$exitCode = 0
try {
    # ---- 關閉這個資料夾裡正在執行的程式（更新時） ----
    $running = Get-Process -Name pythonw, python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith("$Root\", [StringComparison]::OrdinalIgnoreCase) }
    if ($running) {
        Status '關閉執行中的二遊全勤君'
        $running | Stop-Process -Force
        Start-Sleep -Seconds 1
    }

    # ---- 下載程式本體 ----
    if (-not $Local) {
        Status '取得版本資訊'
        $label = $Tag
        if (-not $Tag) {
            try {
                $rel = Invoke-RestMethod -UseBasicParsing -Headers @{ 'User-Agent' = 'gachahub-installer' } `
                    -Uri "https://api.github.com/repos/$Repo/releases/latest" -TimeoutSec 20
                $label = $rel.tag_name
            } catch { $label = '' }
        }
        $zipUrl = if ($label) { "https://github.com/$Repo/archive/refs/tags/$label.zip" } else { "https://github.com/$Repo/archive/refs/heads/main.zip" }
        Status "下載程式（$(if ($label) { $label } else { 'main' })）"
        $tmp = "$Root\runtime\install-tmp"
        if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
        New-Item -ItemType Directory -Force -Path $tmp | Out-Null
        Get-File $zipUrl "$tmp\src.zip"
        Status '解壓縮程式'
        Expand-Archive -Path "$tmp\src.zip" -DestinationPath "$tmp\x" -Force
        $srcDir = Get-ChildItem "$tmp\x" -Directory | Select-Object -First 1
        if (-not $srcDir -or -not (Test-Path "$($srcDir.FullName)\pyproject.toml")) { throw '下載的壓縮檔內容不正確' }
        # 程式碼資料夾整個換新；data（設定、任務鏈、歷史）、runtime、.local、.venv 保留
        foreach ($d in 'src', 'tests') { if (Test-Path "$Root\$d") { Remove-Item -Recurse -Force "$Root\$d" } }
        Get-ChildItem $srcDir.FullName -Force | ForEach-Object { Copy-Item -Path $_.FullName -Destination $Root -Recurse -Force }
        Remove-Item -Recurse -Force $tmp
    }
    if (-not (Test-Path "$Root\pyproject.toml")) { throw "找不到程式（$Root\pyproject.toml）" }

    # ---- uv ----
    $Uv = "$Root\.local\uv\uv.exe"
    if (-not (Test-Path $Uv)) {
        Status '下載 uv（Python 套件管理工具）'
        New-Item -ItemType Directory -Force -Path "$Root\.local\uv" | Out-Null
        $uvZip = "$Root\runtime\uv.zip"
        Get-File $UvUrl $uvZip
        Expand-Archive -Path $uvZip -DestinationPath "$Root\.local\uv" -Force
        Remove-Item -Force $uvZip
        if (-not (Test-Path $Uv)) {
            $found = Get-ChildItem "$Root\.local\uv" -Recurse -Filter uv.exe | Select-Object -First 1
            if (-not $found) { throw '解壓縮後找不到 uv.exe' }
            Copy-Item $found.FullName $Uv
        }
    }

    # 所有快取與 Python 都限制在安裝資料夾內
    $env:UV_CACHE_DIR = "$Root\.local\uv-cache"
    $env:UV_PYTHON_INSTALL_DIR = "$Root\.local\python"
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $env:UV_PYTHON_INSTALL_BIN = '0'
    $env:UV_PYTHON_INSTALL_REGISTRY = '0'
    $env:UV_TOOL_DIR = "$Root\.local\uv-tools"
    $env:UV_NO_CONFIG = '1'
    $env:UV_NO_PROGRESS = '1'
    $env:PIP_CACHE_DIR = "$Root\.local\pip-cache"
    $env:PYTHONPYCACHEPREFIX = "$Root\.local\pycache"
    $env:PYTHONUTF8 = '1'

    Status '安裝 Python 3.12'
    Invoke-Native $Uv @('python', 'install', '3.12')
    $Py = "$Root\.venv\Scripts\python.exe"
    if (-not (Test-Path $Py)) {
        Status '建立虛擬環境'
        Invoke-Native $Uv @('venv', '--python', '3.12', "$Root\.venv")
    }
    Status '安裝相依套件（首次約 300 MB，需要幾分鐘）'
    Invoke-Native $Uv @('pip', 'install', '--python', $Py, '-e', $Root)
    Status '檢查安裝結果'
    Invoke-Native $Py @('-c', 'import gachahub, PySide6, qfluentwidgets; print(gachahub.__version__)')
    Status '完成'
} catch {
    $exitCode = 1
    Status "失敗：$($_.Exception.Message)"
    Log ($_ | Out-String)
} finally {
    if ($StatusFile) { [IO.File]::WriteAllText("$StatusFile.done", "$exitCode", $Utf8Bom) }
}
exit $exitCode
