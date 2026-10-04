"""排程服務：定時檢查到期排程 → 在場偵測／延後 → 倒數 → 交給控制器執行；忙碌時排隊。

狀態全部在主執行緒（QTimer）中處理，不需要鎖。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from ..core import presence
from ..core.schedule import Schedule, ScheduleStore, check_due, upcoming
from .controller import AppController
from .settings_page import cfg

log = logging.getLogger("gachahub.scheduler")
CHECK_INTERVAL_MS = 20_000


@dataclass
class PendingRun:
    schedule: Schedule
    planned: datetime  # 排程預定時間
    not_before: datetime = field(default_factory=datetime.now)  # 延後後的最早開始時間
    defers: int = 0
    reason: str = ""  # 最近一次延後原因


class SchedulerService(QObject):
    queueChanged = Signal()
    countdownRequested = Signal(object)  # PendingRun：GUI 顯示倒數視窗
    countdownCancelled = Signal()
    notice = Signal(str, str)  # (標題, 內容)：交給托盤通知
    schedulesChanged = Signal()
    runRequested = Signal(object)  # TaskChain：使用者手動「立即執行」，交給主視窗（含權限提醒）

    def __init__(self, controller: AppController, parent: QObject | None = None):
        super().__init__(parent)
        self.controller = controller
        self.store = ScheduleStore(controller.paths.root / "data")
        self.queue: list[PendingRun] = []
        self.counting: PendingRun | None = None  # 正在倒數的項目
        self.paused_until: datetime | None = None  # 「今日暫停」
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_INTERVAL_MS)
        self.timer.timeout.connect(self.tick)
        controller.runFinished.connect(lambda *_: QTimer.singleShot(3000, self.tick))
        controller.chainRenamed.connect(self._on_chain_renamed)

    def start(self) -> None:
        self.timer.start()
        QTimer.singleShot(2000, self.tick)  # 啟動後稍等再檢查，讓視窗先出現

    # --- 排程存取 ---

    def schedules(self) -> list[Schedule]:
        return self.store.list()

    def save(self, schedule: Schedule) -> None:
        self.store.upsert(schedule)
        self.schedulesChanged.emit()

    def delete(self, schedule_id: str) -> None:
        self.store.delete(schedule_id)
        self.queue = [p for p in self.queue if p.schedule.id != schedule_id]
        self.schedulesChanged.emit()
        self.queueChanged.emit()

    def upcoming(self, limit: int = 5) -> list[tuple[datetime, Schedule]]:
        return upcoming(self.schedules(), datetime.now(), limit)

    def _on_chain_renamed(self, old: str, new: str) -> None:
        self.store.rename_chain(old, new)
        self.schedulesChanged.emit()

    # --- 暫停 ---

    def pause_today(self) -> None:
        tomorrow = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        self.paused_until = tomorrow
        self.notice.emit("今日排程已暫停", "明天 00:00 自動恢復")
        self.queueChanged.emit()

    def resume(self) -> None:
        self.paused_until = None
        self.queueChanged.emit()
        self.tick()

    @property
    def paused(self) -> bool:
        if self.paused_until and datetime.now() >= self.paused_until:
            self.paused_until = None
        return self.paused_until is not None

    # --- 主迴圈 ---

    def tick(self) -> None:
        now = datetime.now()
        try:
            result = check_due(self.schedules(), self.store, now)
        except Exception:
            log.exception("檢查排程失敗")
            return
        for s, planned in result.skipped:
            log.info("略過錯過的排程 %s（%s）", s.chain, planned)
            self.controller.notify_async(
                "schedule_skip", f"⏭ 已略過排程：{s.chain}",
                f"預定 {planned:%m/%d %H:%M} 的排程已超過補跑時間（{s.catch_up_minutes} 分鐘），本次略過。",
            )
        queued_ids = {p.schedule.id for p in self.queue} | ({self.counting.schedule.id} if self.counting else set())
        for s, planned in result.due:
            if s.id in queued_ids:
                continue
            if self.paused:
                self.store.mark_fired(s.id, planned)
                self.controller.runLog.emit(f"今日已暫停，略過排程「{s.chain}」")
                continue
            self.queue.append(PendingRun(s, planned))
            self.queueChanged.emit()
        self._advance()

    def _advance(self) -> None:
        if self.counting is not None or self.controller.running or not self.queue:
            return
        now = datetime.now()
        item = next((p for p in self.queue if p.not_before <= now), None)
        if item is None:
            return
        # 在場偵測：使用者正在用電腦就延後
        policy = presence.PresencePolicy(
            enabled=cfg.presenceEnabled.value,
            idle_minutes=cfg.presenceIdleMinutes.value,
            check_fullscreen=cfg.presenceFullscreen.value,
            defer_minutes=cfg.presenceDeferMinutes.value,
            max_defers=cfg.presenceMaxDefers.value,
        )
        present, reason = presence.check(policy)
        if present and item.defers < policy.max_defers:
            item.defers += 1
            item.reason = reason
            item.not_before = now + timedelta(minutes=policy.defer_minutes)
            self.controller.runLog.emit(
                f"排程「{item.schedule.chain}」延後 {policy.defer_minutes} 分鐘（{reason}，第 {item.defers} 次）"
            )
            self.queueChanged.emit()
            return
        self.counting = item
        self.queueChanged.emit()
        if cfg.countdownSeconds.value > 0:
            self.countdownRequested.emit(item)
        else:
            self.confirm()

    # --- 倒數結果（由倒數視窗呼叫） ---

    def confirm(self) -> None:
        item = self.counting
        if item is None:
            return
        self.counting = None
        chains = {c.name: c for c in self.controller.chains()}
        chain = chains.get(item.schedule.chain)
        self._remove(item)
        self.store.mark_fired(item.schedule.id, item.planned)
        if chain is None:
            self.notice.emit("排程無法執行", f"找不到任務鏈「{item.schedule.chain}」")
            return
        if not self.controller.start_chain(chain, trigger="schedule", schedule_id=item.schedule.id):
            # 剛好有其他任務開始：放回佇列等下一輪
            self.queue.insert(0, item)
            self.queueChanged.emit()
            return
        self.notice.emit("排程開始", f"{chain.name}（{item.planned:%H:%M} 的排程）")

    def postpone(self, minutes: int) -> None:
        item = self.counting
        if item is None:
            return
        self.counting = None
        item.not_before = datetime.now() + timedelta(minutes=minutes)
        item.reason = "使用者延後"
        self.controller.runLog.emit(f"排程「{item.schedule.chain}」由使用者延後 {minutes} 分鐘")
        self.countdownCancelled.emit()
        self.queueChanged.emit()

    def skip(self) -> None:
        item = self.counting
        if item is None:
            return
        self.counting = None
        self._remove(item)
        self.store.mark_fired(item.schedule.id, item.planned)
        self.controller.runLog.emit(f"已略過這次排程「{item.schedule.chain}」")
        self.countdownCancelled.emit()

    def run_now(self, schedule: Schedule) -> None:
        """排程頁的「立即執行」：不看在場偵測與倒數。"""
        chains = {c.name: c for c in self.controller.chains()}
        chain = chains.get(schedule.chain)
        if chain is None:
            self.notice.emit("無法執行", f"找不到任務鏈「{schedule.chain}」")
            return
        self.runRequested.emit(chain)

    def _remove(self, item: PendingRun) -> None:
        if item in self.queue:
            self.queue.remove(item)
        self.queueChanged.emit()
