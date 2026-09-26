"""物理内核不变量：齐奥尔科夫斯基方程本身。

重点锁定：
* ve 加倍 => 该级理想 Δv 严格加倍；
* 结构质量约束内继续加注推进剂 => 质量比升高、Δv 升高；
* m0 == mf => 理想 Δv 为 0；
* Δv = ve·ln(m0/mf) 的数值正确性。
"""

from __future__ import annotations

import math

import pytest

from app.core.physics import ideal_stage_delta_v, mass_ratio


def test_rocket_equation_basic_value():
    ve, m0, mf = 3000.0, 10000.0, 3000.0
    assert ideal_stage_delta_v(ve, m0, mf) == pytest.approx(ve * math.log(m0 / mf))
    assert mass_ratio(m0, mf) == pytest.approx(m0 / mf)


def test_ve_doubling_doubles_stage_ideal_delta_v():
    # 不变量①：某一级 ve 加倍、其余不变，该级理想 Δv 随之加倍
    m0, mf = 12000.0, 4000.0
    base = ideal_stage_delta_v(3000.0, m0, mf)
    doubled = ideal_stage_delta_v(6000.0, m0, mf)
    assert doubled == pytest.approx(2.0 * base)


def test_ve_doubling_halves_returns_zero():
    # 退化情形（无推进剂）下加倍 ve 仍然为 0，不会凭空产生速度增量
    assert ideal_stage_delta_v(3000.0, 5000.0, 5000.0) == 0.0
    assert ideal_stage_delta_v(6000.0, 5000.0, 5000.0) == 0.0


def test_more_propellant_raises_mass_ratio_and_delta_v():
    # 不变量②：结构质量不变，继续加注推进剂 => 质量比升高、Δv 升高
    ve = 3000.0
    structural_and_upper = 4000.0  # mf 固定（结构 + 上方质量）
    prev_ratio = 0.0
    prev_dv = -1.0
    for mp in (1000.0, 4000.0, 8000.0, 12000.0):
        m0 = structural_and_upper + mp
        r = mass_ratio(m0, structural_and_upper)
        dv = ideal_stage_delta_v(ve, m0, structural_and_upper)
        assert r > prev_ratio
        assert dv > prev_dv
        prev_ratio, prev_dv = r, dv


def test_zero_delta_v_when_mf_equals_m0():
    # 不变量④：燃料耗尽质量恰好等于点火质量时该级速度增量为零
    assert ideal_stage_delta_v(3200.0, 7777.0, 7777.0) == 0.0


def test_kernel_rejects_nonpositive_mf_and_m0_lt_mf():
    with pytest.raises(ValueError):
        mass_ratio(1000.0, 0.0)
    with pytest.raises(ValueError):
        ideal_stage_delta_v(3000.0, 1000.0, 2000.0)
    with pytest.raises(ValueError):
        ideal_stage_delta_v(0.0, 3000.0, 1000.0)
