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
