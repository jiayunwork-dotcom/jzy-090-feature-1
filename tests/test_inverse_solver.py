"""反解求解器内核测试（不经 HTTP，直接调用 solver）。

覆盖：
* 单级解析解对照：x* = mf·(exp((目标+重力损失)/ve) − 1)；
* 结局一返回逐级质量比/Δv、达成净值、偏差、迭代轮数；
* 总推进剂最省：两级同 ve 时解出的总量不超过已知可行的对称分配；
* 各级加注量落在上下限、总量不超预算（含预算绑定）；
* 零下限下低 ve 级分到 0 推进剂；
* 全下限构型已在容差带时直接返回下限（最省特例）；
* 结局二的四类不可行诊断（级上限/预算/双重卡死/下限超速/预算低于下限）。
"""

from __future__ import annotations

import math

import pytest

from app.core.constants import STANDARD_GRAVITY
from app.core.engine import StageInput, evaluate
from app.core.losses import DragSpec
from app.solver.inverse import (
    BINDING_BUDGET,
    BINDING_BUDGET_AND_STAGE_MAX,
    BINDING_STAGE_MAX,
    BINDING_STAGE_MIN,
    DEFAULT_TOLERANCE,
    REASON_BUDGET_BELOW_MINIMUM,
    REASON_TARGET_OVERSHOT_AT_MINIMUM,
    REASON_TARGET_UNREACHABLE,
    InverseProblem,
    InverseSolution,
    InverseStageSpec,
    solve_inverse,
)

# 与前向内核同一重力损失口径
G = STANDARD_GRAVITY


def spec(ve=3200.0, ms=2000.0, bt=100.0, pmin=0.0, pmax=30000.0):
    return InverseStageSpec(ve, ms, bt, pmin, pmax)


# ---------------------------------------------------------------------------
# 结局一：可达且最省
# ---------------------------------------------------------------------------


def test_single_stage_matches_closed_form():
    """单级无阻力：净Δv = ve·ln(1 + x/mf) − g·t，反解应对上闭式解。"""
    ve, ms, payload, bt = 3200.0, 1500.0, 800.0, 120.0
    mf = ms + payload
    gravity = G * bt
    tol = 1e-3
    target = 4000.0
    x_exact = mf * (math.exp((target + gravity) / ve) - 1.0)

    out = solve_inverse(
        InverseProblem(
            target_delta_v=target,
            payload_mass=payload,
            stages=[spec(ve=ve, ms=ms, bt=bt, pmax=50000.0)],
            propellant_budget=None,
            tolerance=tol,
            drag=DragSpec(),
        )
    )
    assert isinstance(out, InverseSolution)
    x = out.propellant_masses[0]
    # 落点在容差带下沿：总量不超过闭式解，但偏离不到"把一个容差补满"
    # 所需的推进剂（边际 ≈ mf/(mf+x)·ve 的倒数）
    assert x <= x_exact + 1e-6
    marginal = ve / (mf + x_exact)
    assert x >= x_exact - tol / marginal - 1e-6

    # 回代前向内核（求解器用的就是它，这里显式再算一遍锁死自洽）
    forward = evaluate(
        [StageInput(ve, ms, x, bt)], payload, DragSpec()
    )
    assert forward.net_delta_v == pytest.approx(
        out.evaluation.net_delta_v, abs=1e-9
    )
    assert abs(forward.net_delta_v - target) <= tol + 1e-9 * max(1.0, target)

    # 逐级结果齐备
    st = out.evaluation.stages[0]
    assert st.mass_ratio == pytest.approx((mf + x) / mf)
    assert st.ideal_delta_v == pytest.approx(ve * math.log((mf + x) / mf))
    assert isinstance(out.iterations, int) and out.iterations >= 1


