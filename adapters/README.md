# 宣告式適配器

每個 `*.yaml` 定義一種腳本接法，以內建適配器（目前只有 `generic`）為基底，主程式無需改版即可新增或調整。

```yaml
id: my-script          # 任務步驟以此 id 引用
name: 我的腳本
base: generic
defaults:              # 步驟 params 會覆蓋這些值
  command: D:/Tools/script.exe
  args: ["-t", "1", "-e"]
  completion: exit     # exit | process_gone | marker_file | log_keyword
  kill_on_finish: [Game.exe]
fields:                # 給 GUI 顯示的參數說明（可選）
  - {key: command, label: 執行檔路徑}
```

完整參數見 `src/gachahub/adapters/generic.py` 開頭說明。
