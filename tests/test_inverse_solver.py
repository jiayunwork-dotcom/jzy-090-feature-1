"""目标反推求解器：往返一致、解析对拍、最省性、三种结局与物理单调性。

这些测试锁的是"反解真的在解物理问题"，而不是拼一个碰巧过关的数：

* 往返一致（命根子）：随机/枚举多组目标与约束，解一遍 -> 把推进剂原样
  组装回普通构型喂回前向内核，两侧净 Δv 必须落在同一容差里；
* 单级情形有闭式解，反解必须与闭式解吻合；
* 最省性：解的邻域网格里不许存在更省的可行分配；
* 物理单调性：目标调高总用量不减；某级 ve 调高总用量不增；放宽预算
  原本可行的目标不会变不可行。
"""

from __future__ import annotations

import itertools
import math
import random

import pytest

from app.core.engine import StageInput, evaluate
from app.core.losses import DragSpec
from app.services.inverse_solver import (
    INFEASIBLE,
    SOLVED,
    InverseProblem,
    InverseStageSpec,
    solve_inverse,
)

G = 9.80665


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def spec(ve=3200.0, ms=2000.0, bt=100.0, pmin=1.0, pmax=20000.0):
    return InverseStageSpec(ve, ms, bt, pmin, pmax)


def forward_net(sol, payload: float) -> float:
    """把反解结果原样组装成普通构型，喂回前向内核核算。"""
    stages = [
        StageInput(
            ve=s.ve,
            structural_mass=s.structural_mass,
            propellant_mass=s.propellant_mass,
            burn_time=s.burn_time,
        )
        for s in sol.stages
    ]
    return evaluate(stages, payload, DragSpec()).net_delta_v


def feasible_span(problem: InverseProblem) -> tuple[float, float]:
    """该问题在下限点/上限点的净 Δv，用于挑一个可行目标。"""
    lo = [s.propellant_min for s in problem.stages]
    hi = [s.propellant_max for s in problem.stages]
    f_lo = evaluate(
        [StageInput(s.ve, s.structural_mass, lo[i], s.burn_time)
         for i, s in enumerate(problem.stages)],
        problem.payload_mass, DragSpec(),
    ).net_delta_v
    f_hi = evaluate(
        [StageInput(s.ve, s.structural_mass, hi[i], s.burn_time)
         for i, s in enumerate(problem.stages)],
        problem.payload_mass, DragSpec(),
    ).net_delta_v
    return f_lo, f_hi


# ---------------------------------------------------------------------------
# 基础结局与字段
# ---------------------------------------------------------------------------


def test_solved_basic_two_stage():
    prob = InverseProblem(
        4000.0, 2000.0, [spec(bt=120.0), spec(bt=80.0)], tolerance=1e-6
    )
    sol = solve_inverse(prob)
    assert sol.status == SOLVED
    assert sol.within_tolerance is True
    assert abs(sol.deviation) <= 1e-6
    assert sol.sweeps >= 1
    # 逐级结果齐全：加注量、上下限、质量比、逐级 Δv
    assert len(sol.stages) == 2
    for s in sol.stages:
        assert s.propellant_min <= s.propellant_mass <= s.propellant_max
        assert s.mass_ratio == pytest.approx(s.m0 / s.mf)
        assert s.ideal_delta_v == pytest.approx(s.ve * math.log(s.mass_ratio))
        assert s.m0 > s.mf > 0


def test_solution_propellant_within_bounds_and_budget():
    prob = InverseProblem(
        3500.0, 1000.0,
        [spec(bt=120.0, pmin=500.0, pmax=9000.0),
         spec(ve=3000.0, ms=1200.0, bt=70.0, pmin=300.0, pmax=7000.0)],
        tolerance=1e-6, propellant_budget=12000.0,
    )
    sol = solve_inverse(prob)
    assert sol.status == SOLVED
    for s in sol.stages:
        assert s.propellant_min - 1e-9 <= s.propellant_mass <= s.propellant_max + 1e-9
    assert sol.total_propellant <= 12000.0 + 1e-6


