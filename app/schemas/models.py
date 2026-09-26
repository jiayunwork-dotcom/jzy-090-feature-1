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


# ---------------------------------------------------------------------------
# 反解（给目标反推构型）
# ---------------------------------------------------------------------------


class InverseStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ve: float = Field(..., description="有效排气速度 m/s，必须 > 0")
    structural_mass: float = Field(..., description="本级结构空重 kg，>= 0")
    burn_time: float = Field(..., description="有效工作时间 s，必须 > 0")
    propellant_min: float = Field(
        0.0, description="允许加注推进剂下限 kg，>= 0；缺省为 0"
    )
    propellant_max: float = Field(
        ..., description="允许加注推进剂上限 kg，必须 >= propellant_min"
    )


class InverseSolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_delta_v: float = Field(
        ..., description="目标净速度增量 m/s，必须 >= 0"
    )
    payload_mass: float = Field(..., description="有效载荷质量 kg，>= 0")
    stages: list[InverseStageRequest] = Field(
        ..., description="自下而上排列的各级参数，至少一级"
    )
    propellant_budget: float | None = Field(
        None, description="可选推进剂总预算上限 kg，>= 0"
    )
    tolerance: float | None = Field(
        None, description="净速度增量容差 m/s，必须 > 0；缺省 1.0"
    )
    drag: DragRequest | None = Field(
        None, description="可选大气阻力扣减；缺省不扣减"
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


class InverseStageSolution(BaseModel):
    """反解结局一中的逐级结果：加注量 + 前向质量链与逐级 Δv。"""

    index: int
    ve: float
    structural_mass: float
    burn_time: float
    propellant_min: float
    propellant_max: float
    propellant_mass: float
    m0: float
    mf: float
    upper_mass: float
    mass_ratio: float
    ideal_delta_v: float
    gravity_loss: float


class InverseSolveResponse(BaseModel):
    """反解响应：status='solved' 与 status='infeasible' 共用同一信封。

    * solved：stages/各级加注量/达成净值/偏差/迭代轮数齐备，不可行字段为空；
    * infeasible：reason_code + binding_constraint + binding_stage_indices
      指明被哪一侧约束卡死，求解字段为空。
    输入本身不成立时不进入本模型，由统一错误信封返回 422。
    """

    status: Literal["solved", "infeasible"]

    # 共有回显
    target_delta_v: float
    payload_mass: float
    propellant_budget: float | None = None
    tolerance: float

    # solved 专属
    stages: list[InverseStageSolution] | None = None
    total_propellant_mass: float | None = None
    liftoff_mass: float | None = None
    overall_mass_ratio: float | None = None
    ideal_total_delta_v: float | None = None
    gravity_loss_total: float | None = None
    drag_loss_total: float | None = None
    drag_mode: str | None = None
    achieved_net_delta_v: float | None = None
    deviation: float | None = None
    iterations: int | None = None

    # infeasible 专属
    reason_code: str | None = None
    binding_constraint: str | None = None
    binding_stage_indices: list[int] | None = None
    max_achievable_net_delta_v: float | None = None
    min_achievable_net_delta_v: float | None = None
    detail: str | None = None
