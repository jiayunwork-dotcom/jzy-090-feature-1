"""单次核算与批量比选的调度层。

* 单次：校验 -> 映射为内核类型 -> 引擎核算 -> 序列化；
* 批量：各候选构型各自独立走一遍"校验/核算"，单项失败只记录该单项的
  错误原因，绝不影响、覆盖其它构型，也不共享任何可变状态。
"""

from __future__ import annotations

from typing import Any

from app.core.engine import ConfigurationResult, StageInput, evaluate
from app.core.losses import DragSpec
from app.services import validator


def _to_stage_inputs(raw_config: Any) -> list[StageInput]:
    stages = validator._get(raw_config, "stages")  # noqa: SLF001
    return [
        StageInput(
            ve=float(validator._get(s, "ve")),  # noqa: SLF001
            structural_mass=float(validator._get(s, "structural_mass")),  # noqa: SLF001
            propellant_mass=float(validator._get(s, "propellant_mass")),  # noqa: SLF001
            burn_time=float(validator._get(s, "burn_time")),  # noqa: SLF001
        )
        for s in stages
    ]


def _to_drag_spec(raw_config: Any) -> DragSpec:
    raw_drag = validator._get(raw_config, "drag")  # noqa: SLF001
    if raw_drag is None:
        return DragSpec()
    mode = validator._get(raw_drag, "mode") or "none"
    value = validator._get(raw_drag, "value")
    fraction = validator._get(raw_drag, "fraction")
    return DragSpec(
        mode=mode,
        value=float(value) if value is not None else 0.0,
        fraction=float(fraction) if fraction is not None else 0.0,
    )


def serialize_result(result: ConfigurationResult) -> dict[str, Any]:
    """把内核结果转为可被 ConfigurationResponse 接收的 dict。"""
    return {
        "stage_count": result.stage_count,
        "stages": [
            {
                "index": s.index,
                "ve": s.ve,
                "m0": s.m0,
                "mf": s.mf,
                "upper_mass": s.upper_mass,
                "mass_ratio": s.mass_ratio,
                "ideal_delta_v": s.ideal_delta_v,
                "gravity_loss": s.gravity_loss,
            }
            for s in result.stages
        ],
        "payload_mass": result.payload_mass,
        "liftoff_mass": result.liftoff_mass,
        "overall_mass_ratio": result.overall_mass_ratio,
        "ideal_total_delta_v": result.ideal_total_delta_v,
        "gravity_loss_total": result.gravity_loss_total,
        "drag_loss_total": result.drag_loss_total,
        "net_delta_v": result.net_delta_v,
        "drag_mode": result.drag_mode,
    }


def compute_configuration(raw_config: Any) -> dict[str, Any]:
    """单次一箭构型核算。非法输入抛出 RocketValidationError。"""
    validator.validate_configuration(raw_config)
    payload = float(validator._get(raw_config, "payload_mass"))  # noqa: SLF001
    result = evaluate(_to_stage_inputs(raw_config), payload, _to_drag_spec(raw_config))
    return serialize_result(result)


def compute_batch(raw_configs: list[Any]) -> dict[str, Any]:
    """一批候选构型批量比选，逐项独立。"""
    validator.validate_batch(raw_configs)
    items: list[dict[str, Any]] = []
    succeeded = 0
    for i, raw in enumerate(raw_configs):
        try:
            result_dict = compute_configuration(raw)
        except Exception as exc:  # 单项错误隔离
            detail = _error_detail(exc)
            items.append({"index": i, "ok": False, "result": None, "error": detail})
            continue
        items.append({"index": i, "ok": True, "result": result_dict, "error": None})
        succeeded += 1
    return {
        "total": len(raw_configs),
        "succeeded": succeeded,
        "failed": len(raw_configs) - succeeded,
        "items": items,
    }


def _error_detail(exc: Exception) -> dict[str, object]:
    to_detail = getattr(exc, "to_detail", None)
    if callable(to_detail):
        return to_detail()
    return {"code": "INTERNAL_ERROR", "reason": str(exc)}
