"""開發用：建立示範資料、在畫面外渲染各頁並存成 PNG。"""
import os, sys, time, tempfile
from pathlib import Path
DEV = Path(__file__).resolve().parents[1] / "runtime" / "dev"; DEV.mkdir(parents=True, exist_ok=True)
root = Path(tempfile.mkdtemp(dir=DEV))
os.environ["GACHAHUB_ROOT"] = str(root)
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QEventLoop
from gachahub.core.config import Paths
from gachahub.core.models import TaskChain, TaskStep, HookSpec, FailPolicy
import shutil
shutil.copytree(DEV.parents[1] / "adapters", root / "adapters")
paths = Paths(); paths.ensure()
# 安全：示範中以假鉤子取代真正的靜音與電源動作，避免真的讓電腦睡眠
from gachahub.hooks import builtin  # noqa: F401
from gachahub.hooks.base import HOOKS, Hook
for _t in ("mute", "power", "kill_processes"):
    HOOKS[_t] = type(f"Fake_{_t}", (Hook,), {"type": _t, "apply": lambda self, ctx: ctx.log(f"（示範）{self.type} {self.params}")})
app = QApplication(sys.argv)
from gachahub.gui.i18n import install_translator
install_translator(app)
from gachahub.gui.settings_page import load_config, cfg
from qfluentwidgets import setTheme, Theme
load_config(paths.root / "data" / "settings.json")
theme = sys.argv[1] if len(sys.argv) > 1 else "light"
setTheme(Theme.DARK if theme == "dark" else Theme.LIGHT)
from gachahub.gui.controller import AppController
from gachahub.gui.main_window import MainWindow
c = AppController(paths)
py = sys.executable
c.store.save(TaskChain(name="每日清體力", steps=[
    TaskStep(name="異環日常", adapter="generic", params={"command": py, "args": ["-c", "import time;print(1);time.sleep(1)"]}),
    TaskStep(name="絕區零一條龍", adapter="generic", on_fail=FailPolicy.RETRY, retries=2, timeout=5400, params={"command": py, "args": ["-c", "raise SystemExit(2)"]}),
    TaskStep(name="鳴潮（停用）", adapter="generic", enabled=False, params={"command": py}),
], pre_hooks=[HookSpec(type="mute")], post_actions=[HookSpec(type="power", params={"action": "sleep"})]))
# 只用來展示表單，示範中絕不執行（會開啟真正的腳本）
c.store.save(TaskChain(name="真實腳本（不執行）", steps=[
    TaskStep(name="絕區零一條龍", adapter="zzz-onedragon", timeout=7200, params={"install_dir": r"C:\zzzbot", "instance": "1", "close_game": True}),
    TaskStep(name="異環日常", adapter="ok-nte", params={"install_dir": r"D:\ok-nte", "task": "DailyRoutineTask"}),
]))
c.store.save(TaskChain(name="週末深淵", steps=[TaskStep(name="深淵", adapter="generic", params={"command": py})]))
w = MainWindow(c); w.setMicaEffectEnabled(False); w.resize(1080, 720); w.show()
out = DEV / f"shots_{theme}"; out.mkdir(exist_ok=True)
def pump(sec):
    end = time.monotonic() + sec
    while time.monotonic() < end:
        app.processEvents(); time.sleep(0.02)
pump(1.0)
w.grab().save(str(out / "1_home.png"))
w.switchTo(w.chainPage); pump(0.8); w.grab().save(str(out / "2_chain.png"))
from gachahub.gui.step_dialog import StepDialog
daily = next(x for x in c.chains() if x.name == "每日清體力")
d = StepDialog(c.registry, daily.steps[1], w); d.show(); pump(0.8); w.grab().save(str(out / "3_dialog.png")); d.close(); pump(0.3)
real = next(x for x in c.chains() if x.name.startswith("真實"))
for i, st in enumerate(real.steps):
    try:
        d = StepDialog(c.registry, st, w); d.show(); pump(0.8); w.grab().save(str(out / f"3_dialog_real{i}.png")); d.close(); pump(0.3)
    except Exception as e:
        print("dialog", i, "failed:", e)
