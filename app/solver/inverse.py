"""反解求解器：目标净速度增量 → 各级推进剂加注量。

求解方向的翻转
--------------
前向核算：构型（含各级推进剂）完整给定，算出净速度增量；各级推进剂是
自变量。本模块反过来：目标净速度增量、有效载荷、各级确定量（有效排气
速度、结构空重、有效工作时间、加注上下限）给定，推进剂成了未知量，
净速度增量成了约束。

问题形式（与前向内核共用同一条质量链定义，见 app.core.staging）
----------------------------------------------------------------
对自下而上的第 i 级（index 0 为最底级），前向质量链给出：

    mf_i = ms_i + 上方质量_i     （上方质量 = 上方各级"耗尽"结构 + 载荷，
                                   与各级推进剂加注量无关，反解中为常量）
    m0_i = mf_i + x_i
    Δv_i = ve_i·ln(m0_i / mf_i)
    理想总Δv = Σ Δv_i
    净Δv = 理想总Δv − g·Σ burn_time − 阻力损失

重力损失只与各级工作时间（反解中已给定）有关，是常量；阻力损失按
app.core.losses 的同一规格扣减。反解写成约束优化：

    最小化  Σ x_i                     （"不浪费"：总推进剂用量最省）
    使满足  净Δv(x) ∈ [目标−容差, 目标+容差]
            propellant_min_i ≤ x_i ≤ propellant_max_i
            Σ x_i ≤ 总预算（若给定）

为什么必须迭代
--------------
净Δv 关于 x_i 是 ve_i·ln(1 + x_i/mf_i) 的非线性求和，加上逐级加注
上下限与总预算这组线性约束，KKT 驻点条件 ve_i/(mf_i + x_i) = λ 在
哪些级上被上下限截断、预算乘数取多大，都随问题数据改变，λ 没有
闭式值。这里对拉格朗日函数 L_λ = Σx − λ·净Δv 做"多起点坐标下降 +
乘子二分"迭代逼近：

* 坐标取第 i 级点火质量 t = m0_i = mf_i + x_i。在该坐标上，加注上下限
  是逐维简单区间 [mf_i + 下限, mf_i + 上限]，总预算 Σx ≤ B 只把该坐标
  再压一个线性上界 m0_i + (B − Σx)，区间可直接写出；
* L_λ 沿单坐标严格凸，唯一驻点由同一套火箭方程的解析导数
  d(净Δv)/d(m0_i) = ve_i / m0_i 定位（t = λ·(1−阻力比例)·ve_i），
  驻点与区间端点一起作为候选点（解析导数只用于"挑试探点"）；
* L_λ 最优解的净速度增量随 λ 单调不减，对 λ 先倍增夹取再二分，即可
  把解钉在"净速度增量恰达容差带下沿"的最省位置；
* 每个候选构型的净速度增量、各级质量比等一切物理量，一律调用
  ``app.core.engine.evaluate`` 求值，最终结论以前向内核数值为准 ——
  反解与前向共用同一套火箭方程与同一条质量链，绝不另抄近似公式。

依赖方向：app.solver.inverse → app.core.engine（单向），前向链路不
反向依赖求解器。仅使用标准库 math。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

from app.core.engine import ConfigurationResult, StageInput, evaluate
from app.core.errors import INVERSE_FORWARD_MISMATCH, RocketValidationError
from app.core.losses import DRAG_LINEAR, DragSpec

# ---- 结局状态 ----
STATUS_SOLVED = "solved"
STATUS_INFEASIBLE = "infeasible"

# ---- 不可行原因码（机器可读）----
REASON_TARGET_UNREACHABLE = "TARGET_UNREACHABLE"
REASON_TARGET_OVERSHOT_AT_MINIMUM = "TARGET_OVERSHOT_AT_MINIMUM"
REASON_BUDGET_BELOW_MINIMUM = "BUDGET_BELOW_MINIMUM"

# ---- 卡死侧标识（机器可读）----
BINDING_BUDGET = "propellant_budget"
BINDING_STAGE_MAX = "stage_propellant_max"
BINDING_BUDGET_AND_STAGE_MAX = "propellant_budget+stage_propellant_max"
BINDING_STAGE_MIN = "stage_propellant_min"
BINDING_DIMINISHING = "diminishing_returns"

# 容差合理缺省：目标量级通常数千 m/s，1 m/s 约为万分之几
DEFAULT_TOLERANCE = 1.0


@dataclass(frozen=True)
class InverseStageSpec:
    """反解中单级的确定参数与加注上下限（自下而上排列）。"""

    ve: float                 # 有效排气速度 m/s
    structural_mass: float    # 结构空重 kg
    burn_time: float          # 有效工作时间 s
    propellant_min: float     # 允许加注推进剂下限 kg（>= 0）
    propellant_max: float     # 允许加注推进剂上限 kg（>= 下限）


@dataclass(frozen=True)
class InverseProblem:
    """一次反解问题的全部输入。"""

    target_delta_v: float
    payload_mass: float
    stages: list[InverseStageSpec]
    propellant_budget: float | None
    tolerance: float
    drag: DragSpec


@dataclass(frozen=True)
class InverseSolution:
    """结局一：目标可达且在容差带内求得总推进剂最省的分配。"""

    propellant_masses: list[float]
    evaluation: ConfigurationResult   # 解构型喂回前向内核得到的完整核算
    iterations: int


@dataclass(frozen=True)
class InverseInfeasible:
    """结局二：约束域内不存在满足容差的加注组合。"""

    reason_code: str                  # REASON_*
    binding_constraint: str | None    # BINDING_*；预算连下限都装不下时为 propellant_budget
    binding_stage_indices: list[int]  # 触到加注上限/下限的级（可空）
    max_achievable_net_delta_v: float | None
    min_achievable_net_delta_v: float | None
    detail: str
    iterations: int


InverseResult = InverseSolution | InverseInfeasible


class _Evaluator:
    """把"各级推进剂向量"装配成普通构型并交给前向内核核算。

    求解过程中所有物理量（质量链、逐级质量比与 Δv、损失、净值、起飞
    质量）都从这里来，本类不计算任何物理量，只做装配与调用计数。
    """

    def __init__(self, problem: InverseProblem) -> None:
        self._fixed = [
            (s.ve, s.structural_mass, s.burn_time) for s in problem.stages
        ]
        self._payload = problem.payload_mass
        self._drag = problem.drag
        self.calls = 0

    def evaluate(self, propellants: list[float]) -> ConfigurationResult:
        self.calls += 1
        stages = [
            StageInput(ve=ve, structural_mass=ms, propellant_mass=mp, burn_time=bt)
            for (ve, ms, bt), mp in zip(self._fixed, propellants)
        ]
        return evaluate(stages, self._payload, self._drag)


def _total_propellant(result: ConfigurationResult) -> float:
    """总推进剂 = Σ(m0_i − mf_i)，与前向质量链同一口径。"""
    return math.fsum(s.m0 - s.mf for s in result.stages)


def _find_roots(
    g: Callable[[float], float],
    level: float,
    lo: float,
    hi: float,
    samples: int = 129,
) -> list[float]:
    """在 (lo, hi) 内找方程 g(t) = level 的全部根。

    均匀采样找符号变化区间，再对每个区间二分。g 在区间内光滑正、严格
    单调（见 _InverseSolver._section_derivative），无根或恰有一根；
    保留通用实现，不依赖该单调性。
    """
    roots: list[float] = []
    prev_t = lo
    prev_v = g(lo) - level
    for k in range(1, samples + 1):
        t = lo + (hi - lo) * (k / samples)
        v = g(t) - level
        if v == 0.0:
            roots.append(t)
        elif prev_v < 0.0 < v or v < 0.0 < prev_v:
            a, b, fa = prev_t, t, prev_v
            for _ in range(80):
                m = 0.5 * (a + b)
                fm = g(m) - level
                if fa * fm < 0.0:
                    b = m
                else:
                    a, fa = m, fm
            roots.append(0.5 * (a + b))
        prev_t, prev_v = t, v
    return roots


class _InverseSolver:
    """多起点坐标下降（固定 λ 时精确求 L_λ 极小点）+ λ 夹取二分。

    坐标 i 取第 i 级点火质量 t = m0_i = mf_i + x_i（index 0 为最底级，
    与前向质量链同一约定）。前向质量链中 m0_i 只由本级推进剂与上方
    耗尽质量决定，因此沿单坐标：

        d(理想总Δv)/dt = ve_i / t

    （上方耗尽质量与各级推进剂无关，见 app.core.staging）。L_λ 沿该
    坐标的驻点条件为 t = λ·(1−阻力比例)·ve_i。该解析式只用于**定位
    候选点**；候选构型的净速度增量与总推进剂全部由前向内核 evaluate
    给出，最终结论以内核数值为准。
    """

    def __init__(self, problem: InverseProblem) -> None:
        self.problem = problem
        self.evaluator = _Evaluator(problem)
        self.sweeps = 0
        # 净 Δv 对理想 Δv 的传导：linear 阻力下 net = F·(1−fraction) − g
        if problem.drag.mode == DRAG_LINEAR:
            self._net_factor = 1.0 - problem.drag.fraction
        else:
            self._net_factor = 1.0
        # 得分比较与收敛判定统一按 kg 尺度，不随乘子 λ 尺度漂移
        self._mass_scale = max(
            1.0, math.fsum(s.propellant_max for s in problem.stages)
        )
        # 各级耗尽质量 mf_i = 本级结构 + 上方耗尽质量 + 载荷（与推进剂
        # 无关；与前向质量链同一递推，见 app.core.staging）
        mf_reversed: list[float] = []
        upper = problem.payload_mass
        for spec in reversed(problem.stages):
            mf_reversed.append(spec.structural_mass + upper)
            upper = mf_reversed[-1]
        self._mf = list(reversed(mf_reversed))

    def _section_derivative(self, i: int, t: float) -> float:
        """g_i(t) = d(理想总Δv)/d(m0_i) = ve_i / m0_i（其余坐标固定）。"""
        return self.problem.stages[i].ve / t

    # ------------------------------------------------------------------
    # 坐标下降主过程
    # ------------------------------------------------------------------
    def descent(
        self,
        start: list[float],
        score: Callable[[ConfigurationResult], float],
        root_levels: tuple[float, ...],
        max_sweeps: int = 60,
        stop_tol: float | None = None,
    ) -> tuple[list[float], ConfigurationResult, float]:
        """从可行起点出发逐坐标最小化 score，只接受让 score 变小的步。

        root_levels：g_i(t) 等于这些值的点（目标函数在单坐标上的驻点
        条件）与区间端点一起作为候选。stop_tol：整轮最大坐标变化量不
        超过它即判收敛（按 kg 计）；缺省取 1e-10·加注量尺度。
        """
        if stop_tol is None:
            stop_tol = 1e-10 * self._mass_scale
        problem = self.problem
        n = len(problem.stages)
        propellants = list(start)
        result = self.evaluator.evaluate(propellants)
        best_score = score(result)

        for _ in range(max_sweeps):
            self.sweeps += 1
            max_change = 0.0
            for i in range(n - 1, -1, -1):  # 自顶向下降级：上方级先更新
                spec = problem.stages[i]
                stage = result.stages[i]
                mf_i = stage.mf
                m0_i = stage.m0
                lo = mf_i + spec.propellant_min
                hi = mf_i + spec.propellant_max
                if problem.propellant_budget is not None:
                    # Σx ≤ B 在本坐标上的精确等价：x_i ≤ B − Σ_{j≠i} x_j，
                    # 即 m0_i ≤ m0_i + (B − Σx)
                    slack = problem.propellant_budget - math.fsum(propellants)
                    hi = min(hi, m0_i + max(0.0, slack))
                if hi - lo <= 1e-12 * max(1.0, m0_i):
                    continue

                candidates = {lo, hi, m0_i}
                for level in root_levels:
                    candidates.update(
                        _find_roots(
                            lambda t, _i=i: self._section_derivative(_i, t),
                            level,
                            lo,
                            hi,
                        )
                    )

                # 候选点都相对同一基准（区间也是对它算的）
                base = list(propellants)
                chosen_t = m0_i
                for t in sorted(candidates):
                    if abs(t - m0_i) <= 1e-12 * max(1.0, m0_i):
                        continue
                    trial = list(base)
                    trial[i] = t - mf_i
                    trial_result = self.evaluator.evaluate(trial)
                    trial_score = score(trial_result)
                    # 只接受严格改善：下降单调，保证收敛到驻点。
                    # 判定用严格小于、不设固定 kg 门槛 —— 驻点附近的
                    # 改善量随距离平方缩小，任何绝对阈值都会把二分后期
                    # 的正当小步误判为噪声；确定性得分下严格小于不会
                    # 振荡（A→B 与 B→A 不能同时严格改善），何时"足够
                    # 收敛"由 stop_tol 统一判定。
                    if trial_score < best_score:
                        chosen_t = t
                        best_score = trial_score
                        result = trial_result
                        propellants = trial

                change = abs(chosen_t - m0_i)
                if change > max_change:
                    max_change = change

            # 预算贴边时单坐标移动被冻结（slack≈0），但"把加注量在
            # 两级间等量腾挪"仍可改善得分。对每对级做保总量的转移步：
            # L_λ 沿转移量 δ 仍严格凸，唯一驻点有闭式解
            # ve_i/(m0_i+δ) = ve_j/(m0_j−δ)
            #     ⇒ δ* = (ve_i·m0_j − ve_j·m0_i)/(ve_i+ve_j)
            for i in range(n):
                for j in range(i + 1, n):
                    si, sj = problem.stages[i], problem.stages[j]
                    xi, xj = propellants[i], propellants[j]
                    d_lo = max(si.propellant_min - xi, xj - sj.propellant_max)
                    d_hi = min(si.propellant_max - xi, xj - sj.propellant_min)
                    if d_hi - d_lo <= 1e-12 * self._mass_scale:
                        continue
                    m0_i_now = result.stages[i].m0
                    m0_j_now = result.stages[j].m0
                    d_star = (
                        si.ve * m0_j_now - sj.ve * m0_i_now
                    ) / (si.ve + sj.ve)
                    d_star = min(max(d_star, d_lo), d_hi)

                    # 候选点都相对同一基准 base（区间也是对它算的），
                    # 接受某个候选后不能再拿其余相对位移叠到新状态上
                    base = list(propellants)
                    base_score = best_score
                    chosen: tuple[
                        float, list[float], ConfigurationResult
                    ] | None = None
                    for d in {d_lo, d_hi, d_star}:
                        if abs(d) <= 1e-12 * self._mass_scale:
                            continue
                        trial = list(base)
                        trial[i] += d
                        trial[j] -= d
                        trial_result = self.evaluator.evaluate(trial)
                        trial_score = score(trial_result)
                        if trial_score < base_score:
                            base_score = trial_score
                            chosen = (d, trial, trial_result)

                    if chosen is not None:
                        d, propellants, result = chosen
                        best_score = base_score
                        if abs(d) > max_change:
                            max_change = abs(d)

            if max_change <= stop_tol:
                break

        return propellants, result, best_score

    # ------------------------------------------------------------------
    # 可行起点
    # ------------------------------------------------------------------
    def _clip_to_budget(self, propellants: list[float]) -> list[float]:
        """把可行域内的起点压进总预算（自顶向下削减，保留下限）。"""
        budget = self.problem.propellant_budget
        if budget is None:
            return list(propellants)
        clipped = list(propellants)
        excess = math.fsum(clipped) - budget
        if excess <= 0.0:
            return clipped
        for i in range(len(clipped) - 1, -1, -1):
            cut = min(
                max(0.0, excess),
                clipped[i] - self.problem.stages[i].propellant_min,
            )
            clipped[i] -= cut
            excess -= cut
            if excess <= 0.0:
                break
        return clipped

    def _start_points(self) -> list[list[float]]:
        specs = self.problem.stages
        mins = [s.propellant_min for s in specs]
        maxs = [s.propellant_max for s in specs]
        mids = [(a + b) * 0.5 for a, b in zip(mins, maxs)]
        starts = [
            list(mins),
            self._clip_to_budget(maxs),
            self._clip_to_budget(mids),
        ]
        unique: list[list[float]] = []
        for candidate in starts:
            if not any(
                all(
                    abs(a - b) <= 1e-12 * max(1.0, abs(a))
                    for a, b in zip(candidate, seen)
                )
                for seen in unique
            ):
                unique.append(candidate)
        return unique

    # ------------------------------------------------------------------
    # 极值探查：约束域内净速度增量的最大/最小点
    # ------------------------------------------------------------------
    def optimize_net(
        self, maximize: bool
    ) -> tuple[list[float], ConfigurationResult]:
        sign = -1.0 if maximize else 1.0
        best: tuple[list[float], ConfigurationResult, float] | None = None
        for start in self._start_points():
            got = self.descent(
                start, lambda r: sign * r.net_delta_v, (0.0,)
            )
            if best is None or got[2] < best[2]:
                best = got
        assert best is not None
        return best[0], best[1]

    # ------------------------------------------------------------------
    # 拉格朗日求解：在 net(x) ≥ edge 下最小化总推进剂 Σx
    # ------------------------------------------------------------------
    def solve_at_edge(
        self, edge: float, edge_hi: float, eps_conv: float
    ) -> tuple[list[float], ConfigurationResult]:
        """对拉格朗日乘子 λ 二分，把最优解的净速度增量钉到 edge。

        L_λ(x) = Σx − λ·net(x) 关于每个坐标光滑且凸（ln 项严格凹取负
        后严格凸），坐标下降配解析驻点可精确求解；最优解的净速度增量
        随 λ 单调不减，故先倍增夹取、再二分，每一步都暖启动坐标下降。
        返回所有"落在带内"的评估点中总推进剂最省的一个。
        """
        nf = self._net_factor
        # λ 的量纲是 kg/(m/s)，与 mf/ve 同阶；取远低于各级 mf_i/ve_i
        # 的下界，保证 λ0 处速度项不起作用、最优解就是全下限构型
        lam = 0.5 * min(
            mf / spec.ve for mf, spec in zip(self._mf, self.problem.stages)
        )
        lam = max(lam, 1e-12) / max(1e-12, nf)

        def make_score(lam_val: float) -> Callable[[ConfigurationResult], float]:
            def score(r: ConfigurationResult) -> float:
                return _total_propellant(r) - lam_val * r.net_delta_v

            return score

        def level(lam_val: float) -> tuple[float, ...]:
            # nf = 1 − 阻力比例；nf<=0 时净Δv 不再随加注量增加
            # （理想Δv 全被阻力吃掉），没有内部驻点，只走区间端点
            if nf <= 1e-12:
                return (0.0,)
            return (1.0 / (lam_val * nf),)

        def acceptable(net: float) -> bool:
            return edge - eps_conv <= net <= edge_hi

        # 坐标下降停止阈值：坐标变化 δx 带来的净Δv 变化约为
        # max_i(ve_i/m0_i)·δx（线性阻力再乘 nf）；取该误差不超过
        # eps_conv 的一半。地板跟随解的实际质量尺度（约 10·ULP，
        # 远低于容差允许的质量变化），不按 Σ 上限放大
        def x_noise_floor(prop: list[float]) -> float:
            return 10.0 * 2.22e-16 * max(1.0, max(map(abs, prop), default=1.0))

        max_marginal = max(
            spec.ve / max(mf, 1e-300)
            for spec, mf in zip(self.problem.stages, self._mf)
        ) * max(1e-12, nf)
        x_stop = max(
            x_noise_floor(list(self._start_points()[0])),
            0.5 * eps_conv / max_marginal,
        )

        propellants, result, _ = self.descent(
            list(self._start_points()[0]),
            make_score(lam),
            level(lam),
            stop_tol=x_stop,
        )

        def better(a: tuple[list[float], ConfigurationResult],
                   b: tuple[list[float], ConfigurationResult]
                   ) -> tuple[list[float], ConfigurationResult]:
            """在带内点中取 Σx 更省者；都不在带内取离 edge 更近者。"""
            a_in = acceptable(a[1].net_delta_v)
            b_in = acceptable(b[1].net_delta_v)
            if a_in and b_in:
                return a if _total_propellant(a[1]) <= _total_propellant(b[1]) else b
            if a_in:
                return a
            if b_in:
                return b
            if abs(a[1].net_delta_v - edge) <= abs(b[1].net_delta_v - edge):
                return a
            return b

        best_prop, best_result = propellants, result

        # 倍增夹取：找到 net(λ) 越过 edge 的 λ 区间
        # （不变式：net(λ_lo) < edge；λ0 处即全下限构型，上层已保证其越不过）
        lam_lo = lam
        lam_hi = lam
        bracketed = result.net_delta_v >= edge
        prev_net = result.net_delta_v
        for _ in range(60):
            if bracketed:
                break
            lam_hi = lam_lo * 4.0
            # 暖启动在相邻乘子极接近时可能因候选网格跳过小步而卡住，
            # 平台出现即回退到全下限冷启动（凸问题，冷启动必到最优）
            # 暖启动在相邻乘子极接近时可能因候选网格跳过小步而卡住，
            # 净Δv 已走不到一个容差步即判定平台，回退全下限冷启动
            # （本问题为凸，冷启动必到该 λ 的真正最优）
            start = (
                propellants
                if abs(result.net_delta_v - prev_net) > eps_conv
                else list(self._start_points()[0])
            )
            propellants, result, _ = self.descent(
                start,
                make_score(lam_hi),
                level(lam_hi),
                stop_tol=x_stop,
            )
            best_prop, best_result = better(
                (best_prop, best_result), (propellants, result)
            )
            if result.net_delta_v >= edge:
                bracketed = True
            else:
                lam_lo = lam_hi
            prev_net = result.net_delta_v
        if not bracketed:
            # λ 已放大到 4^60 仍够不到 edge：按不可收敛交上层判定
            return best_prop, best_result

        # 二分 λ：保持 net(λ_lo) < edge ≤ net(λ_hi)
        prev_net = best_result.net_delta_v
        for _ in range(50):
            lam_mid = 0.5 * (lam_lo + lam_hi)
            start = (
                propellants
                if abs(result.net_delta_v - prev_net) > eps_conv
                else list(self._start_points()[0])
            )
            propellants, result, _ = self.descent(
                start,
                make_score(lam_mid),
                level(lam_mid),
                stop_tol=x_stop,
            )
            best_prop, best_result = better(
                (best_prop, best_result), (propellants, result)
            )
            if result.net_delta_v >= edge:
                lam_hi = lam_mid
            else:
                lam_lo = lam_mid
            # 注意：停止判定只看"至今最贴近 edge 的带内点" best_result，
            # 不能用当前分点（它可能还在 edge 之下）提前收工
            if (
                acceptable(best_result.net_delta_v)
                and abs(best_result.net_delta_v - edge) <= eps_conv
            ):
                break
            prev_net = result.net_delta_v
        return best_prop, best_result


def _diagnose_binding(
    problem: InverseProblem, propellants: list[float]
) -> tuple[str, list[int]]:
    """在最大净速度增量点诊断被哪一侧约束卡死。"""
    at_max = [
        i
        for i, (spec, x) in enumerate(zip(problem.stages, propellants))
        if x >= spec.propellant_max
        - 1e-9 * max(1.0, spec.propellant_max)
    ]
    budget = problem.propellant_budget
    budget_binding = budget is not None and math.fsum(propellants) >= budget - (
        1e-9 * max(1.0, budget)
    )
    if budget_binding and at_max:
        return BINDING_BUDGET_AND_STAGE_MAX, at_max
    if budget_binding:
        return BINDING_BUDGET, []
    if at_max:
        return BINDING_STAGE_MAX, at_max
    return BINDING_DIMINISHING, []


def solve_inverse(problem: InverseProblem) -> InverseResult:
    """求解一次反解问题，如实返回三种结局之一。

    输入合法性应已由服务层 ``inverse_validator`` 提前拦截；本函数假设
    问题数据自洽（级数 >= 1、数值有限、上下限有序、预算非负）。
    """
    solver = _InverseSolver(problem)
    evaluator = solver.evaluator
    specs = problem.stages
    target = problem.target_delta_v
    tol = problem.tolerance
    budget = problem.propellant_budget

    mins = [s.propellant_min for s in specs]
    sum_min = math.fsum(mins)

    # 预算连各级下限之和都装不下：约束域为空
    if budget is not None and budget + 1e-9 * max(1.0, budget) < sum_min:
        return InverseInfeasible(
            reason_code=REASON_BUDGET_BELOW_MINIMUM,
            binding_constraint=BINDING_BUDGET,
            binding_stage_indices=[],
            max_achievable_net_delta_v=None,
            min_achievable_net_delta_v=None,
            detail=(
                f"推进剂总预算 {budget:g} kg 低于各级加注下限之和 "
                f"{sum_min:g} kg，不存在任何合法加注组合"
            ),
            iterations=solver.sweeps,
        )

    # 全下限构型（总推进剂最省构型，也是净速度增量的自然下界参照）
    result_at_min = evaluator.evaluate(mins)
    net_at_min = result_at_min.net_delta_v

    # 约束域内能达到的最大净速度增量（多起点坐标上升）
    max_prop, result_max = solver.optimize_net(maximize=True)
    net_max = result_max.net_delta_v

    num_slack = 1e-9 * max(1.0, abs(target))

    # 结局二（上侧）：目标高出可达上界，灌到最满也不够
    if net_max < target - tol - num_slack:
        binding, at_max = _diagnose_binding(problem, max_prop)
        if binding == BINDING_BUDGET:
            detail = (
                f"把各级加注到约束允许的最满（总加注 "
                f"{math.fsum(max_prop):g} kg，已触预算 {budget:g} kg），"
                f"前向内核算净速度增量仅 {net_max:.6g} m/s，仍达不到目标 "
                f"{target:g} m/s（容差 {tol:g} m/s）：被推进剂总预算卡死。"
            )
        elif binding == BINDING_BUDGET_AND_STAGE_MAX:
            detail = (
                f"把各级加注到上限并耗尽预算 {budget:g} kg（触上限的级："
                f"{at_max}），净速度增量仅 {net_max:.6g} m/s，仍达不到目标 "
                f"{target:g} m/s：预算与这些级的加注上限同时卡死。"
            )
        elif binding == BINDING_STAGE_MAX:
            detail = (
                f"把第 {at_max} 级加注到上限后，净速度增量仅 {net_max:.6g} m/s，"
                f"仍达不到目标 {target:g} m/s（容差 {tol:g} m/s）："
                "被这些级的加注上限卡死。"
            )
        else:
            detail = (
                f"约束域内可达的最大净速度增量仅 {net_max:.6g} m/s，"
                f"达不到目标 {target:g} m/s（容差 {tol:g} m/s）；最优点处"
                "预算与各级上限均未触顶，继续加注的边际速度收益已不抵约束。"
            )
        return InverseInfeasible(
            reason_code=REASON_TARGET_UNREACHABLE,
            binding_constraint=binding,
            binding_stage_indices=at_max,
            max_achievable_net_delta_v=net_max,
            min_achievable_net_delta_v=None,
            detail=detail,
            iterations=solver.sweeps,
        )

    # 结局二（下侧）：最省构型已超目标上沿。再求约束域内净速度增量
    # 最小点确认（若存在更低的净Δv点，容差带仍可能可达）；只有最小点
    # 也超出上沿，才是真正的不可行。
    if net_at_min > target + tol + num_slack:
        min_prop, result_min = solver.optimize_net(maximize=False)
        net_min = result_min.net_delta_v
        if net_min > target + tol + num_slack:
            at_min = [
                i
                for i, (spec, x) in enumerate(zip(specs, min_prop))
                if x <= spec.propellant_min
                + 1e-9 * max(1.0, spec.propellant_min)
            ]
            return InverseInfeasible(
                reason_code=REASON_TARGET_OVERSHOT_AT_MINIMUM,
                binding_constraint=BINDING_STAGE_MIN,
                binding_stage_indices=at_min,
                max_achievable_net_delta_v=net_max,
                min_achievable_net_delta_v=net_min,
                detail=(
                    f"把各级推进剂压到加注下限，前向内核算净速度增量仍有 "
                    f"{net_min:.6g} m/s，高于目标 {target:g} m/s 的容差上沿 "
                    f"{target + tol:.6g} m/s：无法在约束内把速度压到容差带，"
                    "被各级加注下限卡死（请收紧下限或下调目标）。"
                ),
                iterations=solver.sweeps,
            )
        # 最小点已入带或在带下：带内可达，继续走拉格朗日求解

    # 结局一的最省特例：全下限构型本身已在容差带内 ——
    # 加注量不可能比下限更低，它就是总推进剂最省解
    if abs(net_at_min - target) <= tol + num_slack:
        return InverseSolution(
            propellant_masses=mins,
            evaluation=result_at_min,
            iterations=solver.sweeps,
        )

    # 结局一（一般情形）：λ 二分把净速度增量钉在容差带下沿附近
    target_scale = max(1.0, abs(target))
    eps_land = max(
        min(0.25 * tol, 1e-6 * target_scale), 1e-11 * target_scale
    )
    edge_lo = target - tol + eps_land
    edge_hi = target + tol - eps_land
    eps_conv = 0.5 * eps_land

    propellants, result = solver.solve_at_edge(edge_lo, edge_hi, eps_conv)

    # 防御性最终自检：宣称的净速度增量必须真的落在容差带内。
    # 正常路径必然成立；不成立宁可报内部错误，也不假装成功。
    if abs(result.net_delta_v - target) > tol + num_slack:
        raise RocketValidationError(
            INVERSE_FORWARD_MISMATCH,
            (
                f"反解未能在容差内收敛：前向内核回算净速度增量 "
                f"{result.net_delta_v:.12g} m/s 与目标 {target:g} m/s 的偏差 "
                f"{abs(result.net_delta_v - target):.6g} m/s 超出容差 {tol:g} m/s"
            ),
        )

    return InverseSolution(
        propellant_masses=propellants,
        evaluation=result,
        iterations=solver.sweeps,
    )