# ---------------------------------------------------------------------------
# 往返一致：随机 + 枚举，这是命根子
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,tol",
    [(t, 1e-4) for t in (500.0, 1500.0, 3000.0, 4200.0, 5300.0)]
    + [(2500.0, 1.0), (2500.0, 1e-8)],
)
def test_roundtrip_consistency_enumerated(target, tol):
    prob = InverseProblem(
        target, 2000.0,
        [spec(ve=3200.0, ms=2000.0, bt=120.0),
         spec(ve=3000.0, ms=1500.0, bt=80.0)],
        tolerance=tol,
    )
    sol = solve_inverse(prob)
    assert sol.status == SOLVED
    # 解回代前向：净 Δv 必须与求解器宣称的达成值严格一致（同一内核的同一结果）
    assert forward_net(sol, 2000.0) == pytest.approx(
        sol.achieved_net_delta_v, abs=1e-10
    )
    # 且与目标的偏差落在请求给定的容差带内
    assert abs(forward_net(sol, 2000.0) - target) <= tol + 1e-9


def test_roundtrip_consistency_random():
    rng = random.Random(20260926)
    for case in range(40):
        n = rng.randint(1, 4)
        stages = [
            spec(
                ve=rng.uniform(2200.0, 4500.0),
                ms=rng.uniform(200.0, 3000.0),
                bt=rng.uniform(10.0, 150.0),
                pmin=rng.uniform(1.0, 500.0),
                pmax=rng.uniform(5000.0, 40000.0),
            )
            for _ in range(n)
        ]
        payload = rng.uniform(0.0, 5000.0)
        prob0 = InverseProblem(0.0, payload, stages, tolerance=1e-7)
        f_lo, f_hi = feasible_span(prob0)
        if f_hi <= f_lo + 100.0:
            continue
        # 预算用例分两类：约一半给一个可能偏紧的预算（允许出现不可行，
        # 此时只校验不可行判定的自洽）；另一半给足预算，必须解出。
        budget = None
        tight_maybe_infeasible = False
        if case % 2 == 0:
            lo_total = sum(s.propellant_min for s in stages)
            hi_total = sum(s.propellant_max for s in stages)
            if case % 4 == 0:
                # 偏紧预算：在 [lo 总量, hi 总量] 的 55%~90% 处取，可能不可行
                budget = lo_total + rng.uniform(0.55, 0.9) * (hi_total - lo_total)
                tight_maybe_infeasible = True
            else:
                budget = rng.uniform(0.98 * hi_total, hi_total)
        target = rng.uniform(f_lo + 50.0, f_hi - 50.0)
        tol = rng.choice([1e-2, 1e-4, 1e-6])
        sol = solve_inverse(InverseProblem(target, payload, stages,
                                           tolerance=tol,
                                           propellant_budget=budget))
        if tight_maybe_infeasible and sol.status == "infeasible":
            # 不可行判定也必须自洽：尽力点不超预算、偏差确实在容差外
            assert sol.limiting_constraint in ("budget", "propellant_cap")
            assert sol.total_propellant <= budget + 1e-7 * max(1.0, budget)
            assert sol.achieved_net_delta_v + tol < target
            continue
        assert sol.status == SOLVED, (case, target, f_lo, f_hi, budget)
        # 回代前向，净 Δv 与宣称值一致、与目标偏差在容差内
        net = forward_net(sol, payload)
        assert net == pytest.approx(sol.achieved_net_delta_v, abs=1e-9, rel=1e-12)
        assert abs(net - target) <= tol + 1e-7
        # 约束全部满足
        for s in sol.stages:
            assert s.propellant_min - 1e-7 <= s.propellant_mass <= s.propellant_max + 1e-7
        if budget is not None:
            assert sol.total_propellant <= budget + 1e-7 * max(1.0, budget)


# ---------------------------------------------------------------------------
# 单级闭式解对拍
# ---------------------------------------------------------------------------


