"""目标反推路由：给定目标净 Δv 与约束，反解各级推进剂加注量。

只暴露 HTTP，不含页面/账户概念。三种结局：
200 + status=solved（达成且容差内、最省）、200 + status=infeasible
（灌满仍不够，指明卡死侧）、422 结构化错误信封（输入本身不成立）。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.models import InverseRequest, InverseSolvedResponse
from app.services import inverse

router = APIRouter(tags=["inverse"])


@router.post(
    "/delta-v/inverse",
    response_model=InverseSolvedResponse,
    summary=(
        "目标反推：给定目标净 Δv、有效载荷与各级空重/排气速度/加注上下限，"
        "迭代解出最省的逐级推进剂加注量"
    ),
)
def compute_inverse(request: InverseRequest) -> dict[str, object]:
    return inverse.solve_target(request)
