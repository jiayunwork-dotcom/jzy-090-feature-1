"""目标反推的输入校验：不成立的输入必须在开算之前被结构化错误挡回。"""

from __future__ import annotations

import math

import pytest

from app.core.errors import RocketValidationError
from app.services.validator import validate_inverse_request


def stage(**over):
    s = {
        "ve": 3200.0,
        "structural_mass": 2000.0,
        "burn_time": 100.0,
        "propellant_min": 100.0,
        "propellant_max": 10000.0,
    }
    s.update(over)
    return s


_UNSET = object()


def req(stages=_UNSET, **over):
    r = {
        "target_net_delta_v": 4000.0,
        "payload_mass": 2000.0,
        "stages": [stage()] if stages is _UNSET else stages,
    }
    r.update(over)
    return r


def expect_code(raw, code):
    with pytest.raises(RocketValidationError) as ei:
        validate_inverse_request(raw)
    assert ei.value.code == code
    assert ei.value.reason


def test_no_stages():
    expect_code(req(stages=[]), "NO_STAGES")
    expect_code(req(stages=None), "NO_STAGES")


def test_negative_target():
    expect_code(req(target_net_delta_v=-0.1), "NEGATIVE_TARGET_DELTA_V")


def test_zero_target_is_allowed():
    validate_inverse_request(req(target_net_delta_v=0.0))


def test_negative_payload():
    expect_code(req(payload_mass=-2.0), "NEGATIVE_PAYLOAD_MASS")


def test_non_finite_values():
    expect_code(req(target_net_delta_v=math.nan), "NON_FINITE_VALUE")
    expect_code(req(target_net_delta_v=math.inf), "NON_FINITE_VALUE")
    expect_code(req(payload_mass=math.inf), "NON_FINITE_VALUE")
    expect_code(req(stages=[stage(ve=math.inf)]), "NON_FINITE_VALUE")
    expect_code(req(stages=[stage(propellant_min=math.nan)]), "NON_FINITE_VALUE")
    expect_code(req(tolerance=math.nan), "NON_FINITE_VALUE")
    expect_code(req(propellant_budget=math.inf), "NON_FINITE_VALUE")


def test_stage_physical_fields():
    expect_code(req(stages=[stage(ve=0.0)]), "NON_POSITIVE_EXHAUST_VELOCITY")
    expect_code(req(stages=[stage(ve=-1.0)]), "NON_POSITIVE_EXHAUST_VELOCITY")
    expect_code(req(stages=[stage(burn_time=0.0)]), "NON_POSITIVE_BURN_TIME")
    expect_code(req(stages=[stage(structural_mass=-1.0)]),
                "NEGATIVE_STRUCTURAL_MASS")


def test_propellant_bounds():
    expect_code(req(stages=[stage(propellant_min=0.0)]),
                "NON_POSITIVE_PROPELLANT_BOUND")
    expect_code(req(stages=[stage(propellant_min=-5.0)]),
                "NON_POSITIVE_PROPELLANT_BOUND")
    expect_code(req(stages=[stage(propellant_max=0.0)]),
                "NON_POSITIVE_PROPELLANT_BOUND")
    expect_code(req(stages=[stage(propellant_min=9000.0, propellant_max=1000.0)]),
                "PROPELLANT_BOUNDS_REVERSED")


def test_bad_stage_index_reported():
    raw = req(stages=[stage(), stage(ve=-1.0)])
    with pytest.raises(RocketValidationError) as ei:
        validate_inverse_request(raw)
    assert ei.value.stage_index == 1
    assert ei.value.field == "ve"


def test_bounds_reversed_reports_stage_and_field():
    raw = req(stages=[stage(), stage(propellant_min=9.0, propellant_max=1.0)])
    with pytest.raises(RocketValidationError) as ei:
        validate_inverse_request(raw)
    assert ei.value.stage_index == 1
    assert ei.value.field == "propellant_max"


def test_tolerance_must_be_positive():
    expect_code(req(tolerance=0.0), "NON_POSITIVE_TOLERANCE")
    expect_code(req(tolerance=-1e-6), "NON_POSITIVE_TOLERANCE")
    # 缺省容差合法
    validate_inverse_request(req())


def test_budget_rules():
    expect_code(req(propellant_budget=-1.0), "NEGATIVE_PROPELLANT_BUDGET")
    expect_code(req(propellant_budget=0.0), "BUDGET_BELOW_MINIMUM")
    expect_code(req(propellant_budget=50.0), "BUDGET_BELOW_MINIMUM")
    # 恰放下各级下限合法
    validate_inverse_request(req(propellant_budget=100.0))


def test_valid_request_passes():
    validate_inverse_request(req(stages=[stage(), stage(ve=3000.0, burn_time=80.0)]))
