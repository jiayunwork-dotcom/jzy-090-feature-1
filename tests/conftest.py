"""pytest 共享夹具。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    # TestClient 会触发 lifespan，从而顺带验证启动算例可正常载入打印
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def two_stage_payload() -> dict[str, object]:
    """标准两级构型（与参考算例同结构，便于复用）。"""
    return {
        "stages": [
            {"ve": 3200.0, "structural_mass": 2000.0, "propellant_mass": 8000.0, "burn_time": 120.0},
            {"ve": 3200.0, "structural_mass": 2000.0, "propellant_mass": 8000.0, "burn_time": 80.0},
        ],
        "payload_mass": 2000.0,
    }
