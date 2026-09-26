"""HTTP 层的 Pydantic 模型（请求与响应）。

只在 Web 边界使用，物理内核不依赖这些类型，保证内核可独立复用与测试。
所有模型额外字段一律拒绝（extra='forbid'），拼写错误不会被静默吞掉。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ve: float = Field(..., description="有效排气速度 m/s，必须 > 0")
    structural_mass: float = Field(..., description="本级结构质量 kg，>= 0")
    propellant_mass: float = Field(..., description="本级推进剂质量 kg，必须 > 0")
    burn_time: float = Field(..., description="有效工作时间 s，必须 > 0")


class DragRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["none", "explicit", "linear"] = Field(
        "none", description="阻力扣减模式"
    )
    value: float = Field(0.0, description="explicit 模式下直接扣除的 m/s，>= 0")
    fraction: float = Field(
        0.0, description="linear 模式下占理想 Δv 的比例，>= 0"
    )


class ConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stages: list[StageRequest] = Field(..., description="自下而上的各级，至少一级")
    payload_mass: float = Field(..., description="最终有效载荷质量 kg，>= 0")
    drag: DragRequest | None = Field(
        None, description="可选大气阻力扣减；缺省不扣减"
    )


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configurations: list[ConfigurationRequest] = Field(
        ..., description="待比选的一批候选构型，至少一支"
    )


class StageResponse(BaseModel):
    index: int
    ve: float
    m0: float
    mf: float
    upper_mass: float
    mass_ratio: float
    ideal_delta_v: float
    gravity_loss: float


class ConfigurationResponse(BaseModel):
    stage_count: int
    stages: list[StageResponse]
    payload_mass: float
    liftoff_mass: float
    overall_mass_ratio: float | None = Field(
        None, description="起飞质量/最终净载荷，仅展示用；有效载荷为 0 时为 null"
    )
    ideal_total_delta_v: float
    gravity_loss_total: float
    drag_loss_total: float
    net_delta_v: float
    drag_mode: str


class ErrorResponse(BaseModel):
    error: dict[str, object]


class BatchItemResponse(BaseModel):
    """批量中的单项：出错时 ok=false 且带原因，结果之间完全独立。"""

    index: int
    ok: bool
    result: ConfigurationResponse | None = None
    error: dict[str, object] | None = None


class BatchResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    items: list[BatchItemResponse]


# ---------------------------------------------------------------------------
# 目标反推（inverse）
# ---------------------------------------------------------------------------


class InverseStageRequest(BaseModel):
    """反推请求中自下而上排列的单级确定量；推进剂是待求未知量。"""

    model_config = ConfigDict(extra="forbid")

    ve: float = Field(..., description="有效排气速度 m/s，必须 > 0")
    structural_mass: float = Field(..., description="本级结构空重 kg，>= 0")
    burn_time: float = Field(..., description="有效工作时间 s，必须 > 0")
    propellant_min: float = Field(
        ..., description="该级允许加注推进剂的下限 kg，必须 > 0（需能回代前向核算）"
    )
    propellant_max: float = Field(
        ..., description="该级允许加注推进剂的上限 kg，必须 >= propellant_min"
    )


class InverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_net_delta_v: float = Field(
        ..., description="任务书要求的净速度增量目标 m/s，>= 0"
    )
    payload_mass: float = Field(..., description="最终有效载荷质量 kg，>= 0")
    stages: list[InverseStageRequest] = Field(
        ..., description="自下而上的各级确定量与加注上下限，至少一级"
    )
    tolerance: float | None = Field(
        None,
        description="达成净 Δv 与目标允许的偏差带 m/s，必须 > 0；缺省 1e-3",
    )
    propellant_budget: float | None = Field(
        None, description="可选的推进剂总预算上限 kg，>= 各级下限之和"
    )


class InverseStageResult(BaseModel):
    index: int
    ve: float
    structural_mass: float
    burn_time: float
    propellant_mass: float
    propellant_min: float
    propellant_max: float
    m0: float
    mf: float
    upper_mass: float
    mass_ratio: float
    ideal_delta_v: float
    gravity_loss: float


class InverseSolvedResponse(BaseModel):
    """反推成功（solved）响应的字段说明；实际响应体为 dict，状态随结局而变。"""

    status: Literal["solved", "infeasible"]
    target_net_delta_v: float
    achieved_net_delta_v: float | None
    deviation: float | None
    within_tolerance: bool
    tolerance: float
    payload_mass: float
    total_propellant_mass: float | None
    propellant_budget: float | None
    sweeps: int
    forward_evaluations: int
    stages: list[InverseStageResult]
    infeasible_reason_code: str | None = None
    limiting_constraint: str | None = None
    stages_at_max: list[int] | None = None
    budget_exhausted: bool | None = None
    reason: str | None = None
