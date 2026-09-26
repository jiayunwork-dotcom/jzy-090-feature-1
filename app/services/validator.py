"""输入合法性校验（独立于物理内核与 Web 框架）。

所有非法情形都以带明确原因的 RocketValidationError 返回，绝不允许继续
算出一个"看似正常的数"。规则包括：

* 级数不足一级；
* 有效排气速度 ve <= 0；
* 有效工作时间 burn_time <= 0；
* 结构/推进剂/有效载荷质量为负、出现非有限值（NaN/Inf）；
* 推进剂为零（此时 m0 == mf，物理上 Δv 必为 0，与"索要正速度增量"矛盾）；
* 燃料耗尽质量不小于点火质量（m0 必须严格大于 mf）。

校验对象是"鸭子类型"的映射/对象（Pydantic 模型或 dict 均可），从而
不与 FastAPI 产生耦合，测试可直接喂 dict。
"""

from __future__ import annotations

import math
from typing import Any

from app.core.errors import (
    EMPTY_BATCH,
    MF_NOT_LT_M0,
    NEGATIVE_PAYLOAD_MASS,
    NEGATIVE_PROPELLANT_MASS,
    NEGATIVE_STRUCTURAL_MASS,
    NON_FINITE_VALUE,
    NON_POSITIVE_BURN_TIME,
    NON_POSITIVE_EXHAUST_VELOCITY,
    NON_POSITIVE_MF,
    ZERO_PROPELLANT,
    RocketValidationError,
)
from app.core.losses import VALID_DRAG_MODES
from app.core.errors import BAD_DRAG_MODE, NON_POSITIVE_DRAG_VALUE


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _require_finite(value: Any, field_name: str, index: int | None = None) -> float:
    """数值字段必须存在、可转 float 且有限。"""
    if value is None:
        raise RocketValidationError(
            NON_FINITE_VALUE,
            f"字段 {field_name!r} 缺失，必须提供有限数值",
            stage_index=index,
            field=field_name,
        )
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise RocketValidationError(
            NON_FINITE_VALUE,
            f"字段 {field_name!r} 必须是数值，收到 {value!r}",
            stage_index=index,
            field=field_name,
        ) from None
    if not math.isfinite(number):
        raise RocketValidationError(
            NON_FINITE_VALUE,
            f"字段 {field_name!r} 必须为有限数值，不能是 NaN 或无穷大",
            stage_index=index,
            field=field_name,
        )
    return number


def validate_stage(raw_stage: Any, index: int) -> dict[str, float]:
    """校验单级，返回规整后的浮点字段。"""
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

    propellant = _require_finite(
        _get(raw_stage, "propellant_mass"), "propellant_mass", index
    )
    if propellant < 0:
        raise RocketValidationError(
            NEGATIVE_PROPELLANT_MASS,
            f"第 {index} 级推进剂质量不能为负，收到 {propellant}",
            stage_index=index,
            field="propellant_mass",
        )
    if propellant == 0:
        raise RocketValidationError(
            ZERO_PROPELLANT,
            f"第 {index} 级推进剂质量为 0 时点火质量等于耗尽质量，"
            "该级速度增量只能为 0；若要正的速度增量请加注推进剂",
            stage_index=index,
            field="propellant_mass",
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

    return {
        "ve": ve,
        "structural_mass": structural,
        "propellant_mass": propellant,
        "burn_time": burn_time,
    }


def validate_drag(raw_config: Any) -> None:
    """校验可选阻力规格。"""
    raw_drag = _get(raw_config, "drag")
    if raw_drag is None:
        return
    mode = _get(raw_drag, "mode") or "none"
    if mode not in VALID_DRAG_MODES:
        raise RocketValidationError(
            BAD_DRAG_MODE,
            f"未知的阻力模式 {mode!r}，可选：{', '.join(VALID_DRAG_MODES)}",
            field="drag.mode",
        )
    if mode == "explicit":
        value = _require_finite(_get(raw_drag, "value"), "drag.value")
        if value < 0:
            raise RocketValidationError(
                NON_POSITIVE_DRAG_VALUE,
                "阻力显式扣减取值不能为负（阻力只能被扣除）",
                field="drag.value",
            )
    if mode == "linear":
        fraction = _require_finite(_get(raw_drag, "fraction"), "drag.fraction")
        if fraction < 0:
            raise RocketValidationError(
                NON_POSITIVE_DRAG_VALUE,
                "阻力线性比例系数不能为负（阻力只能被扣除）",
                field="drag.fraction",
            )


def validate_configuration(raw_config: Any) -> None:
    """校验一支构型（单次核算与批量中的每一项共用此入口）。"""
    raw_stages = _get(raw_config, "stages")
    if not raw_stages:
        raise RocketValidationError(
            "NO_STAGES",
            "至少需要一级火箭：stages 为空或缺失，无法核算速度增量",
            field="stages",
        )

    stages = [validate_stage(s, i) for i, s in enumerate(raw_stages)]

    payload = _require_finite(
        _get(raw_config, "payload_mass"), "payload_mass"
    )
    if payload < 0:
        raise RocketValidationError(
            NEGATIVE_PAYLOAD_MASS,
            f"有效载荷质量不能为负，收到 {payload}",
            field="payload_mass",
        )

    validate_drag(raw_config)

    # 复用质量链定义，自顶向下检查 m0 > mf 且 mf > 0。
    upper_mass = payload
    for i in range(len(stages) - 1, -1, -1):
        s = stages[i]
        mf = s["structural_mass"] + upper_mass
        m0 = mf + s["propellant_mass"]
        if mf <= 0:
            raise RocketValidationError(
                NON_POSITIVE_MF,
                f"第 {i} 级燃料耗尽质量必须为正（结构质量加上方质量），得到 {mf}",
                stage_index=i,
            )
        if mf >= m0:
            # 正常校验已拦截 propellant==0；此分支为防御性兜底
            raise RocketValidationError(
                MF_NOT_LT_M0,
                f"第 {i} 级燃料耗尽质量 {mf} 不小于点火质量 {m0}，"
                "无法产生正的质量比（推进剂质量必须为正）",
                stage_index=i,
            )
        upper_mass = mf


def validate_batch(raw_configs: Any) -> None:
    """批量比选：构型列表不能为空；各构型再独立校验。"""
    if not raw_configs:
        raise RocketValidationError(
            EMPTY_BATCH,
            "批量比选至少需要一支候选构型：configurations 为空",
            field="configurations",
        )
