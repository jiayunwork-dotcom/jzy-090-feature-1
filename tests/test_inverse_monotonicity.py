"""物理单调性硬标准（独立测试盯住，防止数值迭代悄悄跑偏）。

* 目标净速度增量调高（其它不变）→ 解出的总推进剂用量不得变少；
* 某一级排气速度调高（这一级出力更省）→ 达成同一目标所需总推进剂
  不得变多；
* 推进剂预算放宽 → 原本可行的目标不得突然变成不可行，且总用量不得
  变多。

这些比较使用小容差（0.01 m/s）并按"一个容差对应的推进剂量"留出数值
余量，避免容差带本身造成假违反。
"""

from __future__ import annotations

import math
import pathlib

import pytest

from app.core.engine import StageInput, evaluate
from app.core.losses import DragSpec
from app.solver.inverse import (
    InverseProblem,
    InverseSolution,
    InverseStageSpec,
    solve_inverse,
)

TOL = 0.01  # 小容差，让单调性比较贴近真正的等净Δv 面


def _feasible_span(problem_spec, payload, drag):
    """返回 (全下限净值, 全上限净值)，用于挑选保证可行的目标。"""
    def net(prop):
        return evaluate(
            [
                StageInput(s.ve, s.structural_mass, x, s.burn_time)
                for s, x in zip(problem_spec, prop)
            ],
            payload,
            drag,
        ).net_delta_v

    mins = [s.propellant_min for s in problem_spec]
    maxs = [s.propellant_max for s in problem_spec]
    return net(mins), net(maxs), net


def _solve_total(problem_spec, payload, target, budget=None, drag=DragSpec()):
    out = solve_inverse(
        InverseProblem(target, payload, list(problem_spec), budget, TOL, drag)
    )
    assert isinstance(out, InverseSolution), (
        f"本测试只比较可行解，意外不可行：{out}"
    )
    return math.fsum(out.propellant_masses)


@pytest.mark.parametrize(
    "stages,payload",
    [
        (
            [
                InverseStageSpec(3200.0, 2000.0, 120.0, 1.0, 30000.0),
                InverseStageSpec(3600.0, 1500.0, 80.0, 1.0, 30000.0),
            ],
            1000.0,
        ),
        (
            [
                InverseStageSpec(2800.0, 800.0, 90.0, 1.0, 25000.0),
                InverseStageSpec(3100.0, 600.0, 70.0, 1.0, 25000.0),
                InverseStageSpec(3900.0, 400.0, 50.0, 1.0, 25000.0),
            ],
            300.0,
        ),
        # 单级
        (
            [InverseStageSpec(3000.0, 1200.0, 100.0, 1.0, 40000.0)],
            800.0,
        ),
    ],
)
def test_higher_target_never_uses_less_propellant(stages, payload):
    fmin, fmax, _ = _feasible_span(stages, payload, DragSpec())
    targets = [
        fmin + frac * (fmax - fmin)
        for frac in (0.1, 0.25, 0.4, 0.55, 0.7, 0.85)
    ]
    totals = [_solve_total(stages, payload, t) for t in targets]
    for lo, hi in zip(totals, totals[1:]):
        assert hi >= lo - 1.0, (
            f"目标 {targets} 升高，总推进剂却 {totals} 下降"
        )


def test_higher_target_monotone_with_linear_drag():
    """线性阻力不改变单调性（净Δv 对理想Δv 仍是正线性传导）。"""
    drag = DragSpec(mode="linear", fraction=0.02)
    stages = [
        InverseStageSpec(3200.0, 2000.0, 120.0, 1.0, 30000.0),
        InverseStageSpec(3500.0, 1500.0, 80.0, 1.0, 30000.0),
    ]
    fmin, fmax, _ = _feasible_span(stages, 1000.0, drag)
    targets = [fmin + frac * (fmax - fmin) for frac in (0.1, 0.3, 0.5, 0.7)]
    totals = [
        _solve_total(stages, 1000.0, t, drag=drag) for t in targets
    ]
    for lo, hi in zip(totals, totals[1:]):
        assert hi >= lo - 1.0


