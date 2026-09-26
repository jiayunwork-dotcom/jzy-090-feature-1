"""单级物理内核：齐奥尔科夫斯基火箭方程。

仅依赖标准库 math。这里只做"纯物理量"计算，不感知 Web/批量/校验框架，
因此内核函数可被测试与其它模块长期复用。
"""

from __future__ import annotations

import math


def mass_ratio(m0: float, mf: float) -> float:
    """返回质量比 m0/mf。

    - mf 非正无物理意义（耗尽后质量必须为正），直接拒绝；
    - m0 == mf 时返回 1.0，其对应的理想速度增量为 0；
    - m0 < mf 不属于正常构型（推进剂质量为负），由校验层提前拦截，
      内核仍做防御性拒绝，避免产生看似正常的结果。
    """
    if mf <= 0:
        raise ValueError("燃料耗尽质量 mf 必须为正数才能计算质量比")
    if m0 < mf:
        raise ValueError("点火质量 m0 不得小于燃料耗尽质量 mf")
    return m0 / mf


def ideal_stage_delta_v(ve: float, m0: float, mf: float) -> float:
    """单级理想速度增量 Δv = ve · ln(m0/mf)。

    当燃料耗尽质量恰好等于点火质量（m0 == mf，即本级无推进剂）时，
    ln(1) = 0，该级理想速度增量严格为 0。
    ve 必须为正，由校验层保证；内核同样做防御性检查。
    """
    if ve <= 0:
        raise ValueError("有效排气速度 ve 必须为正数")
    ratio = mass_ratio(m0, mf)
    return ve * math.log(ratio)
