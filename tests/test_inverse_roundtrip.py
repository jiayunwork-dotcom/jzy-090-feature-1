"""命根子不变量：反解与前向严格自洽的往返一致性（随机 + HTTP 全链路）。

对每组随机可行目标：
1. 反解出各级加注量；
2. 把加注量连同原本给定的空重、排气速度、工作时间、有效载荷原样
   组装成一支普通构型；
3. 分别经"求解器内核调用的前向核算"、独立再调一次前向内核、以及
   HTTP 前向接口三条路径回算净速度增量；
4. 断言三处数值与求解宣称的达成值一致（容差远小于请求容差），
   且都落在目标容差带内、约束全满足。

随机种子固定，保证 CI 可复现。
"""

from __future__ import annotations

import math
import random

import pytest

from app.core.engine import StageInput, evaluate
from app.core.losses import DragSpec
from app.solver.inverse import InverseProblem, InverseSolution, InverseStageSpec, solve_inverse


def _random_problem(rng: random.Random):
    # 重试直到全下限构型已有非负净值（避免重重力损失把下限净值压成负）
    for _ in range(100):
        n = rng.choice([1, 2, 2, 3, 4])
        stages = []
        for _ in range(n):
            ve = rng.uniform(2400.0, 4600.0)
            ms = rng.uniform(100.0, 3000.0)
            bt = rng.uniform(20.0, 150.0)
            # 下限取 >=1：保证回代构型也满足前向校验（前向禁止推进剂为 0）
            pmin = rng.choice([1.0, 1.0, rng.uniform(1.0, 800.0)])
            pmax = pmin + rng.uniform(2000.0, 22000.0)
            stages.append(InverseStageSpec(ve, ms, bt, pmin, pmax))
        payload = rng.uniform(100.0, 3000.0)
        mins = [s.propellant_min for s in stages]
        maxs = [s.propellant_max for s in stages]
        drag = DragSpec()

        def fwd_net(prop):
            return evaluate(
                [
                    StageInput(s.ve, s.structural_mass, x, s.burn_time)
                    for s, x in zip(stages, prop)
                ],
                payload,
                drag,
            ).net_delta_v

        net_min, net_max = fwd_net(mins), fwd_net(maxs)
        # 重结构多级构型的全下限净值可能为负（重力损失主导），但只要
        # 上限净值足够大，[0, 0.8·fmax] 内的非负目标就一定可达
        if net_max > 500.0:
            target = rng.uniform(0.15, 0.8) * net_max
            budget = sum(maxs) if rng.random() < 0.35 else None
            tolerance = rng.choice([0.5, 1.0, 2.0])
            return InverseProblem(
                target, payload, stages, budget, tolerance, drag
            )
    raise RuntimeError("随机生成器未能构造出有效问题")  # pragma: no cover


@pytest.mark.parametrize("seed", range(24))
def test_roundtrip_core_random(seed):
    rng = random.Random(20260926 + seed)
    problem = _random_problem(rng)
    outcome = solve_inverse(problem)
    assert isinstance(outcome, InverseSolution), (
        f"seed={seed} 意外不可行：{outcome}"
    )
    x = outcome.propellant_masses

    # 约束
    for i, (spec, xi) in enumerate(zip(problem.stages, x)):
        assert spec.propellant_min - 1e-9 <= xi <= spec.propellant_max + 1e-9, (
            f"seed={seed} 第 {i} 级越界: {xi}"
        )
    if problem.propellant_budget is not None:
        assert math.fsum(x) <= problem.propellant_budget + 1e-9

    # 路径 A：求解器内部评估结果（它用的就是前向内核）
    claimed = outcome.evaluation.net_delta_v
    # 路径 B：独立重新组装并再调一次前向内核
    independent = evaluate(
        [
            StageInput(s.ve, s.structural_mass, xi, s.burn_time)
            for s, xi in zip(problem.stages, x)
        ],
        problem.payload_mass,
        problem.drag,
    )
    independent_net = independent.net_delta_v
    assert independent_net == pytest.approx(claimed, abs=1e-9), (
        f"seed={seed} 独立前向回算 {independent_net} != 求解宣称 {claimed}"
    )
    # 两侧都必须落入目标容差带
    for net in (claimed, independent_net):
        assert abs(net - problem.target_delta_v) <= problem.tolerance + 1e-9 * max(
            1.0, abs(problem.target_delta_v)
        )

    # 逐级质量比/Δv 也必须与独立前向逐级结果一一对应
    for got, ref in zip(outcome.evaluation.stages, independent.stages):
        assert got.mass_ratio == pytest.approx(ref.mass_ratio, abs=1e-12)
        assert got.ideal_delta_v == pytest.approx(ref.ideal_delta_v, abs=1e-9)
        assert got.m0 == pytest.approx(ref.m0, abs=1e-9)
        assert got.mf == pytest.approx(ref.mf, abs=1e-9)


