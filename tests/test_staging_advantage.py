"""核心不变量③：相同结构质量比下，多级拆分优于把全部推进剂塞进单级。

同时通过 HTTP 复核内置参考算例确实给出"分级明显更优"。
"""

from __future__ import annotations

import pytest

from app.core.engine import StageInput, evaluate
from app.core.losses import DragSpec
from app.services.reference import (
    SINGLE_STAGE_STAGE,
    TWO_STAGE_STAGES,
    run_reference,
)

G = 9.80665


def _make_stages(specs: list[tuple[float, float, float]], ve: float) -> list[StageInput]:
    # specs: (ms, mp, burn_time)；所有级结构比一致
    return [StageInput(ve=ve, structural_mass=ms, propellant_mass=mp, burn_time=bt)
            for ms, mp, bt in specs]


def test_two_stage_beats_single_same_structural_ratio():
    payload = 2000.0
    ve = 3200.0
    # ε = 2000/(2000+8000) = 0.2，两级完全相同
    two = evaluate(
        _make_stages([(2000.0, 8000.0, 120.0), (2000.0, 8000.0, 80.0)], ve),
        payload,
        DragSpec(),
    )
    # 单级：结构与推进剂取和，结构比仍为 0.2，工作时间取和（保证重力损失口径一致）
    one = evaluate(
        _make_stages([(4000.0, 16000.0, 200.0)], ve),
        payload,
        DragSpec(),
    )

    assert two.ideal_total_delta_v > one.ideal_total_delta_v
    # "明显优于"：两级理想 Δv 高出至少 10%
    assert two.ideal_total_delta_v > 1.10 * one.ideal_total_delta_v
    # 工作时间总和相同 => 重力损失相同，因此净值上也严格更优
    assert two.gravity_loss_total == pytest.approx(one.gravity_loss_total)
    assert two.net_delta_v > one.net_delta_v


def test_three_stage_monotonic_split_advantage():
    # 同样结构比下，拆得更细理想 Δv 进一步增大（理想值，不含损失口径差异）
    payload = 1000.0
    ve = 3000.0
    one = evaluate(
        [StageInput(ve, 3000.0, 9000.0, 1.0)], payload, DragSpec()
    )
    two = evaluate(
        [
            StageInput(ve, 1500.0, 4500.0, 0.5),
            StageInput(ve, 1500.0, 4500.0, 0.5),
        ],
        payload,
        DragSpec(),
    )
    assert two.ideal_total_delta_v > one.ideal_total_delta_v


def test_builtin_reference_staging_advantage():
    ref = run_reference()
    assert ref["staging_better_on_ideal"] is True
    assert ref["staging_better_on_net"] is True
    assert ref["ideal_advantage_mps"] > 500.0
    assert ref["net_advantage_mps"] > 500.0
    # 单级对照与两级保持相同结构比（0.2）与相同加权 ve
    assert SINGLE_STAGE_STAGE.structural_mass == pytest.approx(
        sum(s.structural_mass for s in TWO_STAGE_STAGES)
    )
    assert SINGLE_STAGE_STAGE.propellant_mass == pytest.approx(
        sum(s.propellant_mass for s in TWO_STAGE_STAGES)
    )
    assert SINGLE_STAGE_STAGE.ve == pytest.approx(3200.0)


def test_reference_endpoint(client):
    resp = client.get("/api/v1/reference/example")
    assert resp.status_code == 200
    body = resp.json()
    assert body["staging_better_on_ideal"] is True
    assert body["two_stage"]["stage_count"] == 2
    assert body["single_stage_comparator"]["stage_count"] == 1
