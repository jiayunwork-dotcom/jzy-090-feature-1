"""反解输入合法性校验：所有不成立的输入必须在开算前以统一信封挡回。

覆盖：负目标/非有限目标、负载荷、ve<=0、burn_time<=0、负结构、
负的加注上下限、下限高于上限（定位到级与字段）、负/非有限预算、
非正容差、级数为 0、mf<=0（载荷与结构全为 0）、缺字段/额外字段、
非法阻力规格。
"""

from __future__ import annotations

import math

import pytest

from app.core.errors import (
    NEGATIVE_PAYLOAD_MASS,
    NEGATIVE_PROPELLANT_BUDGET,
    NEGATIVE_PROPELLANT_LIMIT,
    NEGATIVE_TARGET_DELTA_V,
    NON_FINITE_VALUE,
    NON_POSITIVE_BURN_TIME,
    NON_POSITIVE_EXHAUST_VELOCITY,
    NON_POSITIVE_MF,
    NON_POSITIVE_TOLERANCE,
    PROPELLANT_LIMIT_REVERSED,
    RocketValidationError,
)
from app.services.inverse_validator import validate_inverse_request


def stage(**over):
    s = {
        "ve": 3200.0,
        "structural_mass": 2000.0,
        "burn_time": 100.0,
        "propellant_min": 0.0,
        "propellant_max": 10000.0,
    }
    s.update(over)
    return s


def request_(stages=..., **over):
    r = {
        "target_delta_v": 5000.0,
        "payload_mass": 2000.0,
        "stages": [stage()] if stages is ... else stages,
    }
    r.update(over)
    return r


def expect_code(raw, code):
    with pytest.raises(RocketValidationError) as ei:
        validate_inverse_request(raw)
    assert ei.value.code == code
    assert ei.value.reason


def test_negative_target_rejected():
    expect_code(request_(target_delta_v=-1.0), NEGATIVE_TARGET_DELTA_V)
    expect_code(request_(target_delta_v=-0.001), NEGATIVE_TARGET_DELTA_V)


def test_target_zero_is_valid():
    # 目标恰好为 0 是合法请求（是否落在带内由求解器按约束判定）
    validate_inverse_request(request_(target_delta_v=0.0))


def test_non_finite_values_rejected():
    expect_code(request_(target_delta_v=math.nan), NON_FINITE_VALUE)
    expect_code(request_(target_delta_v=math.inf), NON_FINITE_VALUE)
    expect_code(request_(payload_mass=math.nan), NON_FINITE_VALUE)
    expect_code(
        request_(stages=[stage(ve=math.inf)]), NON_FINITE_VALUE
    )
    expect_code(
        request_(propellant_budget=math.inf), NON_FINITE_VALUE
    )
    expect_code(
        request_(propellant_budget=math.nan), NON_FINITE_VALUE
    )


def test_negative_payload_rejected():
    expect_code(request_(payload_mass=-0.5), NEGATIVE_PAYLOAD_MASS)


def test_stage_ve_and_burn_time_rejected():
    expect_code(request_(stages=[stage(ve=0.0)]), NON_POSITIVE_EXHAUST_VELOCITY)
    expect_code(request_(stages=[stage(ve=-3000.0)]), NON_POSITIVE_EXHAUST_VELOCITY)
    expect_code(
        request_(stages=[stage(burn_time=0.0)]), NON_POSITIVE_BURN_TIME
    )
    expect_code(
        request_(stages=[stage(burn_time=-5.0)]), NON_POSITIVE_BURN_TIME
    )


def test_negative_structural_rejected():
    expect_code(
        request_(stages=[stage(structural_mass=-1.0)]),
        "NEGATIVE_STRUCTURAL_MASS",
    )


def test_negative_propellant_limits_rejected():
    expect_code(
        request_(stages=[stage(propellant_min=-10.0)]),
        NEGATIVE_PROPELLANT_LIMIT,
    )
    expect_code(
        request_(stages=[stage(propellant_min=0.0, propellant_max=-1.0)]),
        NEGATIVE_PROPELLANT_LIMIT,
    )


