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
    validator.py  # 输入合法性校验（前向构型 + 目标反推请求，独立模块）
    calculator.py # 单次核算 / 批量调度（逐项独立）
    inverse_solver.py # 目标反推求解器：迭代解各级推进剂（只调前向内核）
    inverse.py    # 目标反推的请求映射与服务编排
    reference.py  # 内置两级算例 + 同等结构比单级对照
  schemas/models.py  # Pydantic 接口模型
  api/
    delta_v.py    # POST /api/v1/delta-v
    batch.py      # POST /api/v1/delta-v/batch
    inverse.py    # POST /api/v1/delta-v/inverse（目标反推）
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
- `POST /api/v1/delta-v/inverse`：**目标反推**，给定目标净 Δv 与约束，迭代解出最省的逐级推进剂加注量

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

## 目标反推（`POST /api/v1/delta-v/inverse`）

求解方向与前向相反：前向是"构型给定 → 算出净 Δv"；反推是"目标净 Δv 给定
→ 解各级推进剂"。每级点火质量扛着上方全部级（含推进剂），各级质量比沿
质量链彼此纠缠，没有一步闭式分配，因此求解器**迭代逼近**，且只通过前向
内核 `engine.evaluate` 评估每一次试探构型——自身不抄任何近似火箭方程。
反解结果原样组装回普通构型喂回 `/delta-v`，净 Δv 必须与求解器宣称值
严格一致（测试锁死）。

请求（`stages` 仍自下而上，index 0 最早点火）：

```json
{
  "target_net_delta_v": 4000.0,
  "payload_mass": 2000.0,
  "tolerance": 0.001,
  "propellant_budget": 30000.0,
  "stages": [
    {"ve": 3200, "structural_mass": 2000, "burn_time": 120,
     "propellant_min": 100, "propellant_max": 20000},
    {"ve": 3200, "structural_mass": 2000, "burn_time": 80,
     "propellant_min": 100, "propellant_max": 20000}
  ]
}
```

- `target_net_delta_v`：任务书要求的净速度增量 (m/s)，>= 0；
- 每级给确定量 `ve`、`structural_mass`（结构空重）、`burn_time`（有效工作时间），
  以及该级允许加注推进剂的上下限 `propellant_min` / `propellant_max`；
  下限必须严格为正（零加注会使 m0==mf，无法回代前向核算），且下限不高于上限；
- `tolerance`：达成值与目标允许的偏差带 (m/s)，必须 > 0，缺省 `1e-3`；
- `propellant_budget`：可选推进剂总预算上限 (kg)，不低于各级下限之和。

优化目标：在可行域内找一组加注量使净 Δv 达到目标（偏差落在容差带内），
并使**总推进剂用量最省**。算法为等边际灌水线（拉格朗日乘子一维二分 +
逐级水位二分）逼近 KKT 最省点，再沿射线对前向净 Δv 直接二分精修；
预算受限时用同一条灌水线求预算下最大净 Δv 点。仅用标准库。

三种机器可读结局：

1. **`status="solved"`（HTTP 200）**：返回逐级加注量、逐级质量比与理想 Δv、
   `achieved_net_delta_v`、`deviation`（达成值−目标）、`within_tolerance`、
   `total_propellant_mass`、迭代扫描轮数 `sweeps` 与前向评估次数
   `forward_evaluations`；
2. **`status="infeasible"`（HTTP 200）**：灌满可用加注量仍够不进容差带。
   `limiting_constraint` 指明卡死侧——`"budget"`（预算见底）或
   `"propellant_cap"`（某级顶到加注上限，`stages_at_max` 给出触顶级），
   并附尽力可达的净 Δv、加注量与中文 `reason`，绝不假装成功；
3. **输入不成立（HTTP 422）**：目标为负、有效载荷为负、某级下限高于上限、
   预算为负/非有限、容差非正、预算放不下各级下限之和等，在开算之前以统一
   结构化错误信封挡回，`stage_index`/`field` 定位到具体级与字段。

## 输入校验（非法一律 422 + 中文原因）

`ve <= 0`、`burn_time <= 0`、级数为 0、推进剂为 0（此时 m0==mf，Δv 只能为 0）、
燃料耗尽质量不小于点火质量、负质量、NaN/无穷大、非法阻力规格等，都返回
`{"error":{"code":..., "reason":..., "stage_index":...}}`，绝不产生看似正常的数。

目标反推另有：目标净 Δv 为负（`NEGATIVE_TARGET_DELTA_V`）、加注上下限非正
（`NON_POSITIVE_PROPELLANT_BOUND`）、下限高于上限（`PROPELLANT_BOUNDS_REVERSED`）、
预算为负（`NEGATIVE_PROPELLANT_BUDGET`）、预算低于各级下限之和
（`BUDGET_BELOW_MINIMUM`）、容差非正（`NON_POSITIVE_TOLERANCE`）。

## 被测试锁住的正确性不变量

1. 某级 `ve` 加倍、其余不变 → 该级理想 Δv 严格加倍（内核 + 接口两级都有测试）；
2. 结构质量不变继续加注推进剂 → 质量比升高、Δv 升高；
3. 相同结构质量比下，多级拆分的总 Δv 明显优于把全部推进剂塞进单级（理想值高 10% 以上，净值同样更优）；
4. `m0 == mf` 时该级 Δv 为 0；
5. 多级总量是逐级之和，不等于"起飞质量/净载荷"的一次对数；
6. 批量中各构型结果独立，非法项不覆盖相邻合法项。

目标反推被以下不变量额外锁死（见 `tests/test_inverse_*.py`）：

7. **往返一致**：随机/枚举多组目标与约束，解一遍后把推进剂连同给定空重、
   排气速度、载荷原样组装回普通构型喂回前向（内核与 HTTP 两级都有），
   两侧净 Δv 必须落在同一容差里——反解与前向共用同一套火箭方程与质量链；
8. 单级情形反解与闭式解 `mp=(ms+pl)·(exp((T+g·t)/ve)−1)` 吻合；
9. **最省性**：解点邻域网格/可行弧微调中不存在更省的达标分配；
10. **物理单调性**：目标调高，总推进剂用量不减少；某级排气速度调高（该级
    更出力），达成同一目标的总用量不增多；放宽预算，原本可行的目标不会
    变成不可行；
11. 不可行必须如实区分 `budget` 与 `propellant_cap` 两种卡死侧，且尽力点
    本身满足各级上限与预算约束。
