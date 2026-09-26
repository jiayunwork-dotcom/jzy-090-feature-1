"""领域错误码与统一的输入校验异常。

物理内核只抛出 ``RocketValidationError``；Web 层把它翻译成带明确原因的
错误响应。错误信息使用中文，便于推进分析组直接核对。
"""

from __future__ import annotations

# ---- 错误码（稳定标识，便于程序化处理）----
NO_STAGES = "NO_STAGES"
NON_FINITE_VALUE = "NON_FINITE_VALUE"
NON_POSITIVE_EXHAUST_VELOCITY = "NON_POSITIVE_EXHAUST_VELOCITY"
NON_POSITIVE_BURN_TIME = "NON_POSITIVE_BURN_TIME"
NEGATIVE_STRUCTURAL_MASS = "NEGATIVE_STRUCTURAL_MASS"
NEGATIVE_PROPELLANT_MASS = "NEGATIVE_PROPELLANT_MASS"
NEGATIVE_PAYLOAD_MASS = "NEGATIVE_PAYLOAD_MASS"
ZERO_PROPELLANT = "ZERO_PROPELLANT"
MF_NOT_LT_M0 = "MF_NOT_LT_M0"
NON_POSITIVE_MF = "NON_POSITIVE_MF"
BAD_DRAG_MODE = "BAD_DRAG_MODE"
NON_POSITIVE_DRAG_VALUE = "NON_POSITIVE_DRAG_VALUE"
EMPTY_BATCH = "EMPTY_BATCH"
REQUEST_VALIDATION = "REQUEST_VALIDATION"


class RocketValidationError(ValueError):
    """输入不满足核算前置条件时抛出。

    属性
    ----
    code: 机器可读错误码
    reason: 中文明确原因
    stage_index: 若错误属于某一级，给出该级下标（从 0 开始，自下而上）
    field: 出错字段名，可空
    """

    def __init__(
        self,
        code: str,
        reason: str,
        *,
        stage_index: int | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.stage_index = stage_index
        self.field = field

    def to_detail(self) -> dict[str, object]:
        detail: dict[str, object] = {"code": self.code, "reason": self.reason}
        if self.stage_index is not None:
            detail["stage_index"] = self.stage_index
        if self.field is not None:
            detail["field"] = self.field
        return detail