def test_min_greater_than_max_rejected_with_stage_and_field():
    with pytest.raises(RocketValidationError) as ei:
        validate_inverse_request(
            request_(
                stages=[
                    stage(),
                    stage(propellant_min=9000.0, propellant_max=1000.0),
                ]
            )
        )
    assert ei.value.code == PROPELLANT_LIMIT_REVERSED
    assert ei.value.stage_index == 1
    assert ei.value.field == "propellant_min"
    assert "下限" in ei.value.reason and "上限" in ei.value.reason


def test_negative_budget_rejected():
    expect_code(
        request_(propellant_budget=-0.01), NEGATIVE_PROPELLANT_BUDGET
    )


def test_bad_tolerance_rejected():
    expect_code(request_(tolerance=0.0), NON_POSITIVE_TOLERANCE)
    expect_code(request_(tolerance=-2.0), NON_POSITIVE_TOLERANCE)
    expect_code(request_(tolerance=math.nan), NON_FINITE_VALUE)


def test_no_stages_rejected():
    expect_code(
        request_(stages=[], target_delta_v=1.0, payload_mass=1.0), "NO_STAGES"
    )
    expect_code(
        request_(stages=None, target_delta_v=1.0, payload_mass=1.0), "NO_STAGES"
    )


def test_non_positive_mf_rejected():
    # 载荷与结构全为 0：最顶一级 mf = 0，质量比无定义
    expect_code(
        request_(
            payload_mass=0.0,
            stages=[stage(structural_mass=0.0)],
        ),
        NON_POSITIVE_MF,
    )


def test_bad_drag_rejected():
    expect_code(
        request_(drag={"mode": "bogus"}), "BAD_DRAG_MODE"
    )


def test_valid_request_passes():
    validate_inverse_request(request_())
    # 可选字段全部省略也合法；budget=None / tolerance=None 取缺省
    validate_inverse_request(
        {
            "target_delta_v": 3000.0,
            "payload_mass": 1000.0,
            "stages": [
                {
                    "ve": 3000.0,
                    "structural_mass": 800.0,
                    "burn_time": 90.0,
                    "propellant_max": 12000.0,
                }
            ],
        }
    )


def test_http_error_envelope(client):
    # 下限反超上限：422 + 结构化信封，定位到级与字段
    body = {
        "target_delta_v": 5000.0,
        "payload_mass": 1000.0,
        "stages": [
            {
                "ve": 3200.0,
                "structural_mass": 2000.0,
                "burn_time": 100.0,
                "propellant_min": 8000.0,
                "propellant_max": 1000.0,
            }
        ],
    }
    resp = client.post("/api/v1/inverse/solve", json=body)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == PROPELLANT_LIMIT_REVERSED
    assert err["stage_index"] == 0
    assert err["field"] == "propellant_min"


def test_http_missing_and_extra_fields(client):
    # 缺必填字段（target_delta_v）：走请求模型错误信封
    resp = client.post(
        "/api/v1/inverse/solve",
        json={
            "payload_mass": 1000.0,
            "stages": [
                {
                    "ve": 3200.0,
                    "structural_mass": 2000.0,
                    "burn_time": 100.0,
                    "propellant_max": 10000.0,
                }
            ],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "REQUEST_VALIDATION"

    # 额外字段不允许静默通过
    resp = client.post(
        "/api/v1/inverse/solve",
        json={
            "target_delta_v": 3000.0,
            "payload_mass": 1000.0,
            "stages": [
                {
                    "ve": 3200.0,
                    "structural_mass": 2000.0,
                    "burn_time": 100.0,
                    "propellant_max": 10000.0,
                    "whoops": 1,
                }
            ],
        },
    )
    assert resp.status_code == 422


def test_http_negative_budget_envelope(client):
    body = {
        "target_delta_v": 5000.0,
        "payload_mass": 1000.0,
        "propellant_budget": -1.0,
        "stages": [
            {
                "ve": 3200.0,
                "structural_mass": 2000.0,
                "burn_time": 100.0,
                "propellant_max": 10000.0,
            }
        ],
    }
    resp = client.post("/api/v1/inverse/solve", json=body)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == NEGATIVE_PROPELLANT_BUDGET
    assert err["field"] == "propellant_budget"
