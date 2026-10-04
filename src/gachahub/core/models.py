"""任務鏈與執行結果的資料模型。所有設定以 UTF-8 YAML 儲存。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FailPolicy(str, Enum):
    RETRY = "retry"  # 重試 retries 次，仍失敗則視為 skip
    SKIP = "skip"  # 記錄失敗，繼續下一步
    ABORT = "abort"  # 中止整條任務鏈（後置鉤子仍會執行）


class StepStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class HookSpec(BaseModel):
    """前置／後置鉤子設定。type 對應 hooks 登錄表中的名稱。"""

    type: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class TaskStep(BaseModel):
    name: str
    adapter: str  # 適配器 id，例如 "generic" 或 YAML 宣告的 "ok-nte"
    params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    timeout: float = 3600.0  # 秒；任何等待外部程序都必須有上限，超過算失敗
    max_duration: float = 0.0  # 秒；最長運行時間，到點直接結束程序並視為完成；0 = 不啟用（預設）
    on_fail: FailPolicy = FailPolicy.SKIP
    retries: int = 1


class TaskChain(BaseModel):
    name: str
    steps: list[TaskStep] = Field(default_factory=list)
    # 前置鉤子依序套用；後置階段依反序還原，再執行 post_actions
    pre_hooks: list[HookSpec] = Field(default_factory=list)
    post_actions: list[HookSpec] = Field(default_factory=list)


class StepResult(BaseModel):
    step: str
    status: StepStatus
    message: str = ""
    attempts: int = 1
    started_at: datetime
    finished_at: datetime


class ChainReport(BaseModel):
    chain: str
    started_at: datetime
    finished_at: datetime | None = None
    results: list[StepResult] = Field(default_factory=list)
    aborted: bool = False
    cancelled: bool = False

    @property
    def ok(self) -> bool:
        return not self.aborted and not self.cancelled and all(
            r.status in (StepStatus.SUCCESS, StepStatus.SKIPPED) for r in self.results
        )
