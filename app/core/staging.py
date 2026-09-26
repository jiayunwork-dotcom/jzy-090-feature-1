"""多级质量比链。

这是多级核算与单级核算的关键区别所在：

* 各级"点火质量 m0"必须把它上方所有级（含最终有效载荷）全部计入；
* "燃料耗尽质量 mf"等于本级结构质量加上全部上级质量（含有效载荷）；
* 各级独立求 Δv 后逐级相加，绝不能用整箭起飞质量除以最终净载荷做一次对数。

级列表约定自下而上（index 0 为最底部、最早点火的一级）。
本模块只处理质量链，不含任何速度/损失计算。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageMass:
    """单级质量链上的中间量。"""

    index: int
    structural_mass: float   # ms：本级结构质量
    propellant_mass: float   # mp：本级推进剂质量
    upper_mass: float        # 上方所有级 + 最终有效载荷
    m0: float                # 点火质量 = ms + mp + upper_mass
    mf: float                # 耗尽质量 = ms + upper_mass
    mass_ratio: float        # m0 / mf


def build_mass_chain(
    structural_masses: list[float],
    propellant_masses: list[float],
    payload_mass: float,
) -> list[StageMass]:
    """由自下而上的各级质量与顶部有效载荷，逐级构建质量链。

    计算自最顶一级向下回推（上方质量唯一确定本级 m0/mf），最后再按
    自下而上的顺序返回，保证 index 0 是最早点火的一级。
    """
    n = len(structural_masses)
    if len(propellant_masses) != n:
        raise ValueError("结构质量与推进剂质量的级数不一致")

    reversed_masses: list[StageMass] = []
    upper_mass = payload_mass
    for i in range(n - 1, -1, -1):
        ms = structural_masses[i]
        mp = propellant_masses[i]
        mf = ms + upper_mass
        m0 = mf + mp
        # 延迟引入以避免在纯质量模块里制造不必要的耦合方向
        from .physics import mass_ratio as _mass_ratio

        reversed_masses.append(
            StageMass(
                index=i,
                structural_mass=ms,
                propellant_mass=mp,
                upper_mass=upper_mass,
                m0=m0,
                mf=mf,
                mass_ratio=_mass_ratio(m0, mf),
            )
        )
        # 本级分离后，对下一级而言"上方质量"包含本级结构（推进剂已耗尽）
        upper_mass = mf
    return list(reversed(reversed_masses))


def liftoff_mass(chain: list[StageMass]) -> float:
    """整箭起飞质量 = 最底一级的点火质量。"""
    if not chain:
        raise ValueError("质量链为空，无起飞质量")
    return chain[0].m0
