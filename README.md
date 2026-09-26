# 多级火箭速度增量核算引擎

常驻的齐奥尔科夫斯基预算 HTTP 服务（Python 3.12 + FastAPI，仅用标准库做计算）。
范围严格限定在**质量比与速度增量核算**，无前端、无账户体系。

## 物理模型

每一级（`stages` 自下而上排列，index 0 为最早点火的一级）给定：

- `ve`：有效排气速度 (m/s)
- `structural_mass`：结构质量 (kg)
- `propellant_mass`：推进剂质量 (kg)
- `burn_time`：有效工作时间 (s)
- 顶部另有 `payload_mass`：最终有效载荷 (kg)

逐级计算（`m0` 必须计入**上方全部级 + 有效载荷**）：

```
m0   = 本级结构 + 本级推进剂 + 上方全部质量
mf   = 本级结构            + 上方全部质量
Δv_i = ve · ln(m0 / mf)
理想总Δv = Σ Δv_i            # 逐级相加，绝不拿起飞质量/净载荷做一次对数
重力损失 = g · Σ t_burn       # g = 9.80665 m/s²，竖直起飞简化
阻力损失 = 可选（none / explicit / linear）
净 Δv   = 理想总Δv − 重力损失 − 阻力损失   # 损失只会让净值变小
```

## 模块组织（按职责拆分）

```
app/
  core/
    physics.py    # 单级火箭方程 Δv = ve·ln(m0/mf)
    staging.py    # 多级质量比链（m0/mf 计入上方全部质量）
    losses.py     # 重力损失扣减 + 可选轻量阻力
    engine.py     # 编排：一次完整构型核算
    constants.py  # g、端口
    errors.py     # 错误码与 RocketValidationError
  services/
    validator.py  # 输入合法性校验（独立模块）
    calculator.py # 单次核算 / 批量调度（逐项独立）
    reference.py  # 内置两级算例 + 同等结构比单级对照
  schemas/models.py  # Pydantic 接口模型
  api/
    delta_v.py    # POST /api/v1/delta-v
    batch.py      # POST /api/v1/delta-v/batch
    reference.py  # GET  /api/v1/reference/example
  main.py         # FastAPI 装配、统一错误信封、启动打印算例
tests/            # 锁住不变量的自动化测试
```

## 启动与容器化

```bash
docker build -t rocket-dv .
docker run --rm -p 8000:8000 rocket-dv     # 容器内固定监听 8000
```

容器启动时会直接载入内置两级算例并打印逐级质量比、理想/净 Δv 及单级对照，供手工核对。

容器内执行自动化测试：

```bash
docker run --rm rocket-dv pytest -q
# 或本地：pip install -r requirements.txt -r requirements-dev.txt && pytest -q
```

## 接口

- `GET  /health`：存活探针
- `GET  /`：服务与端点信息
- `GET  /api/v1/reference/example`：内置算例（两级 vs 同结构比单级）
- `POST /api/v1/delta-v`：单次构型核算，返回逐级质量比、逐级 Δv、重力损失、净值
- `POST /api/v1/delta-v/batch`：批量比选，各构型结果独立；单项非法只标记该项

单次请求示例：

```json
{
  "stages": [
    {"ve": 3200, "structural_mass": 2000, "propellant_mass": 8000, "burn_time": 120},
    {"ve": 3200, "structural_mass": 2000, "propellant_mass": 8000, "burn_time": 80}
  ],
  "payload_mass": 2000,
  "drag": {"mode": "none"}
}
```

阻力可选项：`{"mode":"explicit","value":150}`（直接扣 150 m/s）或
`{"mode":"linear","fraction":0.02}`（按理想 Δv 的 2% 线性近似）。

## 输入校验（非法一律 422 + 中文原因）

`ve <= 0`、`burn_time <= 0`、级数为 0、推进剂为 0（此时 m0==mf，Δv 只能为 0）、
燃料耗尽质量不小于点火质量、负质量、NaN/无穷大、非法阻力规格等，都返回
`{"error":{"code":..., "reason":..., "stage_index":...}}`，绝不产生看似正常的数。

## 被测试锁住的正确性不变量

1. 某级 `ve` 加倍、其余不变 → 该级理想 Δv 严格加倍（内核 + 接口两级都有测试）；
2. 结构质量不变继续加注推进剂 → 质量比升高、Δv 升高；
3. 相同结构质量比下，多级拆分的总 Δv 明显优于把全部推进剂塞进单级（理想值高 10% 以上，净值同样更优）；
4. `m0 == mf` 时该级 Δv 为 0；
5. 多级总量是逐级之和，不等于"起飞质量/净载荷"的一次对数；
6. 批量中各构型结果独立，非法项不覆盖相邻合法项。