@pytest.mark.parametrize("stage_index", [0, 1])
def test_higher_exhaust_velocity_never_needs_more_propellant(stage_index):
    payload = 1000.0
    base = [
        InverseStageSpec(3200.0, 2000.0, 120.0, 1.0, 30000.0),
        InverseStageSpec(3300.0, 1500.0, 80.0, 1.0, 30000.0),
    ]
    bumped = [
        InverseStageSpec(
            (s.ve * 1.15) if i == stage_index else s.ve,
            s.structural_mass,
            s.burn_time,
            s.propellant_min,
            s.propellant_max,
        )
        for i, s in enumerate(base)
    ]
    fmin, fmax, _ = _feasible_span(base, payload, DragSpec())
    for frac in (0.2, 0.5, 0.8):
        target = fmin + frac * (fmax - fmin)
        total_before = _solve_total(base, payload, target)
        total_after = _solve_total(bumped, payload, target)
        assert total_after <= total_before + 1.0, (
            f"第 {stage_index} 级 ve 调高 15%，达成同一目标的总推进剂反而增加："
            f"{total_before} -> {total_after}（目标 {target}）"
        )


def test_relaxing_budget_never_breaks_feasibility():
    payload = 500.0
    stages = [
        InverseStageSpec(3000.0, 1000.0, 100.0, 1.0, 50000.0),
        InverseStageSpec(3400.0, 1000.0, 80.0, 1.0, 50000.0),
    ]
    target = 5000.0

    def run(budget):
        return solve_inverse(
            InverseProblem(target, payload, stages, budget, TOL, DragSpec())
        )

    # 先找到一个"恰好可行但预算绑定"的紧预算
    tight = run(9000.0)
    assert isinstance(tight, InverseSolution), tight
    assert math.fsum(tight.propellant_masses) <= 9000.0 + 1e-9

    relaxed = run(20000.0)
    unlimited = run(None)
    for later in (relaxed, unlimited):
        assert isinstance(later, InverseSolution), (
            "预算放宽后，原本可行的目标突然不可行"
        )
        # 放宽约束不可能让最省用量变多
        assert math.fsum(later.propellant_masses) <= (
            math.fsum(tight.propellant_masses) + 1.0
        )


def test_budget_relaxation_monotone_chain():
    """预算从紧到松逐级放宽：可行性与总用量都单调。"""
    payload = 500.0
    stages = [
        InverseStageSpec(3000.0, 1000.0, 100.0, 1.0, 60000.0),
        InverseStageSpec(3400.0, 1000.0, 80.0, 1.0, 60000.0),
    ]
    target = 5200.0
    totals: list[float] = []
    for budget in (8500.0, 10000.0, 13000.0, 18000.0):
        out = solve_inverse(
            InverseProblem(target, payload, stages, budget, TOL, DragSpec())
        )
        assert isinstance(out, InverseSolution), (
            f"预算 {budget} 应可行，却得到 {out}"
        )
        totals.append(math.fsum(out.propellant_masses))
    for tight, loose in zip(totals, totals[1:]):
        assert loose <= tight + 1.0


def test_dependency_direction_core_never_imports_solver():
    """依赖方向锁死：前向内核/批量/校验不得反向依赖求解器。"""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    forbidden_dirs = ["core", "api"]
    forbidden_files = [
        root / "services" / "calculator.py",
        root / "services" / "validator.py",
        root / "services" / "reference.py",
    ]
    checked = []
    for d in forbidden_dirs:
        checked.extend((root / d).glob("*.py"))
    checked.extend(forbidden_files)
    for path in checked:
        text = path.read_text(encoding="utf-8")
        assert "app.solver" not in text and "from app.solver" not in text, (
            f"{path} 反向依赖了求解模块，破坏了依赖方向"
        )
