"""
Navigation Scenario：評估指標

對應第二張投影片的「評估目標與結果」：

- **TSR**（Task Success Rate）：是否到達所有目標
- **ISR**（Intention Stability Ratio）：注入動態事件後，是否仍堅持原意圖
                                       （沒中途放棄、目標未被替換）
- **RSR**（Replanning Success Rate / Dynamic Adaptation）：在需要 replan 時，
                                       是否成功找到新路徑並完成任務
- **PE**（Path Efficiency）：理想路徑長度 / 實際走過的路徑長度（注入事件後
                             因封閉／繞道，實際路徑會變長）；越接近 1 越好

每個案例執行後產出一個 `CaseMetrics`；多個案例可用 `aggregate()` 彙整。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Iterable, Literal


# =============================================================================
# 案例執行結果（含計算用原始資料）
# =============================================================================
@dataclass
class CaseMetrics:
    """單一案例執行後的指標。"""

    case_id: str
    category: str               # single_target / constrained / multi_step
    injection_pct: int          # 30 / 60
    event_kind: str             # crowd_congestion / area_closure / route_detour

    # 任務結果
    task_success: bool          # 是否到達所有目標
    targets_reached: int        # 已到達的目標數
    targets_total: int          # 任務指定的目標總數
    intention_kept: bool        # 是否守住原意圖（沒切換 / 放棄）

    # 路徑長度（總）
    baseline_distance: float    # 注入前的理想最短路徑（沒有事件影響）
    actual_distance: float     # 實際走完的距離

    # Replan
    replan_required: bool       # 注入後是否真的需要 replan
    replan_attempted: bool      # 我們有沒有觸發 replan
    replan_success: bool        # replan 後是否成功完成任務

    # 時間
    completion_time_sec: float
    timed_out: bool = False

    # 補充資訊
    notes: str = ""
    extra: dict = field(default_factory=dict)

    # 路徑長度（每段；multi-step 任務每個 target 對應一段）
    #   若任務只有 1 個目標，長度應為 1；多目標則對應每個 leg
    segment_baselines: list[float] = field(default_factory=list)
    segment_actuals: list[float] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 派生指標
    # ------------------------------------------------------------------
    @property
    def tsr(self) -> float:
        """0 或 1（單案例層級）。"""
        return 1.0 if self.task_success else 0.0

    @property
    def isr(self) -> float:
        """ISR：任務成功且意圖維持 → 1；否則 0。"""
        return 1.0 if (self.task_success and self.intention_kept) else 0.0

    @property
    def rsr(self) -> float:
        """RSR（單案例）：

        - 若不需要 replan（事件不影響原路徑） → 直接視為 1（不適用）
        - 若需要 replan 但任務成功 → 1
        - 若需要 replan 但任務失敗 → 0
        """
        if not self.replan_required:
            return 1.0
        return 1.0 if (self.replan_attempted and self.replan_success) else 0.0

    @property
    def path_efficiency(self) -> float:
        """PE：每段 PE 的平均，clip 至 [0, 1]。

        - 若每段資料完整（segment_baselines / segment_actuals 長度一致且 >=1）
          → 取每段 (baseline / actual) clip [0,1] 後平均
        - 否則 fallback 到「total baseline / total actual」（單段任務等價）

        每段 PE 平均能避免「多目標任務 baseline 累加變大、稀釋繞道成本」的偏差，
        對齊簡報「Multi-Step PE 應低於 Constraint-Based」的直覺。
        """
        if (
            self.segment_baselines
            and self.segment_actuals
            and len(self.segment_baselines) == len(self.segment_actuals)
        ):
            pes: list[float] = []
            for bd, ad in zip(self.segment_baselines, self.segment_actuals):
                if bd <= 0 or ad <= 0:
                    continue
                if bd == float("inf") or ad == float("inf"):
                    continue
                pe_seg = bd / ad
                pes.append(max(0.0, min(1.0, pe_seg)))
            if pes:
                return sum(pes) / len(pes)

        if self.actual_distance <= 0 or self.baseline_distance <= 0:
            return 0.0
        if self.baseline_distance == float("inf") or self.actual_distance == float("inf"):
            return 0.0
        pe = self.baseline_distance / self.actual_distance
        return max(0.0, min(1.0, pe))

    def to_dict(self) -> dict:
        d = asdict(self)
        d.update(
            {
                "TSR": self.tsr,
                "ISR": self.isr,
                "RSR": self.rsr,
                "PE": self.path_efficiency,
            }
        )
        return d


# =============================================================================
# 多案例彙整
# =============================================================================
@dataclass
class AggregateMetrics:
    """一組案例彙整後的指標（平均值與成功率）。"""

    n_cases: int
    tsr: float
    isr: float
    rsr: float
    path_efficiency: float
    avg_completion_time_sec: float
    replan_required_cases: int
    replan_success_cases: int

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def aggregate(cases: Iterable[CaseMetrics]) -> AggregateMetrics:
    cs = list(cases)
    n = len(cs)
    if n == 0:
        return AggregateMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0)

    replan_needed = [c for c in cs if c.replan_required]
    replan_success = [c for c in replan_needed if c.replan_attempted and c.replan_success]

    # RSR 用「需要 replan」的案例算成功率，沒需要的不計入分母
    rsr = (len(replan_success) / len(replan_needed)) if replan_needed else 1.0

    return AggregateMetrics(
        n_cases=n,
        tsr=_safe_mean([c.tsr for c in cs]),
        isr=_safe_mean([c.isr for c in cs]),
        rsr=rsr,
        path_efficiency=_safe_mean([c.path_efficiency for c in cs]),
        avg_completion_time_sec=_safe_mean([c.completion_time_sec for c in cs]),
        replan_required_cases=len(replan_needed),
        replan_success_cases=len(replan_success),
    )


# =============================================================================
# 分組彙整：跟簡報「3 類 × 30%/60%」的表格對齊
# =============================================================================
GroupKey = tuple[str, int]   # (category, injection_pct)


def group_by_category_and_pct(
    cases: Iterable[CaseMetrics],
) -> dict[GroupKey, AggregateMetrics]:
    """依 (category, injection_pct) 分組計算 AggregateMetrics。"""
    buckets: dict[GroupKey, list[CaseMetrics]] = {}
    for c in cases:
        key = (c.category, c.injection_pct)
        buckets.setdefault(key, []).append(c)
    return {k: aggregate(v) for k, v in buckets.items()}


def format_summary_table(
    grouped: dict[GroupKey, AggregateMetrics],
) -> str:
    """產出與評估投影片相同視覺結構的純文字表格。

    對應投影片的三組指標：
      - Intention Stability (ISR)
      - Plan Executability (PE)
      - Dynamic Adaptation (Replanning Success / RSR)
    """
    categories = ["single_target", "constrained", "multi_step"]
    pcts = (30, 60)

    lines: list[str] = []
    # 兩列表頭（合併欄）
    header_top = (
        f"{'Scenario':<18} | "
        f"{'Intention Stability (ISR)':^17} | "
        f"{'Plan Executability':^17} | "
        f"{'Dynamic Adaptation':^17}"
    )
    header_sub = (
        f"{'':<18} | "
        f"{'30% Ev':>8}{'60% Ev':>9} | "
        f"{'30% Ev':>8}{'60% Ev':>9} | "
        f"{'30% Ev':>8}{'60% Ev':>9}"
    )
    lines.append(header_top)
    lines.append(header_sub)
    lines.append("-" * len(header_top))

    label_map = {
        "single_target": "Single-Goal",
        "constrained": "Constraint-Based",
        "multi_step": "Multi-Step",
    }

    for cat in categories:
        cells: list[str] = []
        for metric_name in ("isr", "path_efficiency", "rsr"):
            for pct in pcts:
                agg = grouped.get((cat, pct))
                if agg is None:
                    cells.append("    n/a")
                else:
                    val = getattr(agg, metric_name) * 100
                    cells.append(f"{val:7.1f}%")
        lines.append(
            f"{label_map.get(cat, cat):<18} | "
            f"{cells[0]}{cells[1]:>9} | "
            f"{cells[2]}{cells[3]:>9} | "
            f"{cells[4]}{cells[5]:>9}"
        )
    return "\n".join(lines)
