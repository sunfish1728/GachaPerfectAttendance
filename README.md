<p align="center"><img src="assets/icon.png" width="120" alt=""></p>

# 二遊全勤君（GachaPerfectAttendance）

把各家二游自動化腳本集中起來：組成任務鏈、定時自動執行，執行前靜音、結束後還原，跑完推播通知。

目前支援：
- **OK 系列**：ok 異環（ok-nte）
- **一條龍系列**：絕區零一條龍（ZenlessZoneZero-OneDragon）
- **通用**：任意 exe / 指令

## 安裝

1. 到 [Releases](https://github.com/sunfish1728/GachaPerfectAttendance/releases/latest) 下載 `GachaPerfectAttendance-Setup-<版本>.exe`。
2. 執行安裝程式：選擇安裝位置、勾選是否建立桌面捷徑。程式與 Python 環境都包在安裝檔內，安裝時不需要網路。
3. 從「開始」選單或桌面捷徑開啟。

- 自帶獨立的 Python 3.12，所有檔案與設定都放在安裝資料夾內，不使用也不影響系統的 Python。
- **更新**：下載新版安裝程式直接執行，會裝到原本的位置；設定、任務鏈與歷史紀錄（`data\`）會保留。
- **解除安裝**：Windows「設定 → 應用程式」中移除；可選擇是否保留設定與紀錄。
- 需要 Windows 10/11（64 位元）。

> OK 系列與一條龍腳本需要系統管理員權限，所以本程式預設以系統管理員身分啟動（開啟時 Windows 會詢問一次 UAC）。不需要時可在「設定」關閉。

## 功能

- **任務鏈**：多個腳本依序執行；失敗時重試／略過／中止；每步可設超時。
- **排程**：每天、每週、間隔、單次；錯過補跑、今日暫停；可透過 Windows 工作排程器喚醒電腦。
- **開始前倒數**：可立即開始、延後或略過；偵測到你正在使用電腦時自動延後。
- **執行前後動作**：靜音並還原、結束指定程式、跑完睡眠／關機。
- **保護**：全域緊急停止熱鍵（預設 Ctrl+Shift+F12）、日誌卡死偵測、失敗現場截圖。
- **歷史與通知**：近 7 天統計與每步明細；Telegram、Discord、Server醬、Bark、Email、Webhook 推播。
- **首次使用嚮導**：自動掃描已安裝的腳本並建立每日任務鏈。
- **更新檢查**：適配器定義可從遠端倉庫同步；本程式新版本會提示。

## 新增腳本支援

每種腳本以「適配器」接入，簡單的情況只要在 `adapters/` 寫一個 YAML，見 [adapters/README.md](adapters/README.md)。

## 開發

```powershell
. .\scripts\env.ps1            # 所有工具環境限制在專案資料夾內
powershell -ExecutionPolicy Bypass -File installer\setup-core.ps1 -Root . -Local   # 在原地建立 .local 與 .venv
.\scripts\build_installer.ps1  # 建立程式包並編譯離線安裝程式（需 Inno Setup 6 於 .local\innosetup）
.venv\Scripts\python.exe -m pytest -q
```

設計說明見 [docs/PLAN.md](docs/PLAN.md)。
