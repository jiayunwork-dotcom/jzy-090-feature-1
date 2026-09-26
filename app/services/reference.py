"""内置参考算例：两级构型 vs 同等结构比的假想单级。

算例刻意采用"每级相同结构比 ε + 相同排气速度 ve"的干净设定，使
"多级拆分级优于把全部推进剂塞进单级"成为可手工复核的严格结论：

* 两级：每级 ms = 2000 kg、mp = 8000 kg（ε = ms/(ms+mp) = 0.2），
  ve = 3200 m/s；有效载荷 2000 kg；burn 120 s / 80 s。
* 单级对照：推进剂与结构质量为两级之和（ms=4000, mp=16000），
  结构比同样为 0.2，ve 取两级的推进剂加权平均（此处两者相等，仍为 3200），
  工作时间取两级之和，有效载荷同为 2000 kg。

服务启动时打印这份算例的结果，供推进分析组手工对照；也可通过
GET /api/v1/reference/example 拉取。
"""

from __future__ import annotations

from app.core.engine import (
    ConfigurationResult,
    StageInput,
    evaluate,
)
from app.core.losses import DragSpec

PAYLOAD_MASS = 2000.0

# 自下而上：index 0 为第一级（先点火），index 1 为上面级
TWO_STAGE_STAGES = [
    StageInput(ve=3200.0, structural_mass=2000.0, propellant_mass=8000.0, burn_time=120.0),
    StageInput(ve=3200.0, structural_mass=2000.0, propellant_mass=8000.0, burn_time=80.0),
]


def collapse_to_single_stage(multi_stages: list[StageInput]) -> StageInput:
    """把多级构型折叠成同等结构比的假想单级。

    结构质量、推进剂质量、工作时间取各级之和；排气速度取推进剂
    加权平均，保证对照方案在 ve 上不被人为调低。
    """
    structural = sum(s.structural_mass for s in multi_stages)
    propellant = sum(s.propellant_mass for s in multi_stages)
    burn_time = sum(s.burn_time for s in multi_stages)
    ve_weighted = (
        sum(s.ve * s.propellant_mass for s in multi_stages) / propellant
    )
    return StageInput(
        ve=ve_weighted,
        structural_mass=structural,
        propellant_mass=propellant,
        burn_time=burn_time,
    )


SINGLE_STAGE_STAGE = collapse_to_single_stage(TWO_STAGE_STAGES)


def run_reference() -> dict[str, object]:
    """计算两级与单级对照，返回可序列化结果与关键指标。"""
    two_stage: ConfigurationResult = evaluate(
        list(TWO_STAGE_STAGES), PAYLOAD_MASS, DragSpec()
    )
    single_stage: ConfigurationResult = evaluate(
        [SINGLE_STAGE_STAGE], PAYLOAD_MASS, DragSpec()
    )
    return {
        "description": (
            "内置算例：相同结构比 ε=0.2、相同 ve=3200 m/s、相同有效载荷下，"
            "两级构型的理想与净速度增量均应明显优于把全部推进剂塞进单级。"
        ),
        "payload_mass_kg": PAYLOAD_MASS,
        "two_stage": _serialize_result(two_stage),
        "single_stage_comparator": _serialize_result(single_stage),
        "ideal_advantage_mps": (
            two_stage.ideal_total_delta_v - single_stage.ideal_total_delta_v
        ),
        "net_advantage_mps": (
            two_stage.net_delta_v - single_stage.net_delta_v
        ),
        "staging_better_on_ideal": (
            two_stage.ideal_total_delta_v > single_stage.ideal_total_delta_v
        ),
        "staging_better_on_net": (
            two_stage.net_delta_v > single_stage.net_delta_v
        ),
    }


def _serialize_result(result: ConfigurationResult) -> dict[str, object]:
    return {
        "stage_count": result.stage_count,
        "liftoff_mass_kg": result.liftoff_mass,
        "overall_mass_ratio": result.overall_mass_ratio,
        "stages": [
            {
                "index": s.index,
                "ve_mps": s.ve,
                "m0_kg": s.m0,
                "mf_kg": s.mf,
                "upper_mass_kg": s.upper_mass,
                "mass_ratio": s.mass_ratio,
                "ideal_delta_v_mps": s.ideal_delta_v,
                "gravity_loss_mps": s.gravity_loss,
            }
            for s in result.stages
        ],
        "ideal_total_delta_v_mps": result.ideal_total_delta_v,
        "gravity_loss_total_mps": result.gravity_loss_total,
        "drag_loss_total_mps": result.drag_loss_total,
        "net_delta_v_mps": result.net_delta_v,
    }


def format_reference_report(reference: dict[str, object]) -> str:
    """把算例结果渲染成启动时打印的纯文本报告。"""
    two = reference["two_stage"]  # type: ignore[assignment]
    one = reference["single_stage_comparator"]  # type: ignore[assignment]
    lines = [
        "=" * 68,
        "多级火箭 Δv 核算引擎 —— 内置参考算例（启动自检）",
        f"有效载荷: {PAYLOAD_MASS:.0f} kg",
        "-" * 68,
        "【两级构型】(ms=2000, mp=8000, ε=0.2) ×2, ve=3200 m/s",
    ]
    for s in two["stages"]:  # type: ignore[index]
        lines.append(
            f"  第{s['index']}级: m0={s['m0_kg']:.0f} kg, mf={s['mf_kg']:.0f} kg, "
            f"质量比={s['mass_ratio']:.4f}, 理想Δv={s['ideal_delta_v_mps']:.2f} m/s"
        )
    lines += [
        f"  理想总Δv = {two['ideal_total_delta_v_mps']:.2f} m/s",
        f"  重力损失 = {two['gravity_loss_total_mps']:.2f} m/s",
        f"  净  Δv  = {two['net_delta_v_mps']:.2f} m/s",
        "-" * 68,
        "【单级对照】ms=4000, mp=16000, ε=0.2, ve=3200 m/s",
        f"  理想Δv = {one['ideal_total_delta_v_mps']:.2f} m/s, "
        f"净Δv = {one['net_delta_v_mps']:.2f} m/s",
        "-" * 68,
        f"分级理想Δv 优势: +{reference['ideal_advantage_mps']:.2f} m/s "
        f"(staging_better={reference['staging_better_on_ideal']})",
        f"分级净  Δv 优势: +{reference['net_advantage_mps']:.2f} m/s "
        f"(staging_better={reference['staging_better_on_net']})",
        "=" * 68,
    ]
    return "\n".join(lines)
