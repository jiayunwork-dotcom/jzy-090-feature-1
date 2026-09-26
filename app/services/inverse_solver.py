"""目标反推求解器：给定目标净速度增量，反解各级推进剂加注量。

求解方向与前向核算相反
----------------------
前向链路（``app.core.engine.evaluate``）是"构型完整给定 -> 算出净 Δv"，
各级推进剂是自变量；本模块反过来：目标净 Δv 是约束，推进剂是未知量。

多级火箭方程在这个方向上纠缠：每一级的点火质量都把上方级（含其推进剂）
扛在身上，改动任一级加注量都会沿质量链影响各级质量比，没有一步到位的
闭式分配。因此本模块只做**迭代逼近**，绝不抄一份近似火箭方程。

一致性保证（命根子）
--------------------
本模块**只**通过前向内核 ``app.core.engine.evaluate`` 评估每一次试探构型
的净 Δv。求解器自身不算任何 Δv、不重建质量链公式；反解出的推进剂原样
组装成普通 ``StageInput`` 喂回 ``evaluate`` 即可复现所宣称的净 Δv。
依赖方向严格为：inverse_solver -> core.engine；前向链路对本模块零依赖。

优化目标与算法
--------------
在可行域（各级加注量 ∈ [min,max]、总加注量 ≤ 预算）内，找一组加注量使
净 Δv 达到目标（净Δv − 目标 ∈ [−tol, 0] 量级，取最省点），并使**总推进剂
用量最省**。净 Δv 对每个加注坐标单调递增、边际递减，因此 KKT 一阶条件
要求：所有未被上下限截断的级，其边际 Δv 相等（等边际灌水线）。

* 可行性：把可用加注量灌满——预算不受限时取各级上限；预算受限时沿
  拉格朗日乘子 λ 做等边际灌水，求预算约束下的最大净 Δv 点。仍够不进
  容差带即不可行，并指明卡在预算还是某级加注上限。
* 最省分配：对乘子 μ 一维二分（μ 小=灌得多=Δv 大），每级内部再用一维
  二分定位"边际=μ"的水位并被上下限截断，使整箭净 Δv 恰好落在目标上；
  再沿"下限点→水位点"的射线做一次一维二分精修，保证达成值严格贴着
  目标（可行侧），总用量即最省。一般纠缠质量链下整个水位过程可重复
  扫描直至稳定（扫描轮数计入 sweeps）。

贪心式"逐坐标压到边界再换下一坐标"在此问题上会卡死在非最省点（压了下
级就只能顶着上级，无法同步再平衡），故不可采用。

全部数值运算只用标准库 math，不引入任何科学计算框架。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.core.constants import (
    DEFAULT_INVERSE_MAX_SWEEPS,
    DEFAULT_INVERSE_TOLERANCE,
)
from app.core.engine import StageInput, evaluate
from app.core.losses import DragSpec

# ---- 机器可读的结局 ----
SOLVED = "solved"
INFEASIBLE = "infeasible"

# ---- 不可行时被哪一侧约束卡死 ----
LIMIT_PROPELLANT_CAP = "propellant_cap"          # 各级顶到加注上限仍不够
LIMIT_BUDGET = "budget"                          # 预算见底

# 边际差分的相对步长（仅用于等边际水位排序，不参与最终 Δv 判定）
_FD_REL_EPS = 1.0e-6
# 一维二分对坐标的绝对/相对收敛下限
_X_ABS = 1.0e-10
_X_REL = 1.0e-12
# 乘子二分的迭代轮数
_MU_BISECT_ITERS = 18
# 单级内部"边际=μ"水位二分的迭代轮数
_LEVEL_BISECT_ITERS = 18
# 相邻扫描轮总用量稳定到该相对精度即认为水位已收敛（最终精度由射线
# 精修直接在 f 上二分保证，与水位定位误差无关）
_SWEEP_REL_TOL = 1.0e-9
# 射线精修的迭代轮数（直接决定达成值贴目标的数值精度）
_RAY_BISECT_ITERS = 60


@dataclass(frozen=True)
class InverseStageSpec:
    """反推请求中单级的确定量（推进剂未知，故不在此处）。"""

    ve: float
    structural_mass: float
    burn_time: float
    propellant_min: float
    propellant_max: float


@dataclass(frozen=True)
class InverseProblem:
    """一次目标反推问题的规整输入（已经过 services.validator 校验）。"""

    target_net_delta_v: float
    payload_mass: float
    stages: list[InverseStageSpec]
    tolerance: float = DEFAULT_INVERSE_TOLERANCE
    propellant_budget: float | None = None
    max_sweeps: int = DEFAULT_INVERSE_MAX_SWEEPS


@dataclass
class InverseStageSolution:
    """单级反解结果：加注量与该级在前向内核下的质量比、理想 Δv。"""

    index: int
    ve: float
    structural_mass: float
    burn_time: float
    propellant_mass: float
    propellant_min: float
    propellant_max: float
    m0: float
    mf: float
    upper_mass: float
    mass_ratio: float
    ideal_delta_v: float
    gravity_loss: float


@dataclass
class InverseSolution:
    """反推结局（solved / infeasible 共用同一结构）。"""

    status: str
    target_net_delta_v: float
    tolerance: float
    payload_mass: float
    achieved_net_delta_v: float | None = None
    deviation: float | None = None
    within_tolerance: bool = False
    total_propellant: float | None = None
    propellant_budget: float | None = None
    sweeps: int = 0
    forward_evaluations: int = 0
    stages: list[InverseStageSolution] = field(default_factory=list)
    infeasible_reason_code: str | None = None
    limiting_constraint: str | None = None
    stages_at_max: list[int] = field(default_factory=list)
    budget_exhausted: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        """序列化为 HTTP 响应体（不依赖 Pydantic）。"""
        data: dict[str, object] = {
            "status": self.status,
            "target_net_delta_v": self.target_net_delta_v,
            "achieved_net_delta_v": self.achieved_net_delta_v,
            "deviation": self.deviation,
            "within_tolerance": self.within_tolerance,
            "tolerance": self.tolerance,
            "payload_mass": self.payload_mass,
            "total_propellant_mass": self.total_propellant,
            "propellant_budget": self.propellant_budget,
            "sweeps": self.sweeps,
            "forward_evaluations": self.forward_evaluations,
            "stages": [
                {
                    "index": s.index,
                    "ve": s.ve,
                    "structural_mass": s.structural_mass,
                    "burn_time": s.burn_time,
                    "propellant_mass": s.propellant_mass,
                    "propellant_min": s.propellant_min,
                    "propellant_max": s.propellant_max,
                    "m0": s.m0,
                    "mf": s.mf,
                    "upper_mass": s.upper_mass,
                    "mass_ratio": s.mass_ratio,
                    "ideal_delta_v": s.ideal_delta_v,
                    "gravity_loss": s.gravity_loss,
                }
                for s in self.stages
            ],
        }
        if self.status == INFEASIBLE:
            data.update(
                {
                    "infeasible_reason_code": self.infeasible_reason_code,
                    "limiting_constraint": self.limiting_constraint,
                    "stages_at_max": self.stages_at_max,
                    "budget_exhausted": self.budget_exhausted,
                    "reason": self.reason,
                }
            )
        return data


def _stage_inputs(x: list[float], problem: InverseProblem) -> list[StageInput]:
    """把试探加注量原样组装成前向内核的普通构型输入。

    除推进剂外的 ve / 结构空重 / 工作时间全部原样透传，保证反解与前向
    共用同一套火箭方程与同一条质量链定义。
    """
    return [
        StageInput(
            ve=spec.ve,
            structural_mass=spec.structural_mass,
            propellant_mass=xi,
            burn_time=spec.burn_time,
        )
        for xi, spec in zip(x, problem.stages)
    ]


def _scale_slack(xs: list[float], extra: float = 0.0) -> float:
    """总量/上限贴合比较用的数值容差。"""
    return 1.0e-10 * max(1.0, math.fsum(abs(v) for v in xs), abs(extra))


def solve_inverse(problem: InverseProblem) -> InverseSolution:
    """求解一次目标反推问题。

    返回的 ``status`` 只可能是：

    * ``solved``    —— 达成值落在容差带内，加注量满足各级上下限与总预算，
                       且为可行域内的最省分配；
    * ``infeasible``—— 灌满所有可用加注量仍达不到目标容差带，
                       ``limiting_constraint`` 指明被哪一侧约束卡死。
    """
    specs = problem.stages
    n = len(specs)
    lo = [s.propellant_min for s in specs]
    hi = [s.propellant_max for s in specs]
    target = problem.target_net_delta_v
    tol = problem.tolerance
    budget = problem.propellant_budget

    eval_counter = {"count": 0}
    f_cache: dict[tuple[float, ...], float] = {}

    def f(x: list[float]) -> float:
        """试探构型的净 Δv —— 只经前向内核 evaluate 得出。

        同一加注向量在一次求解内复用结果（等边际定位会反复试探相同点）；
        缓存命中不计入前向评估次数，``forward_evaluations`` 只统计真正
        调用 evaluate 的次数。
        """
        key = tuple(x)
        cached = f_cache.get(key)
        if cached is not None:
            return cached
        value = evaluate(
            _stage_inputs(x, problem),
            problem.payload_mass,
            DragSpec(),
        ).net_delta_v
        eval_counter["count"] += 1
        f_cache[key] = value
        return value

    base = InverseSolution(
        status=SOLVED,
        target_net_delta_v=target,
        tolerance=tol,
        payload_mass=problem.payload_mass,
        propellant_budget=budget,
    )

    x_lo = list(lo)
    f_lo = f(x_lo)

    # 平凡情形：下限点已进入容差带（f_lo >= target − tol）。下限点是可行域
    # 内总用量最小的点，故它就是最省解，无需迭代。若 f_lo + tol < target，
    # 连下限都够不进容差带，必须继续走可行性判定，绝不在这里假装成功。
    if f_lo + tol >= target:
        base.forward_evaluations = eval_counter["count"]
        return _finalize_solved(base, x_lo, problem, f, sweeps=0)

    # ---- 第一步：求"可用加注量上限点"，据此判定可行性 ----
    cap_slack = _scale_slack(hi, budget or 0.0)
    if budget is None or math.fsum(hi) <= budget + cap_slack:
        # 预算不构成约束：灌满所有级的上限即是最大净 Δv 点
        x_max = list(hi)
        budget_binds = False
    else:
        # 预算比各级上限之和紧：预算约束下的等边际最大点
        x_max = _budget_waterfill(lo, hi, budget, f)
        budget_binds = True
    f_max = f(x_max)

    if f_max + tol < target:
        base.forward_evaluations = eval_counter["count"]
        return _finalize_infeasible(
            base, x_max, problem, f, budget_binds=budget_binds
        )

    # ---- 第二步：可行。等边际水位迭代求总用量最省的达标分配 ----
    # "够用"按容差带下界 target−tol 判定：达到该值偏差即落入 [−tol, 0]，
    # 多灌的每一公斤都属于浪费，故最省点对着容差带下界求解，而不是死贴
    # target；若调用方要求"一个都不能少"，把 tolerance 取到数值极小即可。
    effective_target = target - tol
    x, sweeps = _minimum_assignment(
        lo, hi, f, effective_target, max_sweeps=problem.max_sweeps
    )

    # 防御性预算守卫：可行性已保证最省点总用量 ≤ 预算（预算点本身可行），
    # 这里只修数值尾数；若修完仍不达标则如实判不可行，绝不返回差解。
    if budget is not None:
        total = math.fsum(x)
        if total > budget + _scale_slack(x, budget):
            alpha = (budget - math.fsum(lo)) / (total - math.fsum(lo))
            alpha = min(1.0, max(0.0, alpha))
            x = [lo[j] + alpha * (x[j] - lo[j]) for j in range(n)]
    f_now = f(x)
    base.forward_evaluations = eval_counter["count"]
    if f_now + tol < target:
        # 仅在预算尾数修正导致掉出容差带时到达：按预算见底如实上报
        return _finalize_infeasible(
            base, x, problem, f, budget_binds=budget is not None
        )

    return _finalize_solved(base, x, problem, f, sweeps=sweeps)


# ---------------------------------------------------------------------------
# 等边际灌水线基础设施
# ---------------------------------------------------------------------------


def _marginal_gain(base: list[float], j: int, f) -> float:
    """第 j 级在当前加注量附近的边际净 Δv（前向差分，仅调前向 f）。

    只用于等边际水位的定位；推进剂↔Δv 关系严格递增凹，边际随加注量
    递减。最终"是否达标"始终用 evaluate 的完整前向结果判定（射线精修
    直接二分前向 f），差分误差不影响往返一致性，只影响最省分配的定位
    精度——射线精修会在该定位附近把达成值重新贴回目标。
    """
    xj = base[j]
    step = max(_X_ABS, _FD_REL_EPS * max(1.0, xj))
    xp = list(base)
    xp[j] = xj + step
    return (f(xp) - f(base)) / step


def _level_for_mu(
    base: list[float],
    lo: list[float],
    hi: list[float],
    mu: float,
    f,
) -> list[float]:
    """给定水位 μ，返回各级"边际 Δv = μ"的加注量（被 [lo,hi] 截断）。

    每级内部沿自身坐标一维二分；边际单调递减，故夹逼唯一。
    """
    x = list(base)
    for j in range(len(lo)):
        xp_lo = list(base)
        xp_lo[j] = lo[j]
        m_at_lo = _marginal_gain(xp_lo, j, f)
        if m_at_lo <= mu:
            x[j] = lo[j]
            continue
        xp_hi = list(base)
        xp_hi[j] = hi[j]
        m_at_hi = _marginal_gain(xp_hi, j, f)
        if m_at_hi >= mu:
            x[j] = hi[j]
            continue

        a, b = lo[j], hi[j]
        for _ in range(_LEVEL_BISECT_ITERS):
            mid = 0.5 * (a + b)
            xp = list(base)
            xp[j] = mid
            if _marginal_gain(xp, j, f) >= mu:
                a = mid
            else:
                b = mid
            if b - a <= max(_X_ABS, _X_REL * max(1.0, b)):
                break
        x[j] = 0.5 * (a + b)
    return x


def _ray_polish(
    lo: list[float],
    z: list[float],
    f,
    target: float,
) -> list[float]:
    """沿射线 lo + α(z − lo) 一维二分，取恰好达标（f ≥ target）的最省点。

    射线上每个坐标都随 α 单调增加，故 f 沿射线单调递增；α=0 是下限点
    （调用方已保证 f_lo < target），α=1 是水位点（f ≥ target）。二分始终
    保留可行侧端点，返回点严格满足 f ≥ target（数值精度内）。
    """
    span = [z[j] - lo[j] for j in range(len(lo))]
    low, high = 0.0, 1.0
    for _ in range(_RAY_BISECT_ITERS):
        mid = 0.5 * (low + high)
        x = [lo[j] + mid * span[j] for j in range(len(lo))]
        if f(x) >= target:
            high = mid
        else:
            low = mid
    alpha = high
    return [lo[j] + alpha * span[j] for j in range(len(lo))]


def _minimum_assignment(
    lo: list[float],
    hi: list[float],
    f,
    target: float,
    *,
    max_sweeps: int,
) -> tuple[list[float], int]:
    """等边际水位迭代：求净 Δv 恰好达标时总用量最省的加注向量。

    每轮扫描：以当前点为基准对水位 μ 二分，使整箭 f 落在目标上，再做
    射线精修；相邻两轮总用量不再变化即收敛。可分质量链一轮即到 KKT 点，
    第二轮用于确认稳定；纠缠质量链下扫描会真实地反复再平衡。
    """
    x = list(lo)
    sweeps = 0
    previous_total: float | None = None
    for sweeps in range(1, max_sweeps + 1):
        # μ 上界：取下限点观测到的最大边际的 10 倍，该水位下所有级退回下限
        m_at_lo = [_marginal_gain(x, j, f) for j in range(len(lo))]
        mu_high = max(1.0, 10.0 * max(m_at_lo))
        mu_low = 0.0  # μ=0 的水位是各级上限（可行性已保证该点达标）
        z_feasible = list(hi)
        for _ in range(_MU_BISECT_ITERS):
            mu = 0.5 * (mu_low + mu_high)
            z = _level_for_mu(x, lo, hi, mu, f)
            if f(z) >= target:
                mu_low = mu
                z_feasible = z
            else:
                mu_high = mu

        x_new = _ray_polish(lo, z_feasible, f, target)
        total_new = math.fsum(x_new)
        if previous_total is not None and abs(previous_total - total_new) <= max(
            _X_ABS, _SWEEP_REL_TOL * max(1.0, total_new)
        ):
            x = x_new
            break
        previous_total = total_new
        x = x_new
    return x, sweeps


def _budget_waterfill(
    lo: list[float],
    hi: list[float],
    budget: float,
    f,
) -> list[float]:
    """预算受限时的最大净 Δv 加注点：等边际灌水 + 预算贴满。

    存在乘子 λ 使每级取"边际=λ"水位（被上下限截断），总用量 S(λ) 随 λ
    单调递减；对 λ 一维二分使 S(λ)=budget，最后把尾数预算补到当前边际
    最高的级，保证返回点总用量与预算严格贴合（便于如实判定预算见底）。
    """
    n = len(lo)
    x = list(lo)

    m_at_lo = [_marginal_gain(x, j, f) for j in range(n)]
    lam_high = max(1.0, 10.0 * max(m_at_lo))

    def total_at(lam: float) -> float:
        return math.fsum(_level_for_mu(x, lo, hi, lam, f))

    lam_low = 0.0
    for _ in range(_MU_BISECT_ITERS + 8):
        lam = 0.5 * (lam_low + lam_high)
        if total_at(lam) > budget:
            lam_low = lam
        else:
            lam_high = lam
    x = _level_for_mu(x, lo, hi, lam_high, f)

    # 数值收尾 1：若差分误差使总量略微越界，从当前边际最低的级回吐
    total = math.fsum(x)
    if total > budget:
        order = sorted(range(n), key=lambda j: _marginal_gain(x, j, f))
        for j in order:
            excess = total - budget
            if excess <= 0.0:
                break
            trimmed = min(excess, x[j] - lo[j])
            x[j] -= trimmed
            total -= trimmed

    # 数值收尾 2：若还剩尾数预算，补到当前边际最高且有空间的级，贴满预算
    total = math.fsum(x)
    budget_tick = 1.0e-10 * max(1.0, budget)
    while budget - total > budget_tick:
        candidates = [j for j in range(n) if hi[j] - x[j] > budget_tick]
        if not candidates:
            break
        j = max(candidates, key=lambda k: _marginal_gain(x, k, f))
        add = min(budget - total, hi[j] - x[j])
        x[j] += add
        total = math.fsum(x)
    return x


# ---------------------------------------------------------------------------
# 结局组装
# ---------------------------------------------------------------------------


def _finalize_solved(
    base: InverseSolution,
    x: list[float],
    problem: InverseProblem,
    f,
    *,
    sweeps: int,
) -> InverseSolution:
    """用前向内核核对最终构型，组装 solved 结局。"""
    result = evaluate(
        _stage_inputs(x, problem),
        problem.payload_mass,
        DragSpec(),
    )
    achieved = result.net_delta_v
    base.status = SOLVED
    base.achieved_net_delta_v = achieved
    base.deviation = achieved - problem.target_net_delta_v
    base.within_tolerance = abs(base.deviation) <= problem.tolerance
    base.total_propellant = math.fsum(x)
    base.sweeps = sweeps
    base.stages = [
        InverseStageSolution(
            index=r.index,
            ve=r.ve,
            structural_mass=problem.stages[r.index].structural_mass,
            burn_time=problem.stages[r.index].burn_time,
            propellant_mass=x[r.index],
            propellant_min=problem.stages[r.index].propellant_min,
            propellant_max=problem.stages[r.index].propellant_max,
            m0=r.m0,
            mf=r.mf,
            upper_mass=r.upper_mass,
            mass_ratio=r.mass_ratio,
            ideal_delta_v=r.ideal_delta_v,
            gravity_loss=r.gravity_loss,
        )
        for r in result.stages
    ]
    return base


def _finalize_infeasible(
    base: InverseSolution,
    x: list[float],
    problem: InverseProblem,
    f,
    *,
    budget_binds: bool,
) -> InverseSolution:
    """组装 infeasible 结局：带上限点尽力结果，指明被哪一侧约束卡死。"""
    result = evaluate(
        _stage_inputs(x, problem),
        problem.payload_mass,
        DragSpec(),
    )
    achieved = result.net_delta_v
    cap_tol = _scale_slack(x)
    at_max = [
        j
        for j, spec in enumerate(problem.stages)
        if x[j] >= spec.propellant_max - cap_tol
    ]

    limiting = LIMIT_BUDGET if budget_binds else LIMIT_PROPELLANT_CAP
    base.status = INFEASIBLE
    base.achieved_net_delta_v = achieved
    base.deviation = achieved - problem.target_net_delta_v
    base.within_tolerance = False
    base.total_propellant = math.fsum(x)
    base.infeasible_reason_code = limiting
    base.limiting_constraint = limiting
    base.stages_at_max = at_max
    base.budget_exhausted = budget_binds
    base.stages = [
        InverseStageSolution(
            index=r.index,
            ve=r.ve,
            structural_mass=problem.stages[r.index].structural_mass,
            burn_time=problem.stages[r.index].burn_time,
            propellant_mass=x[r.index],
            propellant_min=problem.stages[r.index].propellant_min,
            propellant_max=problem.stages[r.index].propellant_max,
            m0=r.m0,
            mf=r.mf,
            upper_mass=r.upper_mass,
            mass_ratio=r.mass_ratio,
            ideal_delta_v=r.ideal_delta_v,
            gravity_loss=r.gravity_loss,
        )
        for r in result.stages
    ]
    shortfall = problem.target_net_delta_v - achieved
    if budget_binds:
        base.reason = (
            f"推进剂总预算 {problem.propellant_budget:g} kg 见底时净 Δv 仅 "
            f"{achieved:.3f} m/s，距目标 {problem.target_net_delta_v:g} m/s "
            f"还差 {shortfall:.3f} m/s，卡死在预算一侧"
            + (
                f"；其中第 {'、'.join(str(j) for j in at_max)} 级同时顶到加注上限"
                if at_max
                else ""
            )
        )
    else:
        stages_text = "、".join(str(j) for j in at_max) or "各级"
        base.reason = (
            f"把各级都灌到加注上限（触顶级：{stages_text}）后净 Δv 仅 "
            f"{achieved:.3f} m/s，距目标 {problem.target_net_delta_v:g} m/s "
            f"还差 {shortfall:.3f} m/s，卡死在某级加注上限一侧"
        )
    return base
