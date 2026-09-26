"""一批候选构型批量比选路由。

各构型的中间量与结论逐项独立，单项非法只影响该单项。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.models import BatchRequest, BatchResponse
from app.services import calculator

router = APIRouter(tags=["batch"])


@router.post(
    "/delta-v/batch",
    response_model=BatchResponse,
    summary="批量比选：一次核算多支候选构型，结果相互独立",
)
def compute_batch(request: BatchRequest) -> dict[str, object]:
    return calculator.compute_batch(list(request.configurations))
