"""批量比选：多构型一次算完，各构型中间量与结论独立、互不覆盖。"""

from __future__ import annotations

import pytest


def good_config(ve=3200.0):
    return {
        "stages": [
            {"ve": ve, "structural_mass": 2000.0, "propellant_mass": 8000.0, "burn_time": 100.0}
        ],
        "payload_mass": 2000.0,
    }


def bad_config():
    return {
        "stages": [
            {"ve": 0.0, "structural_mass": 1.0, "propellant_mass": 1.0, "burn_time": 1.0}
        ],
        "payload_mass": 1.0,
    }


def test_batch_mixed_valid_invalid(client):
    configs = [good_config(3000.0), bad_config(), good_config(6000.0)]
    resp = client.post("/api/v1/delta-v/batch", json={"configurations": configs})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["succeeded"] == 2
    assert body["failed"] == 1

    items = body["items"]
    assert [it["index"] for it in items] == [0, 1, 2]
    assert items[0]["ok"] is True and items[0]["error"] is None
    assert items[1]["ok"] is False and items[1]["result"] is None
    assert items[1]["error"]["code"] == "NON_POSITIVE_EXHAUST_VELOCITY"
    assert items[2]["ok"] is True


def test_batch_items_do_not_interfere(client):
    # 非法项夹在中间，不允许把相邻合法项的结果写串或覆盖
    configs = [good_config(3000.0), bad_config(), good_config(3000.0)]
    body = client.post("/api/v1/delta-v/batch", json={"configurations": configs}).json()
    first = body["items"][0]["result"]
    third = body["items"][2]["result"]
    assert first == pytest.approx(third)  # 相同输入 => 完全相同输出

    # ve 加倍的批量版本：第 2 支合法构型第一级 Δv 应为第 0 支的两倍
    configs2 = [good_config(3000.0), good_config(6000.0), bad_config()]
    body2 = client.post("/api/v1/delta-v/batch", json={"configurations": configs2}).json()
    dv0 = body2["items"][0]["result"]["stages"][0]["ideal_delta_v"]
    dv1 = body2["items"][1]["result"]["stages"][0]["ideal_delta_v"]
    assert dv1 == pytest.approx(2.0 * dv0)
    assert body2["items"][2]["ok"] is False


def test_empty_batch_rejected(client):
    resp = client.post("/api/v1/delta-v/batch", json={"configurations": []})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EMPTY_BATCH"
