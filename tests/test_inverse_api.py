"""反解 HTTP 接口：三种结局的机器可读结论与响应结构。

* solved：逐级加注量、逐级质量比与 Δv、达成净值、偏差、迭代轮数；
* infeasible：明确原因码与卡死约束侧；
* 输入非法：422 结构化错误信封（在 test_inverse_validation 中覆盖）。
"""

from __future__ import annotations

import math

import pytest


def _stage(**over):
    s = {
        "ve": 3200.0,
        "structural_mass": 2000.0,
        "burn_time": 100.0,
        "propellant_min": 0.0,
        "propellant_max": 20000.0,
    }
    s.update(over)
    return s


def test_solved_response_shape_and_content(client):
    body = {
        "target_delta_v": 4000.0,
        "payload_mass": 1000.0,
        "tolerance": 1.0,
        "stages": [
            _stage(ve=3200.0, structural_mass=2000.0, burn_time=120.0),
            _stage(ve=3500.0, structural_mass=1500.0, burn_time=80.0),
        ],
    }
    resp = client.post("/api/v1/inverse/solve", json=body)
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "solved"
    assert data["target_delta_v"] == 4000.0
    assert data["payload_mass"] == 1000.0
    assert data["tolerance"] == 1.0
    assert data["reason_code"] is None
    assert data["binding_constraint"] is None

    # 逐级内容：加注量落在上下限，质量比与 Δv 由前向口径给出
    assert len(data["stages"]) == 2
    total = 0.0
    ideal_sum = 0.0
    for i, st in enumerate(data["stages"]):
        assert st["index"] == i
        assert 0.0 <= st["propellant_mass"] <= 20000.0
        assert st["propellant_min"] == 0.0
        assert st["propellant_max"] == 20000.0
        assert st["mass_ratio"] == pytest.approx(st["m0"] / st["mf"])
        assert st["ideal_delta_v"] == pytest.approx(
            st["ve"] * math.log(st["mass_ratio"])
        )
        total += st["propellant_mass"]
        ideal_sum += st["ideal_delta_v"]

    assert data["total_propellant_mass"] == pytest.approx(total)
    assert data["ideal_total_delta_v"] == pytest.approx(ideal_sum)
    assert data["achieved_net_delta_v"] == pytest.approx(
        data["ideal_total_delta_v"]
        - data["gravity_loss_total"]
        - data["drag_loss_total"]
    )
    assert data["deviation"] == pytest.approx(
        data["achieved_net_delta_v"] - 4000.0
    )
    assert abs(data["deviation"]) <= 1.0
    assert isinstance(data["iterations"], int) and data["iterations"] >= 1
    assert data["liftoff_mass"] > 0.0


def test_solved_with_default_tolerance(client):
    body = {
        "target_delta_v": 3000.0,
        "payload_mass": 2000.0,
        "stages": [_stage()],
    }
    data = client.post("/api/v1/inverse/solve", json=body).json()
    assert data["status"] == "solved"
    assert data["tolerance"] == 1.0
    assert abs(data["deviation"]) <= 1.0


def test_infeasible_stage_max_envelope(client):
    body = {
        "target_delta_v": 9000.0,
        "payload_mass": 500.0,
        "stages": [
            _stage(ve=3000.0, structural_mass=1000.0, burn_time=100.0,
                   propellant_max=2000.0),
            _stage(ve=3000.0, structural_mass=1000.0, burn_time=80.0,
                   propellant_max=2000.0),
        ],
    }
    resp = client.post("/api/v1/inverse/solve", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "infeasible"
    assert data["reason_code"] == "TARGET_UNREACHABLE"
    assert data["binding_constraint"] == "stage_propellant_max"
    assert data["binding_stage_indices"] == [0, 1]
    assert data["max_achievable_net_delta_v"] < 9000.0
    assert data["stages"] is None
    assert data["achieved_net_delta_v"] is None
    assert data["detail"]
    assert isinstance(data["iterations"], int)


def test_infeasible_budget_envelope(client):
    body = {
        "target_delta_v": 9000.0,
        "payload_mass": 500.0,
        "propellant_budget": 4000.0,
        "stages": [
            _stage(ve=3000.0, structural_mass=1000.0, burn_time=100.0,
                   propellant_max=50000.0),
            _stage(ve=3000.0, structural_mass=1000.0, burn_time=80.0,
                   propellant_max=50000.0),
        ],
    }
    data = client.post("/api/v1/inverse/solve", json=body).json()
    assert data["status"] == "infeasible"
    assert data["binding_constraint"] == "propellant_budget"
    assert data["binding_stage_indices"] == []
    assert data["max_achievable_net_delta_v"] < 9000.0
    assert "预算" in data["detail"]


def test_infeasible_budget_below_minimum_envelope(client):
    body = {
        "target_delta_v": 500.0,
        "payload_mass": 500.0,
        "propellant_budget": 2000.0,
        "stages": [_stage(propellant_min=3000.0, propellant_max=8000.0)],
    }
    data = client.post("/api/v1/inverse/solve", json=body).json()
    assert data["status"] == "infeasible"
    assert data["reason_code"] == "BUDGET_BELOW_MINIMUM"
    assert data["binding_constraint"] == "propellant_budget"
    assert data["max_achievable_net_delta_v"] is None


def test_infeasible_overshoot_at_minimum_envelope(client):
    body = {
        "target_delta_v": 100.0,
        "payload_mass": 500.0,
        "stages": [_stage(ve=3000.0, structural_mass=1000.0, burn_time=100.0,
                          propellant_min=5000.0, propellant_max=8000.0)],
    }
    data = client.post("/api/v1/inverse/solve", json=body).json()
    assert data["status"] == "infeasible"
    assert data["reason_code"] == "TARGET_OVERSHOT_AT_MINIMUM"
    assert data["binding_constraint"] == "stage_propellant_min"
    assert data["min_achievable_net_delta_v"] > 100.0
    assert data["max_achievable_net_delta_v"] is not None


def test_explicit_drag_solved(client):
    # 阻力显式扣减 200 m/s：目标按净值给，求解器与前向共用同一扣减
    body = {
        "target_delta_v": 3500.0,
        "payload_mass": 1000.0,
        "tolerance": 1.0,
        "drag": {"mode": "explicit", "value": 200.0},
        "stages": [_stage(ve=3200.0, structural_mass=1500.0, burn_time=100.0)],
    }
    data = client.post("/api/v1/inverse/solve", json=body).json()
    assert data["status"] == "solved"
    assert data["drag_mode"] == "explicit"
    assert data["drag_loss_total"] == pytest.approx(200.0)
    assert abs(data["achieved_net_delta_v"] - 3500.0) <= 1.0


def test_budget_echoed_when_absent(client):
    body = {
        "target_delta_v": 3000.0,
        "payload_mass": 2000.0,
        "stages": [_stage()],
    }
    data = client.post("/api/v1/inverse/solve", json=body).json()
    assert data["propellant_budget"] is None


def test_endpoint_listed_in_root(client):
    root = client.get("/").json()
    assert "POST /api/v1/inverse/solve" in root["endpoints"]