w.chainPage.select(real.name); pump(0.5); w.grab().save(str(out / "2_chain_real.png"))
c.runner.retry_delay = 0.2
w.run_chain(next(x for x in c.chains() if x.name == "每日清體力")); pump(1.5); w.grab().save(str(out / "4_running.png"))
deadline = time.monotonic() + 30
while c.running and time.monotonic() < deadline: pump(0.2)
pump(0.5); w.grab().save(str(out / "5_done.png"))
w.switchTo(w.homePage); pump(0.6); w.grab().save(str(out / "6_home_after.png"))
# 排程（只建立在未來的排程，示範中不會觸發；不對「真實腳本」建立排程）
from datetime import datetime, timedelta
from gachahub.core.schedule import Schedule, ScheduleKind
svc = w.scheduler
svc.save(Schedule(chain="每日清體力", kind=ScheduleKind.DAILY, time="04:30", note="體力剛好回滿"))
svc.save(Schedule(chain="週末深淵", kind=ScheduleKind.WEEKLY, time="21:00", weekdays=[5, 6], wake_computer=True))
svc.save(Schedule(chain="每日清體力", kind=ScheduleKind.INTERVAL, time="06:00", interval_minutes=480, enabled=False))
w.switchTo(w.schedulePage); pump(0.8); w.grab().save(str(out / "8_schedule.png"))
from gachahub.gui.schedule_page import ScheduleDialog
sd = ScheduleDialog([x.name for x in c.chains()], svc.schedules()[1], w); sd.show(); pump(0.8); w.grab().save(str(out / "9_schedule_dialog.png")); sd.close(); pump(0.3)
from gachahub.gui.countdown import CountdownToast
from gachahub.gui.scheduler_service import PendingRun
toast = CountdownToast(PendingRun(svc.schedules()[0], datetime.now().replace(second=0, microsecond=0), defers=1), 60)
toast.show(); pump(1.2); toast.grab().save(str(out / "10_countdown.png")); toast.dismiss(); pump(0.2)
w.switchTo(w.homePage); pump(0.6); w.grab().save(str(out / "6_home_after.png"))
w.switchTo(w.settingsPage); pump(0.6); w.grab().save(str(out / "7_settings.png"))
w.settingsPage.verticalScrollBar().setValue(w.settingsPage.verticalScrollBar().maximum() // 2); pump(0.5); w.grab().save(str(out / "7b_settings.png"))
# M4：歷史、通知、嚮導（通知只填示範用假值，不發送）
w.switchTo(w.historyPage); pump(0.8); w.grab().save(str(out / "11_history.png"))
from gachahub.core.notify import Channel, NotifyConfig
c.save_notify_config(NotifyConfig(channels=[
    Channel(type="telegram", name="手機", params={"bot_token": "000000:DEMO", "chat_id": "123"}),
    Channel(type="discord", name="伺服器頻道", enabled=False, params={"webhook_url": "https://example.invalid/demo"}),
]))
w.notifyPage.reload(); w.switchTo(w.notifyPage); pump(0.6); w.grab().save(str(out / "12_notify.png"))
from gachahub.gui.notify_page import ChannelDialog
cd = ChannelDialog(c.notify_config().channels[0], w); cd.show(); pump(0.6); w.grab().save(str(out / "13_channel_dialog.png")); cd.close(); pump(0.2)
from gachahub.gui.wizard import SetupWizard
wz = SetupWizard(c, svc, w); wz.show(); pump(0.5); w.grab().save(str(out / "14_wizard1.png"))
wz.validate(); pump(1.0); w.grab().save(str(out / "15_wizard2.png"))
wz.validate(); pump(0.5); w.grab().save(str(out / "16_wizard3.png")); wz.close(); pump(0.2)
w.switchTo(w.homePage); w.homePage.reload(); pump(0.5); w.grab().save(str(out / "17_home.png"))
w.switchTo(w.settingsPage); pump(0.4)
w.settingsPage.verticalScrollBar().setValue(w.settingsPage.verticalScrollBar().maximum()); pump(0.5); w.grab().save(str(out / "18_settings_update.png"))
w._shutdown()
print("saved", out, "running:", c.running)
w.tray.hide()
