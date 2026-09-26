"""FastAPI 应用入口。

职责仅限装配：路由、统一错误响应、启动时载入并打印内置算例。
物理方程、批量调度、校验均不在这里实现。
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api import batch, delta_v, reference
from app.core.constants import SERVICE_PORT
from app.core.errors import REQUEST_VALIDATION, RocketValidationError
from app.services.reference import format_reference_report, run_reference

API_PREFIX = "/api/v1"


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 服务启动后直接载入内置算例并打印，供人工手工对照
    reference_data = run_reference()
    print(format_reference_report(reference_data), flush=True)
    yield


app = FastAPI(
    title="多级火箭速度增量核算引擎",
    version="1.0.0",
    description=(
        "常驻的齐奥尔科夫斯基预算 HTTP 服务。范围限定为质量比与速度增量核算："
        "逐级 Δv 相加、重力损失 g·t_burn 扣除、可选轻量阻力扣减，"
        "并提供批量候选构型比选与内置两级参考算例。"
    ),
    lifespan=lifespan,
)
app.include_router(delta_v.router, prefix=API_PREFIX)
app.include_router(batch.router, prefix=API_PREFIX)
app.include_router(reference.router, prefix=API_PREFIX)


@app.exception_handler(RocketValidationError)
async def rocket_validation_handler(
    request: Request, exc: RocketValidationError
) -> JSONResponse:
    """领域校验失败：422 + 明确原因，而不是算出看似正常的数。"""
    return JSONResponse(status_code=422, content={"error": exc.to_detail()})


@app.exception_handler(RequestValidationError)
async def request_validation_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """请求结构/类型错误：同样包装成统一的中文错误信封。"""
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": REQUEST_VALIDATION,
                "reason": "请求体不满足接口模型要求，请检查字段与类型",
                "details": exc.errors(),
            }
        },
    )


@app.get("/health", tags=["meta"], summary="存活探针")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", tags=["meta"], summary="服务信息")
def root() -> dict[str, object]:
    return {
        "service": "multi-stage-rocket-delta-v-engine",
        "version": "1.0.0",
        "port": SERVICE_PORT,
        "api_prefix": API_PREFIX,
        "endpoints": [
            "POST /api/v1/delta-v",
            "POST /api/v1/delta-v/batch",
            "GET  /api/v1/reference/example",
            "GET  /health",
        ],
    }