def test_single_stage_matches_closed_form():
    ve, ms, bt, payload, target = 3000.0, 1500.0, 90.0, 800.0, 2500.0
    # net = ve*ln((ms+mp+payload)/(ms+payload)) - g*t
    expected = (ms + payload) * math.exp((target + G * bt) / ve) - (ms + payload)
    sol = solve_inverse(
        InverseProblem(target, payload, [spec(ve, ms, bt, 1.0, 1.0e7)],
                       tolerance=1e-9)
    )
    assert sol.status == SOLVED
    assert sol.stages[0].propellant_mass == pytest.approx(expected, rel=1e-8)
    assert forward_net(sol, payload) == pytest.approx(target, abs=1e-8)


# ---------------------------------------------------------------------------
# 最省性：邻域网格里不应存在更省的可行分配
# ---------------------------------------------------------------------------


def test_solution_is_locally_minimal_two_stage():
    prob = InverseProblem(
        4000.0, 2000.0, [spec(bt=120.0), spec(bt=80.0)], tolerance=1e-7
    )
    sol = solve_inverse(prob)
    x = [s.propellant_mass for s in sol.stages]
    static = [(3200.0, 2000.0, 120.0), (3200.0, 2000.0, 80.0)]
    best = math.inf
    for d in itertools.product((-200.0, -50.0, -10.0, 10.0, 50.0, 200.0), repeat=2):
        trial = [x[i] + d[i] for i in range(2)]
        if any(v < 1.0 for v in trial):
            continue
        res = evaluate(
            [StageInput(static[i][0], static[i][1], trial[i], static[i][2])
             for i in range(2)],
            2000.0, DragSpec(),
        )
        if res.net_delta_v >= 4000.0:
            best = min(best, math.fsum(trial))
    # 网格（步长 10 kg）找不到比求解结果更省 10 kg 以上的可行点
    assert best >= sol.total_propellant - 10.0


def test_first_order_optimality_along_feasible_arc():
    # 与质量链具体形式无关的 KKT 一阶条件检验：从解点出发，把上面级的
    # 加注量挪走任意一小份、再给下面级补到重新达标，总用量只增不减。
    # （等边际的内部解或某级顶在隐式边界的边界解都必须满足这一条。）
    prob = InverseProblem(
        4000.0, 2000.0, [spec(bt=120.0), spec(bt=80.0)], tolerance=1e-9
    )
    sol = solve_inverse(prob)
    x0, x1 = (s.propellant_mass for s in
              sorted(sol.stages, key=lambda s: s.index))
    static = [(3200.0, 2000.0, 120.0), (3200.0, 2000.0, 80.0)]

    def net(a, b):
        return evaluate(
            [StageInput(*static[0][:2], a, static[0][2]),
             StageInput(*static[1][:2], b, static[1][2])],
            2000.0, DragSpec(),
        ).net_delta_v

    for dx1 in (-1.0, -10.0, -100.0, -1000.0):
        lo, hi = x0, x0 + 1.0e5
        assert net(hi, x1 + dx1) >= 4000.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if net(mid, x1 + dx1) >= 4000.0:
                hi = mid
            else:
                lo = mid
        assert hi + (x1 + dx1) >= sol.total_propellant - 1e-4


# ---------------------------------------------------------------------------
# 三种结局：不可行 + 卡死侧识别
# ---------------------------------------------------------------------------


def test_infeasible_due_to_propellant_cap():
    prob = InverseProblem(
        100000.0, 2000.0, [spec(bt=120.0, pmin=100.0, pmax=5000.0)],
        tolerance=1e-3,
    )
    sol = solve_inverse(prob)
    assert sol.status == INFEASIBLE
    assert sol.limiting_constraint == "propellant_cap"
    assert sol.stages_at_max == [0]
    assert sol.budget_exhausted is False
    # 尽力结果如实给出，且确实差得离谱而不是假装成功
    assert sol.achieved_net_delta_v + sol.tolerance < 100000.0
    assert all(s.propellant_mass == pytest.approx(s.propellant_max)
               for s in sol.stages)


