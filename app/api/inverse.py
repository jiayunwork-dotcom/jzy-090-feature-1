"""给目标反推构型路由：目标净速度增量 → 各级推进剂加注量。"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.models import InverseSolveRequest, InverseSolveResponse
from app.services import inverse as inverse_service

router = APIRouter(tags=["inverse"])


@router.post(
    "/inverse/solve",
    response_model=InverseSolveResponse,
    summary=(
        "给目标反推构型：在加注上下限与总预算内解出总推进剂最省的"
        "各级加注量，使前向内核算净速度增量落入目标容差带"
    ),
)
def solve_inverse(config: InverseSolveRequest) -> dict[str, object]:
    return inverse_service.solve_inverse_request(config)