@pytest.mark.parametrize("seed", range(10))
def test_roundtrip_http_random(client, seed):
    """HTTP 全链路：反解 → 拿加注量喂回 POST /delta-v → 净值对得上。"""
    rng = random.Random(7700000 + seed)
    problem = _random_problem(rng)

    inv_body = {
        "target_delta_v": problem.target_delta_v,
        "payload_mass": problem.payload_mass,
        "propellant_budget": problem.propellant_budget,
        "tolerance": problem.tolerance,
        "stages": [
            {
                "ve": s.ve,
                "structural_mass": s.structural_mass,
                "burn_time": s.burn_time,
                "propellant_min": s.propellant_min,
                "propellant_max": s.propellant_max,
            }
            for s in problem.stages
        ],
    }
    inv = client.post("/api/v1/inverse/solve", json=inv_body)
    assert inv.status_code == 200
    solved = inv.json()
    assert solved["status"] == "solved", solved

    fwd_body = {
        "payload_mass": problem.payload_mass,
        "stages": [
            {
                "ve": s.ve,
                "structural_mass": s.structural_mass,
                "propellant_mass": st["propellant_mass"],
                "burn_time": s.burn_time,
            }
            for s, st in zip(problem.stages, solved["stages"])
        ],
    }
    fwd = client.post("/api/v1/delta-v", json=fwd_body)
    assert fwd.status_code == 200
    forward_net = fwd.json()["net_delta_v"]

    # 前向接口净值 == 反解宣称净值 == 逐级之和减损失
    assert forward_net == pytest.approx(
        solved["achieved_net_delta_v"], abs=1e-9
    )
    assert abs(forward_net - problem.target_delta_v) <= problem.tolerance + 1e-9
    # 回传偏差字段与重新计算一致
    assert solved["deviation"] == pytest.approx(
        forward_net - problem.target_delta_v, abs=1e-9
    )


def test_http_roundtrip_reference_configuration(client):
    """以内置算例构型的净 Δv 为目标：解回前向必须精确对上该净 Δv。"""
    fwd = client.post(
        "/api/v1/delta-v",
        json={
            "stages": [
                {"ve": 3200.0, "structural_mass": 2000.0,
                 "propellant_mass": 8000.0, "burn_time": 120.0},
                {"ve": 3200.0, "structural_mass": 2000.0,
                 "propellant_mass": 8000.0, "burn_time": 80.0},
            ],
            "payload_mass": 2000.0,
        },
    ).json()
    target = fwd["net_delta_v"]

    solved = client.post(
        "/api/v1/inverse/solve",
        json={
            "target_delta_v": target,
            "payload_mass": 2000.0,
            "tolerance": 0.1,
            "stages": [
                {"ve": 3200.0, "structural_mass": 2000.0, "burn_time": 120.0,
                 "propellant_min": 1.0, "propellant_max": 20000.0},
                {"ve": 3200.0, "structural_mass": 2000.0, "burn_time": 80.0,
                 "propellant_min": 1.0, "propellant_max": 20000.0},
            ],
        },
    ).json()
    assert solved["status"] == "solved"

    back = client.post(
        "/api/v1/delta-v",
        json={
            "stages": [
                {"ve": 3200.0, "structural_mass": 2000.0,
                 "propellant_mass": solved["stages"][0]["propellant_mass"],
                 "burn_time": 120.0},
                {"ve": 3200.0, "structural_mass": 2000.0,
                 "propellant_mass": solved["stages"][1]["propellant_mass"],
                 "burn_time": 80.0},
            ],
            "payload_mass": 2000.0,
        },
    ).json()
    assert back["net_delta_v"] == pytest.approx(
        solved["achieved_net_delta_v"], abs=1e-9
    )
    assert abs(back["net_delta_v"] - target) <= 0.1
    # 最省：总量严格少于原对称构型的 16000 kg
    assert solved["total_propellant_mass"] < 16000.0
