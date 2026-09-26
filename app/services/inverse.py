"""反解调度层：校验 → 组装求解问题 → 求解 → 序列化三种结局。

与前向核算调度（calculator）分家；依赖方向：
api → services.inverse → solver.inverse → core.engine。
前向链路不反向依赖求解器。
"""

from __future__ import annotations

import math
from typing import Any

from app.core.losses import DragSpec
from app.services import inverse_validator
from app.services.calculator import _to_drag_spec  # 复用前向同一套阻力装配
from app.solver.inverse import (
    DEFAULT_TOLERANCE,
    InverseInfeasible,
    InverseProblem,
    InverseSolution,
    InverseStageSpec,
    solve_inverse,
)


def _build_problem(data: dict[str, Any], raw_request: Any) -> InverseProblem:
    tolerance = data["tolerance"]
    return InverseProblem(
        target_delta_v=float(data["target_delta_v"]),
        payload_mass=float(data["payload_mass"]),
        stages=[
            InverseStageSpec(
                ve=float(s["ve"]),
                structural_mass=float(s["structural_mass"]),
                burn_time=float(s["burn_time"]),
                propellant_min=float(s["propellant_min"]),
                propellant_max=float(s["propellant_max"]),
            )
            for s in data["stages"]
        ],
        propellant_budget=(
            float(data["propellant_budget"])
            if data["propellant_budget"] is not None
            else None
        ),
        tolerance=float(tolerance) if tolerance is not None else DEFAULT_TOLERANCE,
        drag=_to_drag_spec(raw_request),
    )


def solve_inverse_request(raw_request: Any) -> dict[str, Any]:
    """处理一次反解请求（鸭子类型的 Pydantic 模型或 dict 均可）。

    输入非法时由 inverse_validator 抛 RocketValidationError（422 信封）；
    合法时无论是否可行都返回 200 + status 字段区分三种结局。
    """
    data = inverse_validator.validate_inverse_request(raw_request)
    problem = _build_problem(data, raw_request)
    outcome = solve_inverse(problem)
    if isinstance(outcome, InverseSolution):
        return _serialize_solved(problem, outcome)
    return _serialize_infeasible(problem, outcome)


def _serialize_solved(
    problem: InverseProblem, outcome: InverseSolution
) -> dict[str, Any]:
    r = outcome.evaluation
    achieved = r.net_delta_v
    return {
        "status": "solved",
        "target_delta_v": problem.target_delta_v,
        "payload_mass": problem.payload_mass,
        "propellant_budget": problem.propellant_budget,
        "tolerance": problem.tolerance,
        "stages": [
            {
                "index": s.index,
                "ve": s.ve,
                "structural_mass": problem.stages[s.index].structural_mass,
                "burn_time": problem.stages[s.index].burn_time,
                "propellant_min": problem.stages[s.index].propellant_min,
                "propellant_max": problem.stages[s.index].propellant_max,
                "propellant_mass": outcome.propellant_masses[s.index],
                "m0": s.m0,
                "mf": s.mf,
                "upper_mass": s.upper_mass,
                "mass_ratio": s.mass_ratio,
                "ideal_delta_v": s.ideal_delta_v,
                "gravity_loss": s.gravity_loss,
            }
            for s in r.stages
        ],
        "total_propellant_mass": math.fsum(outcome.propellant_masses),
        "liftoff_mass": r.liftoff_mass,
        "overall_mass_ratio": r.overall_mass_ratio,
        "ideal_total_delta_v": r.ideal_total_delta_v,
        "gravity_loss_total": r.gravity_loss_total,
        "drag_loss_total": r.drag_loss_total,
        "drag_mode": r.drag_mode,
        "achieved_net_delta_v": achieved,
        "deviation": achieved - problem.target_delta_v,
        "iterations": outcome.iterations,
        # 不可行结局字段在可行响应中显式为空
        "reason_code": None,
        "binding_constraint": None,
        "binding_stage_indices": None,
        "max_achievable_net_delta_v": None,
        "min_achievable_net_delta_v": None,
        "detail": None,
    }


def _serialize_infeasible(
    problem: InverseProblem, outcome: InverseInfeasible
) -> dict[str, Any]:
    return {
        "status": "infeasible",
        "target_delta_v": problem.target_delta_v,
        "payload_mass": problem.payload_mass,
        "propellant_budget": problem.propellant_budget,
        "tolerance": problem.tolerance,
        "stages": None,
        "total_propellant_mass": None,
        "liftoff_mass": None,
        "overall_mass_ratio": None,
        "ideal_total_delta_v": None,
        "gravity_loss_total": None,
        "drag_loss_total": None,
        "drag_mode": problem.drag.mode,
        "achieved_net_delta_v": None,
        "deviation": None,
        "iterations": outcome.iterations,
        "reason_code": outcome.reason_code,
        "binding_constraint": outcome.binding_constraint,
        "binding_stage_indices": outcome.binding_stage_indices,
        "max_achievable_net_delta_v": outcome.max_achievable_net_delta_v,
        "min_achievable_net_delta_v": outcome.min_achievable_net_delta_v,
        "detail": outcome.detail,
    }
