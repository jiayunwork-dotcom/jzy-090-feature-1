"""目标反推 HTTP 接口：三种结局、错误信封、以及经前向接口的端到端往返。"""

from __future__ import annotations

import math

import pytest


def two_stage_inverse(target=4000.0, **over):
    body = {
        "target_net_delta_v": target,
        "payload_mass": 2000.0,
        "tolerance": 1e-5,
        "stages": [
            {"ve": 3200.0, "structural_mass": 2000.0, "burn_time": 120.0,
             "propellant_min": 100.0, "propellant_max": 20000.0},
            {"ve": 3200.0, "structural_mass": 2000.0, "burn_time": 80.0,
             "propellant_min": 100.0, "propellant_max": 20000.0},
        ],
    }
    body.update(over)
    return body


def test_inverse_solved_endpoint(client):
    resp = client.post("/api/v1/delta-v/inverse", json=two_stage_inverse())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "solved"
    assert body["within_tolerance"] is True
    assert abs(body["deviation"]) <= 1e-5
    assert body["sweeps"] >= 1
    assert body["forward_evaluations"] >= 1
    assert len(body["stages"]) == 2
    for s in body["stages"]:
        assert s["propellant_min"] <= s["propellant_mass"] <= s["propellant_max"]
        assert s["mass_ratio"] == pytest.approx(s["m0"] / s["mf"])
        assert s["ideal_delta_v"] == pytest.approx(s["ve"] * math.log(s["mass_ratio"]))
        # 逐级重力损失仍是前向模型 g*t
        assert s["gravity_loss"] == pytest.approx(9.80665 * s["burn_time"])


def test_inverse_roundtrip_through_forward_endpoint(client):
    """命根子：反解结果原样喂回 POST /delta-v，净 Δv 必须对得上。"""
    sol = client.post(
        "/api/v1/delta-v/inverse", json=two_stage_inverse(3600.0)
    ).json()
    assert sol["status"] == "solved"
    forward_body = {
        "stages": [
            {"ve": s["ve"], "structural_mass": s["structural_mass"],
             "propellant_mass": s["propellant_mass"], "burn_time": s["burn_time"]}
            for s in sol["stages"]
        ],
        "payload_mass": 2000.0,
    }
    fwd = client.post("/api/v1/delta-v", json=forward_body).json()
    # 同一套火箭方程、同一条质量链：两侧净 Δv 严格一致
    assert fwd["net_delta_v"] == pytest.approx(sol["achieved_net_delta_v"], abs=1e-9)
    assert abs(fwd["net_delta_v"] - 3600.0) <= 1e-5
    # 逐级质量比/Δv 也逐行一致
    for a, b in zip(fwd["stages"], sol["stages"]):
        assert a["mass_ratio"] == pytest.approx(b["mass_ratio"], abs=1e-12)
        assert a["ideal_delta_v"] == pytest.approx(b["ideal_delta_v"], abs=1e-9)


@pytest.mark.parametrize("target", [1200.0, 2400.0, 3300.0, 4500.0, 5100.0])
def test_inverse_roundtrip_enumerated_targets(client, target):
    body = two_stage_inverse(
        target,
        stages=[
            {"ve": 3100.0, "structural_mass": 2200.0, "burn_time": 110.0,
             "propellant_min": 50.0, "propellant_max": 30000.0},
            {"ve": 3400.0, "structural_mass": 1400.0, "burn_time": 70.0,
             "propellant_min": 50.0, "propellant_max": 30000.0},
        ],
        tolerance=1e-4,
    )
    sol = client.post("/api/v1/delta-v/inverse", json=body).json()
    assert sol["status"] == "solved"
    fwd = client.post("/api/v1/delta-v", json={
        "stages": [
            {"ve": s["ve"], "structural_mass": s["structural_mass"],
             "propellant_mass": s["propellant_mass"], "burn_time": s["burn_time"]}
            for s in sol["stages"]
        ],
        "payload_mass": 2000.0,
    }).json()
    assert abs(fwd["net_delta_v"] - target) <= 1e-4
    assert fwd["net_delta_v"] == pytest.approx(sol["achieved_net_delta_v"], abs=1e-10)


