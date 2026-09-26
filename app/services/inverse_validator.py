"""反解（给定目标净 Δv 反推各级推进剂加注量）的输入合法性校验。

与前向核算的 ``validator`` 分家：前向校验"构型完整给定"，这里校验
"目标 + 各级确定量 + 加注上下限 + 可选预算"。所有非法情形都在真正
开算之前以带明确原因的 RocketValidationError 抛出，沿用统一的
{"error": {"code", "reason", "stage_index", "field"}} 错误信封。

校验对象同样是鸭子类型的映射/对象（Pydantic 模型或 dict 均可）。
"""

from __future__ import annotations

from typing import Any

from app.core.errors import (
    NEGATIVE_PAYLOAD_MASS,
    NEGATIVE_PROPELLANT_BUDGET,
    NEGATIVE_PROPELLANT_LIMIT,
    NEGATIVE_STRUCTURAL_MASS,
    NEGATIVE_TARGET_DELTA_V,
    NON_FINITE_VALUE,
    NON_POSITIVE_BURN_TIME,
    NON_POSITIVE_EXHAUST_VELOCITY,
    NON_POSITIVE_MF,
    NON_POSITIVE_TOLERANCE,
    PROPELLANT_LIMIT_REVERSED,
    RocketValidationError,
)
from app.services.validator import _get, _require_finite, validate_drag


def validate_inverse_stage(raw_stage: Any, index: int) -> dict[str, float]:
    """校验反解的单级参数：确定量（ve/结构/工作时间）+ 加注上下限。"""
    ve = _require_finite(_get(raw_stage, "ve"), "ve", index)
    if ve <= 0:
        raise RocketValidationError(
            NON_POSITIVE_EXHAUST_VELOCITY,
            f"第 {index} 级有效排气速度 ve 必须严格大于 0，收到 {ve}",
            stage_index=index,
            field="ve",
        )

    structural = _require_finite(
        _get(raw_stage, "structural_mass"), "structural_mass", index
    )
    if structural < 0:
        raise RocketValidationError(
            NEGATIVE_STRUCTURAL_MASS,
            f"第 {index} 级结构质量不能为负，收到 {structural}",
            stage_index=index,
            field="structural_mass",
        )

    burn_time = _require_finite(
        _get(raw_stage, "burn_time"), "burn_time", index
    )
    if burn_time <= 0:
        raise RocketValidationError(
            NON_POSITIVE_BURN_TIME,
            f"第 {index} 级有效工作时间 burn_time 必须严格大于 0，收到 {burn_time}",
            stage_index=index,
            field="burn_time",
        )

    prop_min_raw = _get(raw_stage, "propellant_min")
    # 与接口模型 InverseStageRequest 的缺省一致：缺省加注下限为 0
    prop_min = (
        0.0
        if prop_min_raw is None
        else _require_finite(prop_min_raw, "propellant_min", index)
    )
    if prop_min < 0:
        raise RocketValidationError(
            NEGATIVE_PROPELLANT_LIMIT,
            f"第 {index} 级推进剂加注下限不能为负，收到 {prop_min}",
            stage_index=index,
            field="propellant_min",
        )

    prop_max = _require_finite(
        _get(raw_stage, "propellant_max"), "propellant_max", index
    )
    if prop_max < 0:
        raise RocketValidationError(
            NEGATIVE_PROPELLANT_LIMIT,
            f"第 {index} 级推进剂加注上限不能为负，收到 {prop_max}",
            stage_index=index,
            field="propellant_max",
        )

    if prop_min > prop_max:
        raise RocketValidationError(
            PROPELLANT_LIMIT_REVERSED,
            f"第 {index} 级推进剂加注下限 {prop_min} 高于上限 {prop_max}，"
            "不存在任何合法加注量",
            stage_index=index,
            field="propellant_min",
        )

    return {
        "ve": ve,
        "structural_mass": structural,
        "burn_time": burn_time,
        "propellant_min": prop_min,
        "propellant_max": prop_max,
    }


def validate_inverse_request(raw_request: Any) -> dict[str, Any]:
    """校验一次反解请求，返回规整后的浮点字段（含阻力规格原样透传）。

    规则：
    * 至少一级；
    * 目标净速度增量必须 >= 0（任务书要的是"至少达到某个数"，负目标无意义）；
    * 有效载荷 >= 0；
    * 每级 ve > 0、burn_time > 0、结构质量 >= 0、0 <= 加注下限 <= 加注上限；
    * 可选推进剂总预算 >= 0 且有限；
    * 可选容差 > 0 且有限；
    * 阻力规格与前向核算同一套规则。
    """
    raw_stages = _get(raw_request, "stages")
    if not raw_stages:
        raise RocketValidationError(
            "NO_STAGES",
            "至少需要一级火箭：stages 为空或缺失，无法反解推进剂分配",
            field="stages",
        )

    target = _require_finite(
        _get(raw_request, "target_delta_v"), "target_delta_v"
    )
    if target < 0:
        raise RocketValidationError(
            NEGATIVE_TARGET_DELTA_V,
            f"目标净速度增量不能为负，收到 {target}",
            field="target_delta_v",
        )

    payload = _require_finite(
        _get(raw_request, "payload_mass"), "payload_mass"
    )
    if payload < 0:
        raise RocketValidationError(
            NEGATIVE_PAYLOAD_MASS,
            f"有效载荷质量不能为负，收到 {payload}",
            field="payload_mass",
        )

    stages = [validate_inverse_stage(s, i) for i, s in enumerate(raw_stages)]

    # 与前向校验同一口径：各级耗尽质量（结构 + 上方质量 + 载荷）必须为正，
    # 否则质量比未定义（该量与推进剂加注量无关，开算前即可判定）
    upper_mass = payload
    for i in range(len(stages) - 1, -1, -1):
        mf = stages[i]["structural_mass"] + upper_mass
        if mf <= 0:
            raise RocketValidationError(
                NON_POSITIVE_MF,
                f"第 {i} 级燃料耗尽质量必须为正（结构质量加上方质量），得到 {mf}",
                stage_index=i,
            )
        upper_mass = mf

    budget_raw = _get(raw_request, "propellant_budget")
    budget: float | None = None
    if budget_raw is not None:
        budget = _require_finite(budget_raw, "propellant_budget")
        if budget < 0:
            raise RocketValidationError(
                NEGATIVE_PROPELLANT_BUDGET,
                f"推进剂总预算不能为负，收到 {budget}",
                field="propellant_budget",
            )

    tolerance_raw = _get(raw_request, "tolerance")
    tolerance: float | None = None
    if tolerance_raw is not None:
        tolerance = _require_finite(tolerance_raw, "tolerance")
        if tolerance <= 0:
            raise RocketValidationError(
                NON_POSITIVE_TOLERANCE,
                f"容差必须严格大于 0（容差带宽度为零无法判定收敛），收到 {tolerance}",
                field="tolerance",
            )

    # 阻力规格沿用前向核算的同一套校验（可选；缺省不扣减）
    validate_drag(raw_request)

    return {
        "stages": stages,
        "target_delta_v": target,
        "payload_mass": payload,
        "propellant_budget": budget,
        "tolerance": tolerance,
    }
