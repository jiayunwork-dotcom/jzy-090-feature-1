"""核算引擎的物理常量与默认取值。"""

# 标准重力加速度（地球表面），单位 m/s^2。
# 重力损失按竖直起飞的简化模型取 g * t_burn。
STANDARD_GRAVITY = 9.80665

# 服务对外固定监听端口
SERVICE_PORT = 8000

# 目标反推：净速度增量达成值与目标之间允许的偏差缺省值 (m/s)
DEFAULT_INVERSE_TOLERANCE = 1.0e-3

# 目标反推：循环坐标迭代的最大扫描轮数（每轮对所有级各做一次一维逼近）
DEFAULT_INVERSE_MAX_SWEEPS = 100
