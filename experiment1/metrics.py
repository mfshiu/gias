"""
Experiment 1 metrics

主要四項指標（與 §5.3.5 對齊）：
  - TSR : Task Success Rate
          任務最終是否抵達所有目標 zone
          TSR = #success / #total
  - PE  : Plan Executability
          plan 是否「可執行」(plan 不是 leaf_unresolved；
          atomic actions 全在 allowed 集合；參數可填)
          PE = #plan_executable / #total
  - ISR : Intention Stability Rate
          多步驟意圖中，所有子目標是否都被「拜訪到」(順序也納入加權)
          ISR = #intention_satisfied / #total
  - RSR : Replanning Success Rate
          當任務執行中發生中斷事件 (擁擠/封閉/繞路) 而觸發 replan 時，
          replan 是否成功(找到新可達路徑)
          RSR = #replan_success / #replan_triggered  (no replan triggered → N/A)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class CaseResult:
    case_id: str
    group: str
    intention: str
    ratio: float
    success: bool = False
    plan_executable: bool = False
    intention_satisfied: bool = False
    replan_triggered: int = 0
    replan_success: int = 0
    constraint_satisfied: bool = True
    completion_time_sec: float = 0.0
    path_length_m: float = 0.0
    visited_zones: list[str] = field(default_factory=list)
    plan_atomic_actions: list[str] = field(default_factory=list)
    initial_events: list[dict] = field(default_factory=list)
    midway_events: list[dict] = field(default_factory=list)
    plan_unresolved_reason: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "group": self.group,
            "intention": self.intention,
            "ratio": self.ratio,
            "success": self.success,
            "plan_executable": self.plan_executable,
            "intention_satisfied": self.intention_satisfied,
            "replan_triggered": self.replan_triggered,
            "replan_success": self.replan_success,
            "constraint_satisfied": self.constraint_satisfied,
            "completion_time_sec": round(self.completion_time_sec, 2),
            "path_length_m": round(self.path_length_m, 2),
            "visited_zones": self.visited_zones,
            "plan_atomic_actions": self.plan_atomic_actions,
            "initial_events": self.initial_events,
            "midway_events": self.midway_events,
            "plan_unresolved_reason": self.plan_unresolved_reason,
            "error": self.error,
        }


@dataclass
class Aggregate:
    group: str
    ratio: float
    n: int
    tsr: float
    pe: float
    isr: float
    rsr: float | None
    constraint_rate: float
    avg_path_length: float
    avg_completion_time: float

    def to_dict(self) -> dict:
        return {
            "group": self.group,
            "ratio": self.ratio,
            "n": self.n,
            "TSR": round(self.tsr, 4),
            "PE": round(self.pe, 4),
            "ISR": round(self.isr, 4),
            "RSR": (round(self.rsr, 4) if self.rsr is not None else None),
            "constraint_rate": round(self.constraint_rate, 4),
            "avg_path_length_m": round(self.avg_path_length, 2),
            "avg_completion_time_sec": round(self.avg_completion_time, 2),
        }


def aggregate(results: Iterable[CaseResult]) -> list[Aggregate]:
    """
    依 (group, ratio) 分桶彙總。
    """
    buckets: dict[tuple[str, float], list[CaseResult]] = {}
    for r in results:
        buckets.setdefault((r.group, r.ratio), []).append(r)

    out: list[Aggregate] = []
    for (group, ratio), rs in sorted(buckets.items()):
        n = len(rs)
        if n == 0:
            continue
        tsr = sum(1 for r in rs if r.success) / n
        pe = sum(1 for r in rs if r.plan_executable) / n
        isr = sum(1 for r in rs if r.intention_satisfied) / n
        triggered = sum(r.replan_triggered for r in rs)
        succeeded = sum(r.replan_success for r in rs)
        rsr = (succeeded / triggered) if triggered > 0 else None
        constraint_rate = sum(1 for r in rs if r.constraint_satisfied) / n
        avg_path = sum(r.path_length_m for r in rs) / n
        avg_time = sum(r.completion_time_sec for r in rs) / n
        out.append(
            Aggregate(
                group=group,
                ratio=ratio,
                n=n,
                tsr=tsr,
                pe=pe,
                isr=isr,
                rsr=rsr,
                constraint_rate=constraint_rate,
                avg_path_length=avg_path,
                avg_completion_time=avg_time,
            )
        )
    return out
