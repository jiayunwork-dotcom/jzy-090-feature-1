"""损失扣减：重力损失恒为正且只能扣除；阻力可选、非负。"""

from __future__ import annotations

import pytest

from app.core.engine import StageInput, evaluate
from app.core.losses import (
    DragSpec,
    drag_loss,
    net_delta_v,
    stage_gravity_loss,
    total_gravity_loss,
)

G = 9.80665


def test_gravity_loss_is_g_times_burn():
    assert stage_gravity_loss(10.0) == pytest.approx(G * 10.0)
    assert total_gravity_loss([10.0, 20.0]) == pytest.approx(G * 30.0)


def test_gravity_only_reduces_net():
    ideal, gravity = 5000.0, G * 100.0
    assert net_delta_v(ideal, gravity, 0.0) == pytest.approx(ideal - gravity)
    # 正的工作时间下净值必然严格小于理想值，不可能被加成更大
    assert net_delta_v(ideal, gravity, 0.0) < ideal


def test_engine_net_equals_ideal_minus_losses(two_stage_payload):
    stages = [StageInput(**s) for s in two_stage_payload["stages"]]
    result = evaluate(stages, 2000.0, DragSpec())
    assert result.net_delta_v == pytest.approx(
        result.ideal_total_delta_v - result.gravity_loss_total
    )
    assert result.gravity_loss_total == pytest.approx(G * 200.0)
    assert result.drag_loss_total == 0.0


def test_drag_modes_nonnegative_and_subtracted():
    assert drag_loss(5000.0, DragSpec(mode="none")) == 0.0
    assert drag_loss(5000.0, DragSpec(mode="explicit", value=300.0)) == 300.0
    assert drag_loss(5000.0, DragSpec(mode="linear", fraction=0.1)) == 500.0

    assert net_delta_v(5000.0, 1000.0, 300.0) == 3700.0
    with pytest.raises(Exception):
        drag_loss(5000.0, DragSpec(mode="explicit", value=-1.0))


def test_drag_via_engine():
    stages = [StageInput(ve=3200.0, structural_mass=2000.0, propellant_mass=8000.0, burn_time=10.0)]
    result = evaluate(stages, 2000.0, DragSpec(mode="explicit", value=123.0))
    assert result.drag_loss_total == 123.0
    assert result.net_delta_v == pytest.approx(
        result.ideal_total_delta_v - result.gravity_loss_total - 123.0
    )
