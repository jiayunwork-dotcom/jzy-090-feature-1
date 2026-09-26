"""HTTP 层：单次核算、错误信封、以及"ve 加倍则该级 Δv 加倍"的接口级复核。"""

from __future__ import annotations

import math

import pytest


def test_single_configuration_endpoint(client, two_stage_payload):
    resp = client.post("/api/v1/delta-v", json=two_stage_payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage_count"] == 2
    # 逐级质量比与逐级 Δv 都要返回
    assert [s["index"] for s in body["stages"]] == [0, 1]
    top = body["stages"][1]
    assert top["mass_ratio"] == pytest.approx(12000.0 / 4000.0)
    # 上面级：m0 = 2000 结构 + 8000 推进 + 2000 载荷
    assert top["m0"] == 12000.0
    assert top["mf"] == 4000.0
    assert top["ideal_delta_v"] == pytest.approx(3200.0 * math.log(3.0))
    # 重力损失与净值
    assert body["gravity_loss_total"] == pytest.approx(9.80665 * 200.0)
    assert body["net_delta_v"] == pytest.approx(
        body["ideal_total_delta_v"] - body["gravity_loss_total"]
    )
    # 理想总量等于逐级之和
    assert body["ideal_total_delta_v"] == pytest.approx(
        sum(s["ideal_delta_v"] for s in body["stages"])
    )


def test_endpoint_ve_doubling_doubles_that_stage_delta_v(client, two_stage_payload):
    r1 = client.post("/api/v1/delta-v", json=two_stage_payload).json()

    doubled = {
        "stages": [
            dict(two_stage_payload["stages"][0]),
            dict(two_stage_payload["stages"][1], ve=6400.0),
        ],
        "payload_mass": two_stage_payload["payload_mass"],
    }
    r2 = client.post("/api/v1/delta-v", json=doubled).json()

    # 只有上面级的 ve 改变，其理想 Δv 严格加倍；第一级完全不变
    assert r2["stages"][1]["ideal_delta_v"] == pytest.approx(
        2.0 * r1["stages"][1]["ideal_delta_v"]
    )
    assert r2["stages"][0] == r1["stages"][0]


def test_endpoint_error_envelope(client):
    bad = {
        "stages": [
            {"ve": -1.0, "structural_mass": 1.0, "propellant_mass": 1.0, "burn_time": 1.0}
        ],
        "payload_mass": 1.0,
    }
    resp = client.post("/api/v1/delta-v", json=bad)
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "NON_POSITIVE_EXHAUST_VELOCITY"
    assert err["stage_index"] == 0
    assert "有效排气速度" in err["reason"]


def test_missing_field_and_extra_field_rejected(client):
    # 缺少必填字段
    resp = client.post("/api/v1/delta-v", json={"payload_mass": 1.0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "REQUEST_VALIDATION"

    # 拼写错误/额外字段不允许静默通过
    bad_extra = {
        "stages": [
            {"ve": 3000.0, "structural_mass": 1.0, "propellant_mass": 1.0,
             "burn_time": 1.0, "whoops": 1}
        ],
        "payload_mass": 1.0,
    }
    assert client.post("/api/v1/delta-v", json=bad_extra).status_code == 422


def test_health_and_root(client):
    assert client.get("/health").json() == {"status": "ok"}
    root = client.get("/").json()
    assert root["service"] == "multi-stage-rocket-delta-v-engine"
    assert "POST /api/v1/delta-v" in root["endpoints"]
