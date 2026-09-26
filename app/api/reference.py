"""内置算例路由：启动自检用的两级构型与单级对照。"""

from __future__ import annotations

from fastapi import APIRouter

from app.services.reference import run_reference

router = APIRouter(tags=["reference"])


@router.get(
    "/reference/example",
    summary="载入内置两级算例及其同等结构比单级对照，供手工复核",
)
def reference_example() -> dict[str, object]:
    return run_reference()
