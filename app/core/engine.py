"""核算引擎：把质量链、火箭方程、损失扣减编排为一次完整构型核算。

引擎使用自身的纯数据类型（StageInput / DragSpec），不依赖 Web 层的
Pydantic 模型，因此既能被 FastAPI 调用，也能被测试、批量调度与算例
直接复用。所有输入应已通过 ``app.services.validator`` 的合法性校验。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .losses import (
    DragSpec,
    drag_loss,
    net_delta_v,
    stage_gravity_loss,
    total_gravity_loss,
)
from .physics import ideal_stage_delta_v
from .staging import StageMass, build_mass_chain, liftoff_mass


@dataclass(frozen=True)
class StageInput:
    """单级输入（自下而上排列）。"""

    ve: float                # 有效排气速度 m/s
    structural_mass: float   # 结构质量 kg
    propellant_mass: float   # 推进剂质量 kg
    burn_time: float         # 有效工作时间 s


@dataclass(frozen=True)
class StageResult:
    """单级结果：逐级质量比与逐级速度增量、逐级重力损失。"""

    index: int
    ve: float
    m0: float
    mf: float
    upper_mass: float
    mass_ratio: float
    ideal_delta_v: float
    gravity_loss: float


@dataclass(frozen=True)
class ConfigurationResult:
    """单支构型的完整核算结论。"""

    stage_count: int
    stages: list[StageResult]
    payload_mass: float
    liftoff_mass: float
    overall_mass_ratio: float | None   # 起飞质量 / 最终净载荷（仅展示，不参与求对数）；载荷为 0 时未定义
    ideal_total_delta_v: float
    gravity_loss_total: float
    drag_loss_total: float
    net_delta_v: float
    drag_mode: str


def evaluate(
    stages: list[StageInput],
    payload_mass: float,
    drag: DragSpec | None = None,
) -> ConfigurationResult:
    """核算一支多级构型。

    多级总理想速度增量是各级理想值逐级相加（fsum 提升数值稳定性），
    绝不拿整箭起飞质量除以最终净载荷做一次对数。
    """
    if drag is None:
        drag = DragSpec()
    chain: list[StageMass] = build_mass_chain(
        structural_masses=[s.structural_mass for s in stages],
        propellant_masses=[s.propellant_mass for s in stages],
        payload_mass=payload_mass,
    )

    stage_results: list[StageResult] = []
    ideal_values: list[float] = []
    for stage_input, masses in zip(stages, chain):
        dv = ideal_stage_delta_v(stage_input.ve, masses.m0, masses.mf)
        g_loss = stage_gravity_loss(stage_input.burn_time)
        ideal_values.append(dv)
        stage_results.append(
            StageResult(
                index=masses.index,
                ve=stage_input.ve,
                m0=masses.m0,
                mf=masses.mf,
                upper_mass=masses.upper_mass,
                mass_ratio=masses.mass_ratio,
                ideal_delta_v=dv,
                gravity_loss=g_loss,
            )
        )

    ideal_total = math.fsum(ideal_values)
    gravity_total = total_gravity_loss([s.burn_time for s in stages])
    drag_total = drag_loss(ideal_total, drag)
    net = net_delta_v(ideal_total, gravity_total, drag_total)

    m_liftoff = liftoff_mass(chain)
    # 展示用整体质量比；它不会被用来做单次对数。载荷为 0 时该比值未定义。
    overall_ratio = m_liftoff / payload_mass if payload_mass > 0 else None
    return ConfigurationResult(
        stage_count=len(stages),
        stages=stage_results,
        payload_mass=payload_mass,
        liftoff_mass=m_liftoff,
        overall_mass_ratio=overall_ratio,
        ideal_total_delta_v=ideal_total,
        gravity_loss_total=gravity_total,
        drag_loss_total=drag_total,
        net_delta_v=net,
        drag_mode=drag.mode,
    )
