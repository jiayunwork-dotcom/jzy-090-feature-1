"""单次一箭构型核算路由。"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.models import ConfigurationRequest, ConfigurationResponse
from app.services import calculator

router = APIRouter(tags=["delta-v"])


@router.post(
    "/delta-v",
    response_model=ConfigurationResponse,
    summary="单次一箭构型核算：逐级质量比、逐级 Δv、重力损失与净值",
)
def compute_delta_v(config: ConfigurationRequest) -> dict[str, object]:
    return calculator.compute_configuration(config)
