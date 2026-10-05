"""任務鏈執行器：前置鉤子 → 各步驟（失敗策略）→ 反序還原 → 後置動作。

還原一定會執行（含失敗、取消、例外）；後置動作在使用者取消時不執行，
避免緊急停止後還把電腦關機。
"""

from __future__ import annotations

import threading
import time
import re
from datetime import datetime
from typing import Any

from ..hooks.base import Hook, create_hook
from .adapter import AdapterError
from . import compat, process
from .context import Cancelled, DurationReached, RunContext, StepTimeout
from .models import ChainReport, FailPolicy, StepResult, StepStatus, TaskChain, TaskStep
from .registry import AdapterRegistry
from .snapshot import capture_screen

STATUS_TEXT = {
    StepStatus.SUCCESS: "成功",
    StepStatus.FAILED: "失敗",
    StepStatus.TIMEOUT: "逾時",
    StepStatus.CANCELLED: "已取消",
    StepStatus.SKIPPED: "已略過",
}


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    minutes = round(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours} 小時 {minutes} 分鐘"
    return f"{hours} 小時" if hours else f"{minutes} 分鐘"


class ChainRunner:
    retry_delay = 3.0

    def __init__(self, registry: AdapterRegistry):
        self.registry = registry
        self._lock = threading.Lock()  # 前台互斥：同一時間只跑一條任務鏈

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def run(self, chain: TaskChain, ctx: RunContext) -> ChainReport:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("已有任務鏈在執行中")
        try:
            return self._run(chain, ctx)
        finally:
            self._lock.release()

    def _run(self, chain: TaskChain, ctx: RunContext) -> ChainReport:
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        report = ChainReport(chain=chain.name, started_at=datetime.now())
        applied: list[tuple[Hook, Any]] = []
        ctx.log(f"開始任務鏈：{chain.name}")
        ctx.emit("chain_start", chain=chain.name, steps=[s.name for s in chain.steps])
        try:
            for spec in chain.pre_hooks:
                if not spec.enabled:
                    continue
                try:
                    hook = create_hook(spec.type, spec.params)
                    applied.append((hook, hook.apply(ctx)))
                except Cancelled:
                    raise
                except Exception as e:  # 前置鉤子失敗不阻止任務
                    ctx.log(f"前置鉤子 {spec.type} 失敗：{e}")

            for i, step in enumerate(chain.steps):
                ctx.emit("step_start", index=i, name=step.name)
                result = self._run_step(step, ctx)
                report.results.append(result)
                ctx.emit("step_end", index=i, result=result)
                if result.status == StepStatus.CANCELLED:
                    report.cancelled = True
                    break
                if result.status not in (StepStatus.SUCCESS, StepStatus.SKIPPED) and step.on_fail == FailPolicy.ABORT:
                    ctx.log(f"步驟「{step.name}」失敗，依設定中止任務鏈")
                    report.aborted = True
                    break
        except Cancelled:
            report.cancelled = True
        finally:
            ctx.deadline = None
            for hook, state in reversed(applied):
                try:
                    hook.restore(ctx, state)
                except Exception as e:
                    ctx.log(f"還原 {hook.type} 失敗：{e}")

        if not report.cancelled:
            for spec in chain.post_actions:
                if not spec.enabled:
                    continue
                try:
                    create_hook(spec.type, spec.params).apply(ctx)
                except Exception as e:
                    ctx.log(f"後置動作 {spec.type} 失敗：{e}")

        report.finished_at = datetime.now()
        ctx.log(f"任務鏈結束：{chain.name}（{'成功' if report.ok else '有失敗'}）")
        return report

    def _run_step(self, step: TaskStep, ctx: RunContext) -> StepResult:
        started = datetime.now()
        if not step.enabled:
            return StepResult(step=step.name, status=StepStatus.SKIPPED, message="已停用",
                              attempts=0, started_at=started, finished_at=started)
        max_attempts = 1 + max(0, step.retries) if step.on_fail == FailPolicy.RETRY else 1
        status, message, attempt = StepStatus.FAILED, "", 0
        try:
            adapter = self.registry.create(step.adapter)
            adapter.validate(step.params)
        except AdapterError as e:
            ctx.log(f"步驟「{step.name}」設定錯誤：{e}")
            return StepResult(step=step.name, status=StepStatus.FAILED, message=str(e),
                              attempts=0, started_at=started, finished_at=datetime.now())

        try:
            info = self.registry.infos.get(step.adapter)
            compatibility = compat.check(info, adapter, step.params) if info is not None else None
        except Exception:  # 相容檢查失敗不影響原本執行
            compatibility = None
        if compatibility is not None and not compatibility.ok:
            if compatibility.block:
                ctx.log(f"步驟「{step.name}」相容性檢查阻擋：{compatibility.message}")
                return StepResult(step=step.name, status=StepStatus.FAILED,
                                  message=compatibility.message, attempts=0,
                                  started_at=started, finished_at=datetime.now())
            ctx.log(f"警告：{compatibility.message}")
            ctx.emit("compat_warning", step=step.name, message=compatibility.message)

        # 最長運行時間涵蓋整個步驟（含重試）
        limit_at = time.monotonic() + step.max_duration if step.max_duration > 0 else None
        # 設了最長運行時間時，逾時上限至少放寬到它之後，否則會先以逾時（失敗）收場
        timeout = max(step.timeout, step.max_duration + 60) if limit_at is not None else step.timeout
        for attempt in range(1, max_attempts + 1):
            ctx.log(f"步驟「{step.name}」第 {attempt}/{max_attempts} 次")
            ctx.deadline = time.monotonic() + timeout
            ctx.limit_at = limit_at
            ctx.failure_hook = (
                (lambda a=attempt: self._capture_failure(ctx, step.name, a)) if ctx.failure_dir is not None else None
            )
            reached = False
            try:
                message = adapter.run(ctx, step.params)
                status = StepStatus.SUCCESS
            except Cancelled:
                status, message = StepStatus.CANCELLED, "使用者取消"
            except DurationReached:
                reached = True
                status, message = StepStatus.SUCCESS, f"已達最長運行時間 {format_duration(step.max_duration)}，已結束程序"
            except StepTimeout:
                status, message = StepStatus.TIMEOUT, f"超過 {timeout:.0f} 秒"
            except AdapterError as e:
                status, message = StepStatus.FAILED, str(e)
            except Exception as e:
                status, message = StepStatus.FAILED, f"未預期錯誤：{e!r}"
            finally:
                ctx.deadline = None
                ctx.limit_at = None
                # 後備：適配器沒有在關閉程序前截圖時才在這裡補拍（capture_failure 每次嘗試只會生效一次）
                if status in (StepStatus.FAILED, StepStatus.TIMEOUT):
                    ctx.capture_failure()
                ctx.failure_hook = None
                try:
                    adapter.cleanup(ctx, step.params)
                except Exception as e:
                    ctx.log(f"清理失敗：{e}")
                # 腳本被中止（取消、逾時、失敗、到達最長運行時間）時連遊戲本體一起關閉
                if reached or status != StepStatus.SUCCESS:
                    self._kill_game(ctx, adapter, step.params)
            ctx.log(f"步驟「{step.name}」{STATUS_TEXT[status]}：{message}")
            if status in (StepStatus.SUCCESS, StepStatus.CANCELLED):
                break
            if limit_at is not None and time.monotonic() >= limit_at:
                break  # 已用完最長運行時間，不再重試
            if attempt < max_attempts:
                try:
                    ctx.sleep(self.retry_delay)
                except Cancelled:
                    status, message = StepStatus.CANCELLED, "使用者取消"
                    break
        return StepResult(step=step.name, status=status, message=message, attempts=attempt,
                          started_at=started, finished_at=datetime.now())

    @staticmethod
    def _kill_game(ctx: RunContext, adapter, params: dict) -> None:
        try:
            names = list(adapter.merged(params).get("game_processes") or [])
        except Exception:
            return
        for name in names:
            try:
                n = process.kill_by_name(name)
            except Exception as e:
                ctx.log(f"無法關閉遊戲 {name}：{e}")
                continue
            if n:
                ctx.log(f"已關閉遊戲 {name}（{n} 個程序）")

    @staticmethod
    def _capture_failure(ctx: RunContext, name: str, attempt: int) -> None:
        try:
            directory = ctx.failure_dir
            directory.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")[:80] or "步驟"
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = directory / f"{stamp}_{safe}_第{attempt}次.png"
            if capture_screen(path):
                ctx.log(f"失敗截圖：{path}")
            else:
                ctx.log(f"失敗截圖未能儲存：{path}")
            images = sorted(directory.glob("*.png"), key=lambda p: (p.stat().st_mtime_ns, p.name))
            for old in images[:-50]:
                old.unlink()
        except Exception as exc:
            # 日誌回呼也可能拋例外，不能因此跳過 cleanup。
            try:
                ctx.log(f"失敗截圖處理失敗：{exc}")
            except Exception:
                pass