def test_infeasible_due_to_budget():
    prob = InverseProblem(
        6000.0, 2000.0,
        [spec(bt=120.0, pmin=100.0, pmax=50000.0)],
        tolerance=1e-3, propellant_budget=3000.0,
    )
    sol = solve_inverse(prob)
    assert sol.status == INFEASIBLE
    assert sol.limiting_constraint == "budget"
    assert sol.budget_exhausted is True
    assert sol.total_propellant == pytest.approx(3000.0, abs=1e-6)
    # 上限明明远高于预算，所以没有任何级顶到自身加注上限
    assert sol.stages_at_max == []


def test_trivial_target_zero_when_lower_bounds_suffice():
    # 工作时间很短时，下限点净 Δv 已为正；零目标直接返回下限点
    prob = InverseProblem(
        0.0, 100.0, [spec(ms=100.0, bt=1.0, pmin=50.0, pmax=9000.0)],
        tolerance=1e-9,
    )
    f_lo, _ = feasible_span(prob)
    if f_lo < 0:
        pytest.skip("该参数下限点不达标，平凡路径不适用")
    sol = solve_inverse(prob)
    assert sol.status == SOLVED
    assert sol.sweeps == 0
    assert sol.stages[0].propellant_mass == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 物理单调性（独立测试盯住，防止数值迭代跑偏）
# ---------------------------------------------------------------------------


def test_monotone_higher_target_needs_no_less_propellant():
    stages = [spec(bt=120.0), spec(ve=3000.0, ms=1500.0, bt=80.0)]
    totals = []
    for target in (2000.0, 2600.0, 3200.0, 3800.0, 4400.0):
        sol = solve_inverse(InverseProblem(target, 2000.0, stages, tolerance=1e-7))
        assert sol.status == SOLVED
        totals.append(sol.total_propellant)
    for a, b in zip(totals, totals[1:]):
        assert b >= a - 1e-6


def test_monotone_higher_ve_needs_no_more_propellant():
    totals = []
    for ve in (2600.0, 3000.0, 3400.0, 4000.0, 4600.0):
        stages = [spec(bt=120.0), spec(ve=ve, ms=1500.0, bt=80.0)]
        sol = solve_inverse(InverseProblem(3500.0, 2000.0, stages, tolerance=1e-7))
        assert sol.status == SOLVED
        totals.append(sol.total_propellant)
    for a, b in zip(totals, totals[1:]):
        assert b <= a + 1e-6


def test_monotone_relaxing_budget_never_turns_feasible_infeasible():
    stages = [spec(bt=120.0, pmax=40000.0), spec(bt=80.0, pmax=40000.0)]
    # 先找到一个在紧预算下恰好可行的目标
    tight = solve_inverse(
        InverseProblem(4200.0, 2000.0, stages, tolerance=1e-6,
                       propellant_budget=16000.0)
    )
    assert tight.status == SOLVED
    for relaxed in (16001.0, 20000.0, 40000.0, 80000.0, None):
        sol = solve_inverse(
            InverseProblem(4200.0, 2000.0, stages, tolerance=1e-6,
                           propellant_budget=relaxed)
        )
        assert sol.status == SOLVED


def test_more_budget_cannot_lower_max_achievable_delta_v():
    # 同一目标下，放宽预算得到的解总用量不应被迫超过紧预算解的用量
    stages = [spec(bt=120.0, pmax=40000.0), spec(bt=80.0, pmax=40000.0)]
    tight = solve_inverse(
        InverseProblem(3800.0, 2000.0, stages, tolerance=1e-7,
                       propellant_budget=15000.0)
    )
    loose = solve_inverse(
        InverseProblem(3800.0, 2000.0, stages, tolerance=1e-7,
                       propellant_budget=30000.0)
    )
    assert tight.status == loose.status == SOLVED
    assert loose.total_propellant <= tight.total_propellant + 1e-6