def test_infeasible_cap(client):
    resp = client.post(
        "/api/v1/delta-v/inverse",
        json=two_stage_inverse(
            99999.0,
            stages=[{"ve": 3200.0, "structural_mass": 2000.0, "burn_time": 120.0,
                     "propellant_min": 100.0, "propellant_max": 5000.0}],
            propellant_budget=1.0e9,
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "infeasible"
    assert body["limiting_constraint"] == "propellant_cap"
    assert body["stages_at_max"] == [0]
    assert body["budget_exhausted"] is False
    assert body["reason"]


def test_infeasible_budget(client):
    resp = client.post(
        "/api/v1/delta-v/inverse",
        json=two_stage_inverse(
            9000.0,
            stages=[{"ve": 3200.0, "structural_mass": 2000.0, "burn_time": 120.0,
                     "propellant_min": 100.0, "propellant_max": 50000.0}],
            propellant_budget=3000.0,
        ),
    )
    body = resp.json()
    assert body["status"] == "infeasible"
    assert body["limiting_constraint"] == "budget"
    assert body["budget_exhausted"] is True
    assert body["stages_at_max"] == []


@pytest.mark.parametrize("patch,code", [
    ({"target_net_delta_v": -1.0}, "NEGATIVE_TARGET_DELTA_V"),
    ({"payload_mass": -1.0}, "NEGATIVE_PAYLOAD_MASS"),
    ({"tolerance": 0.0}, "NON_POSITIVE_TOLERANCE"),
    ({"propellant_budget": -1.0}, "NEGATIVE_PROPELLANT_BUDGET"),
    ({"propellant_budget": 10.0}, "BUDGET_BELOW_MINIMUM"),
])
def test_invalid_top_level_fields(client, patch, code):
    resp = client.post("/api/v1/delta-v/inverse", json=two_stage_inverse(**patch))
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == code
    assert err["reason"]


def test_invalid_stage_bounds_report_index(client):
    body = two_stage_inverse(stages=[
        {"ve": 3200.0, "structural_mass": 2000.0, "burn_time": 120.0,
         "propellant_min": 9000.0, "propellant_max": 1000.0},
    ])
    resp = client.post("/api/v1/delta-v/inverse", json=body)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "PROPELLANT_BOUNDS_REVERSED"
    assert err["stage_index"] == 0
    assert err["field"] == "propellant_max"


def test_extra_and_missing_fields_rejected(client):
    good = two_stage_inverse()
    assert client.post("/api/v1/delta-v/inverse", json={**good, "x": 1}).status_code == 422
    assert client.post("/api/v1/delta-v/inverse", json={"payload_mass": 1.0}).status_code == 422
    bad_stage = {
        "target_net_delta_v": 1000.0, "payload_mass": 1.0,
        "stages": [{"ve": 3000.0, "structural_mass": 1.0, "burn_time": 1.0,
                    "propellant_min": 1.0, "propellant_max": 2.0, "oops": 9}],
    }
    assert client.post("/api/v1/delta-v/inverse", json=bad_stage).status_code == 422


def test_default_tolerance_used_when_absent(client):
    body = two_stage_inverse()
    del body["tolerance"]
    body = client.post("/api/v1/delta-v/inverse", json=body).json()
    assert body["tolerance"] == pytest.approx(1e-3)


def test_inverse_listed_in_root(client):
    endpoints = client.get("/").json()["endpoints"]
    assert "POST /api/v1/delta-v/inverse" in endpoints


def test_existing_endpoints_unchanged(client, two_stage_payload):
    # 前向单次、健康探针行为不被反解改动破坏
    assert client.get("/health").json() == {"status": "ok"}
    r = client.post("/api/v1/delta-v", json=two_stage_payload)
    assert r.status_code == 200
    assert r.json()["stage_count"] == 2