def test_two_stage_solution_is_minimal_and_balanced():
    """两级同 ve：最省解把自由级的边际 dΔv/dx 调成相等（water-filling）。"""
    target = 4265.582476977002  # 内置算例 (8000, 8000) 的净 Δv
    stages = [
        spec(ve=3200.0, ms=2000.0, bt=120.0, pmax=20000.0),
        spec(ve=3200.0, ms=2000.0, bt=80.0, pmax=20000.0),
    ]
    out = solve_inverse(
        InverseProblem(
            target_delta_v=target,
            payload_mass=2000.0,
            stages=stages,
            propellant_budget=None,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert isinstance(out, InverseSolution)
    x = out.propellant_masses
    total = math.fsum(x)
    # 已知可行对称分配 (8000, 8000) 总量 16000；最省解必须严格更少
    assert total < 16000.0
    assert abs(out.evaluation.net_delta_v - target) <= 1.0

    # 两个自由级都不在加注边界上：边际 d(净Δv)/dx_i = ve_i/m0_i 相等
    chain = out.evaluation.stages
    marginals = [stages[i].ve / chain[i].m0 for i in range(2)]
    assert marginals[0] == pytest.approx(marginals[1], rel=1e-6)

    # 总推进剂与质量链上 Σ(m0−mf) 严格相等（同一口径）
    assert total == pytest.approx(
        math.fsum(s.m0 - s.mf for s in chain)
    )


def test_zero_floor_low_ve_stage_gets_no_propellant():
    """下限为 0 且 ve 明显更低的级在最优分配中应分到 0（一分钱不浪费）。"""
    out = solve_inverse(
        InverseProblem(
            target_delta_v=3000.0,
            payload_mass=500.0,
            stages=[
                spec(ve=3200.0, ms=1000.0, bt=100.0, pmax=50000.0),
                spec(ve=400.0, ms=1000.0, bt=80.0, pmax=50000.0),
            ],
            propellant_budget=None,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert isinstance(out, InverseSolution)
    assert out.propellant_masses[1] == 0.0
    assert out.propellant_masses[0] > 0.0
    assert abs(out.evaluation.net_delta_v - 3000.0) <= 1.0


def test_all_minimum_solution_when_band_already_met():
    """全下限构型已落入容差带：它就是最省解，直接原样返回。"""
    out = solve_inverse(
        InverseProblem(
            # 下限构型净Δv ≈ 39.3 m/s，容差带 [0, 100] 已包含它
            target_delta_v=50.0,
            payload_mass=500.0,
            stages=[spec(ve=3000.0, ms=1000.0, bt=10.0, pmin=50.0, pmax=5000.0)],
            propellant_budget=None,
            tolerance=50.0,
            drag=DragSpec(),
        )
    )
    assert isinstance(out, InverseSolution)
    assert out.propellant_masses == [50.0]
    assert abs(out.evaluation.net_delta_v - 50.0) <= 50.0


def test_stage_limits_respected():
    """非零下界/上界都被遵守：下界高时解贴在下界上。"""
    stages = [
        spec(ve=3200.0, ms=1000.0, bt=100.0, pmin=3000.0, pmax=30000.0),
        spec(ve=3200.0, ms=1000.0, bt=80.0, pmin=3000.0, pmax=30000.0),
    ]
    out = solve_inverse(
        InverseProblem(
            # 全下限构型净Δv ≈ 4273.4 m/s；取 4300±50，下限本身已进带
            target_delta_v=4300.0,
            payload_mass=500.0,
            stages=stages,
            propellant_budget=None,
            tolerance=50.0,
            drag=DragSpec(),
        )
    )
    assert isinstance(out, InverseSolution)
    for s, x in zip(stages, out.propellant_masses):
        assert s.propellant_min - 1e-9 <= x <= s.propellant_max + 1e-9
    # 两个级都贴着加注下限 3000 kg（下限本身已足够达到目标容差带）
    assert out.propellant_masses == pytest.approx([3000.0, 3000.0])


def test_budget_binding_solution_within_budget():
    """紧预算下：解必须可行（进带）且总量不超预算。

    现有质量链下，命中某个净Δv 的最省总量是确定点；预算真正绑定的
    可行窗口只在"命中容差带下沿所需总量"附近。动态构造：先取无预算
    最优总量，再把预算压到它命中下沿所需的位置，验证总量贴边、净值
    进带、且两个自由级边际相等（保总量腾挪的 KKT 条件）。
    """
    stages = [
        spec(ve=3000.0, ms=1000.0, bt=100.0, pmax=50000.0),
        spec(ve=3400.0, ms=1000.0, bt=80.0, pmax=50000.0),
    ]
    tol = 5.0
    free = solve_inverse(
        InverseProblem(5000.0, 500.0, stages, None, tol, DragSpec())
    )
    assert isinstance(free, InverseSolution)
    # 无预算最优命中下沿时的总量；把预算压到该总量即绑定
    binding_budget = math.fsum(free.propellant_masses)

    out = solve_inverse(
        InverseProblem(
            5000.0, 500.0, stages, binding_budget, tol, DragSpec()
        )
    )
    assert isinstance(out, InverseSolution)
    total = math.fsum(out.propellant_masses)
    assert total <= binding_budget + 1e-9
    assert abs(out.evaluation.net_delta_v - 5000.0) <= tol
    # 预算贴边（无预算最优总量就是预算本身）
    assert total == pytest.approx(binding_budget, abs=1e-6)
    # 两个自由级边际相等（保总量腾挪的 KKT 条件）
    m0 = [s.m0 for s in out.evaluation.stages]
    assert 3000.0 / m0[0] == pytest.approx(3400.0 / m0[1], rel=1e-6)

    # 预算再收紧 20 kg 即不可行（绑定的另一侧证据）
    short = solve_inverse(
        InverseProblem(
            5000.0, 500.0, stages, binding_budget - 20.0, tol, DragSpec()
        )
    )
    assert not isinstance(short, InverseSolution)
    assert short.reason_code == REASON_TARGET_UNREACHABLE
    assert short.binding_constraint == BINDING_BUDGET


def test_default_tolerance_used_when_not_given():
    from app.services.inverse import solve_inverse_request

    body = {
        "target_delta_v": 4000.0,
        "payload_mass": 1000.0,
        "stages": [
            {
                "ve": 3200.0,
                "structural_mass": 2000.0,
                "burn_time": 100.0,
                "propellant_min": 0.0,
                "propellant_max": 30000.0,
            }
        ],
    }
    resp = solve_inverse_request(body)
    assert resp["status"] == "solved"
    assert resp["tolerance"] == DEFAULT_TOLERANCE
    assert abs(resp["achieved_net_delta_v"] - 4000.0) <= DEFAULT_TOLERANCE


# ---------------------------------------------------------------------------
# 结局二：不可行诊断
# ---------------------------------------------------------------------------


def test_infeasible_stage_max_binding():
    out = solve_inverse(
        InverseProblem(
            target_delta_v=9000.0,
            payload_mass=500.0,
            stages=[
                spec(ve=3000.0, ms=1000.0, bt=100.0, pmax=2000.0),
                spec(ve=3000.0, ms=1000.0, bt=80.0, pmax=2000.0),
            ],
            propellant_budget=None,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert out.reason_code == REASON_TARGET_UNREACHABLE
    assert out.binding_constraint == BINDING_STAGE_MAX
    assert out.binding_stage_indices == [0, 1]
    # 机器可读结论里给出了"最满能到多少"，绝不假装成功
    assert out.max_achievable_net_delta_v < 9000.0 - 1.0
    assert out.detail


def test_infeasible_budget_binding():
    out = solve_inverse(
        InverseProblem(
            target_delta_v=9000.0,
            payload_mass=500.0,
            stages=[
                spec(ve=3000.0, ms=1000.0, bt=100.0, pmax=50000.0),
                spec(ve=3000.0, ms=1000.0, bt=80.0, pmax=50000.0),
            ],
            propellant_budget=4000.0,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert out.reason_code == REASON_TARGET_UNREACHABLE
    assert out.binding_constraint == BINDING_BUDGET
    assert out.binding_stage_indices == []
    assert out.max_achievable_net_delta_v < 9000.0 - 1.0


def test_infeasible_budget_and_stage_max_binding():
    out = solve_inverse(
        InverseProblem(
            target_delta_v=9000.0,
            payload_mass=500.0,
            stages=[
                spec(ve=3000.0, ms=1000.0, bt=100.0, pmax=1000.0),
                spec(ve=3000.0, ms=1000.0, bt=80.0, pmax=50000.0),
            ],
            propellant_budget=3000.0,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert out.reason_code == REASON_TARGET_UNREACHABLE
    assert out.binding_constraint == BINDING_BUDGET_AND_STAGE_MAX
    assert 0 in out.binding_stage_indices


def test_infeasible_budget_below_minimum():
    out = solve_inverse(
        InverseProblem(
            target_delta_v=500.0,
            payload_mass=500.0,
            stages=[spec(pmin=3000.0, pmax=8000.0)],
            propellant_budget=2000.0,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert out.reason_code == REASON_BUDGET_BELOW_MINIMUM
    assert out.binding_constraint == BINDING_BUDGET
    assert out.max_achievable_net_delta_v is None


def test_infeasible_overshoot_at_minimum():
    """加注下限已经把速度顶得超过目标容差上沿：被下限卡死。"""
    out = solve_inverse(
        InverseProblem(
            target_delta_v=100.0,
            payload_mass=500.0,
            stages=[spec(ve=3000.0, ms=1000.0, bt=100.0, pmin=5000.0, pmax=8000.0)],
            propellant_budget=None,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert out.reason_code == REASON_TARGET_OVERSHOT_AT_MINIMUM
    assert out.binding_constraint == BINDING_STAGE_MIN
    assert out.min_achievable_net_delta_v > 101.0


def test_achievable_upper_bound_is_honest():
    """不可行结论给出的"最大可达净Δv"必须等于独立前向核算（灌到上限）
    的结果，不允许报一个夸大或缩小的数。"""
    stages = [
        spec(ve=2800.0, ms=1200.0, bt=100.0, pmax=1500.0),
        spec(ve=3100.0, ms=900.0, bt=70.0, pmax=1200.0),
    ]
    out = solve_inverse(
        InverseProblem(
            target_delta_v=9000.0,
            payload_mass=600.0,
            stages=stages,
            propellant_budget=None,
            tolerance=1.0,
            drag=DragSpec(),
        )
    )
    assert out.reason_code == REASON_TARGET_UNREACHABLE
    independent = evaluate(
        [
            StageInput(2800.0, 1200.0, 1500.0, 100.0),
            StageInput(3100.0, 900.0, 1200.0, 70.0),
        ],
        600.0,
        DragSpec(),
    ).net_delta_v
    assert out.max_achievable_net_delta_v == pytest.approx(
        independent, abs=1e-6
    )
