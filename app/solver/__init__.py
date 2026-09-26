"""反解求解模块：给定目标净速度增量，反推各级推进剂加注量。

与前向核算内核、批量比选、校验各自分家；依赖方向严格单向：
本模块只调用 ``app.core.engine.evaluate`` 评估试探构型，前向链路不
反向依赖本模块。数值迭代只用标准库 math。
"""

from app.solver.inverse import (
    BINDING_BUDGET,
    BINDING_BUDGET_AND_STAGE_MAX,
    BINDING_DIMINISHING,
    BINDING_STAGE_MAX,
    BINDING_STAGE_MIN,
    DEFAULT_TOLERANCE,
    REASON_BUDGET_BELOW_MINIMUM,
    REASON_TARGET_OVERSHOT_AT_MINIMUM,
    REASON_TARGET_UNREACHABLE,
    STATUS_INFEASIBLE,
    STATUS_SOLVED,
    InverseInfeasible,
    InverseProblem,
    InverseSolution,
    InverseStageSpec,
    solve_inverse,
)

__all__ = [
    "BINDING_BUDGET",
    "BINDING_BUDGET_AND_STAGE_MAX",
    "BINDING_DIMINISHING",
    "BINDING_STAGE_MAX",
    "BINDING_STAGE_MIN",
    "DEFAULT_TOLERANCE",
    "REASON_BUDGET_BELOW_MINIMUM",
    "REASON_TARGET_OVERSHOT_AT_MINIMUM",
    "REASON_TARGET_UNREACHABLE",
    "STATUS_INFEASIBLE",
    "STATUS_SOLVED",
    "InverseInfeasible",
    "InverseProblem",
    "InverseSolution",
    "InverseStageSpec",
    "solve_inverse",
]
