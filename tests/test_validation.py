"""输入合法性校验：每类非法输入都必须带明确原因失败。

覆盖：ve<=0、mf>=m0（推进剂为零）、级数不足一级、推进剂为零却要正 Δv、
burn_time<=0、负质量、非有限值、非法阻力；以及内核层面的防御性错误。
"""

from __future__ import annotations

import math

import pytest

from app.core.errors import (
    NEGATIVE_PAYLOAD_MASS,
    NEGATIVE_PROPELLANT_MASS,
    NEGATIVE_STRUCTURAL_MASS,
    NON_FINITE_VALUE,
    NON_POSITIVE_BURN_TIME,
    NON_POSITIVE_EXHAUST_VELOCITY,
    ZERO_PROPELLANT,
    RocketValidationError,
)
from app.services.validator import validate_configuration


def base_stage(**over):
    stage = {
        "ve": 3200.0,
        "structural_mass": 2000.0,
        "propellant_mass": 8000.0,
        "burn_time": 100.0,
    }
    stage.update(over)
    return stage


def base_config(stages=None, payload=2000.0):
    return {"stages": stages if stages is not None else [base_stage()], "payload_mass": payload}


def expect_code(raw, code):
    with pytest.raises(RocketValidationError) as ei:
        validate_configuration(raw)
    assert ei.value.code == code
    assert ei.value.reason  # 原因信息非空


def test_no_stages_rejected():
    expect_code({"stages": [], "payload_mass": 2000.0}, "NO_STAGES")
    expect_code({"stages": None, "payload_mass": 2000.0}, "NO_STAGES")


def test_non_positive_ve_rejected():
    expect_code(base_config([base_stage(ve=0.0)]), NON_POSITIVE_EXHAUST_VELOCITY)
    expect_code(base_config([base_stage(ve=-1500.0)]), NON_POSITIVE_EXHAUST_VELOCITY)


def test_non_positive_burn_time_rejected():
    expect_code(base_config([base_stage(burn_time=0.0)]), NON_POSITIVE_BURN_TIME)
    expect_code(base_config([base_stage(burn_time=-3.0)]), NON_POSITIVE_BURN_TIME)


def test_zero_propellant_rejected():
    # 推进剂为零 => m0 == mf，物理上 Δv 必为 0，不允许假装要正的速度增量
    expect_code(base_config([base_stage(propellant_mass=0.0)]), ZERO_PROPELLANT)


def test_negative_masses_rejected():
    expect_code(base_config([base_stage(structural_mass=-1.0)]), NEGATIVE_STRUCTURAL_MASS)
    expect_code(base_config([base_stage(propellant_mass=-50.0)]), NEGATIVE_PROPELLANT_MASS)
    expect_code(base_config(payload=-1.0), NEGATIVE_PAYLOAD_MASS)


def test_non_finite_values_rejected():
    expect_code(base_config([base_stage(ve=math.nan)]), NON_FINITE_VALUE)
    expect_code(base_config([base_stage(ve=math.inf)]), NON_FINITE_VALUE)
    expect_code(base_config(payload=math.nan), NON_FINITE_VALUE)
    expect_code(base_config([base_stage(burn_time=math.inf)]), NON_FINITE_VALUE)


def test_stage_index_reported_for_bad_stage():
    raw = base_config([base_stage(), base_stage(ve=-1.0)])
    with pytest.raises(RocketValidationError) as ei:
        validate_configuration(raw)
    assert ei.value.stage_index == 1
    assert ei.value.field == "ve"


def test_drag_validation():
    expect_code(
        {"stages": [base_stage()], "payload_mass": 1.0,
         "drag": {"mode": "explicit", "value": -1.0}},
        "NON_POSITIVE_DRAG_VALUE",
    )
    expect_code(
        {"stages": [base_stage()], "payload_mass": 1.0,
         "drag": {"mode": "bogus"}},
        "BAD_DRAG_MODE",
    )


def test_valid_configuration_passes():
    validate_configuration(base_config())
