"""损失扣减：重力损失与可选的大气阻力损失。

重力损失按竖直起飞的简化模型取 Δv_g = g · t_burn，各级分别计算后求和，
只允许从理想速度增量中"扣除"，不可能被加成更大。

大气阻力只做轻量近似（不引入任何数值框架），支持三种模式：
    none    —— 不扣减
    explicit—— 直接给定要扣除的阻力损失（m/s，非负）
    linear  —— 按理想速度增量的比例系数做线性近似（系数非负）
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .constants import STANDARD_GRAVITY
from .errors import (
    BAD_DRAG_MODE,
    NON_POSITIVE_DRAG_VALUE,
    RocketValidationError,
)

DRAG_NONE = "none"
DRAG_EXPLICIT = "explicit"
DRAG_LINEAR = "linear"
VALID_DRAG_MODES = (DRAG_NONE, DRAG_EXPLICIT, DRAG_LINEAR)


@dataclass(frozen=True)
class DragSpec:
    """阻力扣减规格。"""

    mode: str = DRAG_NONE
    value: float = 0.0          # explicit 模式下：直接扣除的 m/s 数
    fraction: float = 0.0       # linear 模式下：占理想 Δv 的比例


def stage_gravity_loss(burn_time: float, g: float = STANDARD_GRAVITY) -> float:
    """单级重力损失 g·t_burn（恒为非负）。"""
    return g * burn_time


def total_gravity_loss(
    burn_times: list[float], g: float = STANDARD_GRAVITY
) -> float:
    """各级重力损失求和。"""
    return math.fsum(g * t for t in burn_times)


def drag_loss(ideal_total: float, spec: DragSpec) -> float:
    """按规格计算总阻力损失，恒为非负。"""
    if spec.mode == DRAG_NONE:
        return 0.0
    if spec.mode == DRAG_EXPLICIT:
        if spec.value < 0:
            raise RocketValidationError(
                NON_POSITIVE_DRAG_VALUE,
                "阻力损失显式取值不能为负（阻力只能被扣除，不能反向增大净值）",
                field="drag.value",
            )
        return spec.value
    if spec.mode == DRAG_LINEAR:
        if spec.fraction < 0:
            raise RocketValidationError(
                NON_POSITIVE_DRAG_VALUE,
                "阻力线性比例系数不能为负（阻力只能被扣除，不能反向增大净值）",
                field="drag.fraction",
            )
        return ideal_total * spec.fraction
    raise RocketValidationError(
        BAD_DRAG_MODE,
        f"未知的阻力模式 {spec.mode!r}，可选：{', '.join(VALID_DRAG_MODES)}",
        field="drag.mode",
    )


def net_delta_v(
    ideal_total: float,
    gravity: float,
    drag: float,
) -> float:
    """净速度增量 = 理想值 − 重力损失 − 可选阻力。

    重力/阻力损失均非负，因此净值绝不大于理想值（不存在被加成更大的路径）。
    """
    return ideal_total - gravity - drag
