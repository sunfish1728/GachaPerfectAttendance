# 保護機制接法

## 在場偵測

```python
from gachahub.core.presence import PresencePolicy, check

policy = PresencePolicy()
present, reason = check(policy)
```

`check()` 只做偵測，不等待、不排程。`idle_minutes` 預設 3，`check_fullscreen` 預設 True；停用時回傳 `(False, "")`。偵測 API 失敗會在回傳原因中說明；另一項偵測若成功確認在場，仍回傳 True。

倒數／排程端自行記錄同一次預定執行已延後幾次。在場而且次數小於 `max_defers`（預設 4）時，延後 `defer_minutes`（預設 15）分鐘；達到上限後直接進入倒數。`check()` 不會改變這些計數。

`idle_seconds()` 使用輸入時間的低 32 位，處理約 49.7 天的 tick 環繞；無法分辨超過一整個環繞週期都沒有輸入的情況。底層偵測函式可能拋例外，前端建議使用會攔截例外的 `check()`。

## 緊急停止

```python
from gachahub.core.hotkey import GlobalHotkey, format_hotkey

hotkey = GlobalHotkey("ctrl+shift+f12", ctx.cancel_event.set)
ok, reason = hotkey.start()
# ok=False 時顯示 reason；離開應用程式時：
hotkey.stop()
```

持有 `hotkey` 物件直到停止。回呼在熱鍵執行緒執行，可直接設定取消 Event；如果要更新 Qt 畫面，請轉成 Qt signal。可重複啟停，註冊／停止等待都有 3 秒上限。回呼本身應快速返回，長時間阻塞的回呼會讓停止逾時；此時記錄警告，舊執行緒退出前不允許重新啟動。沒有修改 Windows 熱鍵設定，解除註冊由原執行緒負責。

## 日誌卡死

步驟 `params["stall_minutes"]`：OK／一條龍預設 20 分鐘，通用適配器預設 0；0 或負數停用。通用適配器僅在 `completion="log_keyword"` 時監看 `log_file`。OK 監看 `working/logs`，一條龍監看安裝目錄 `.log`，都包含子資料夾。

啟動寬限期內不判定卡死；寬限期結束後按啟動以來最近一次日誌變化判斷。檔案新增、刪除、大小或 mtime 改變都會重設計時。判定卡死時沿用既有程序清理和 runner 失敗重試策略。一條龍已讀到成功標誌後，沿用原本 60 秒等待退出流程。

這是日誌進展偵測：腳本仍正常工作卻長時間不寫日誌時，也可能判為卡死；持續寫日誌但實際沒進展時不會判為卡死。

## 失敗截圖

建立 `RunContext` 時傳入 `failure_dir=Path("runtime/failures")` 即可啟用；預設 None 停用。每次嘗試失敗或逾時，runner 會在 `adapter.cleanup()` 前截圖並記錄路徑。成功、取消、未啟用步驟和執行前設定檢查失敗不截圖。資料夾的 PNG 按修改時間由舊到新清理，最多保留 50 張；請使用專用資料夾。

截圖包含所有螢幕。各適配器在 `run()` 的 finally 中仍會先清理程序，因此截圖時腳本視窗可能已關閉。GDI 可能無法取得受保護畫面或某些獨佔全螢幕內容；截圖失敗不阻止清理或重試。

Windows API 參考：[通知狀態列舉](https://learn.microsoft.com/zh-tw/windows/win32/api/shellapi/ne-shellapi-query_user_notification_state)、[GetDIBits](https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-getdibits)。
