"""目标反推的服务编排层：请求映射 -> 校验 -> 求解模块 -> 序列化。

与前向 ``calculator`` 分家：这里不实现任何迭代，只做适配；迭代逻辑全部
位于 ``app.services.inverse_solver``，物理评估全部走前向内核
``app.core.engine.evaluate``。
"""

from __future__ import annotations

from typing import Any

from app.core.constants import (
    DEFAULT_INVERSE_MAX_SWEEPS,
    DEFAULT_INVERSE_TOLERANCE,
)
from app.services import validator
from app.services.inverse_solver import InverseProblem, InverseStageSpec, solve_inverse


def build_problem(raw_request: Any) -> InverseProblem:
    """把已通过校验的请求映射为求解模块的纯数据问题。"""
    raw_stages = validator._get(raw_request, "stages")  # noqa: SLF001
    specs = [
        InverseStageSpec(
            ve=float(validator._get(s, "ve")),  # noqa: SLF001
            structural_mass=float(validator._get(s, "structural_mass")),  # noqa: SLF001
            burn_time=float(validator._get(s, "burn_time")),  # noqa: SLF001
            propellant_min=float(validator._get(s, "propellant_min")),  # noqa: SLF001
            propellant_max=float(validator._get(s, "propellant_max")),  # noqa: SLF001
        )
        for s in raw_stages
    ]
    raw_tolerance = validator._get(raw_request, "tolerance")  # noqa: SLF001
    tolerance = (
        float(raw_tolerance) if raw_tolerance is not None else DEFAULT_INVERSE_TOLERANCE
    )
    raw_budget = validator._get(raw_request, "propellant_budget")  # noqa: SLF001
    budget = float(raw_budget) if raw_budget is not None else None
    return InverseProblem(
        target_net_delta_v=float(
            validator._get(raw_request, "target_net_delta_v")  # noqa: SLF001
        ),
        payload_mass=float(validator._get(raw_request, "payload_mass")),  # noqa: SLF001
        stages=specs,
        tolerance=tolerance,
        propellant_budget=budget,
        max_sweeps=DEFAULT_INVERSE_MAX_SWEEPS,
    )


def solve_target(raw_request: Any) -> dict[str, Any]:
    """HTTP 层入口：先校验（非法直接抛 RocketValidationError），再求解。"""
    validator.validate_inverse_request(raw_request)
    problem = build_problem(raw_request)
    return solve_inverse(problem).to_dict()
