# 多级火箭速度增量核算引擎

常驻的齐奥尔科夫斯基预算 HTTP 服务（Python 3.12 + FastAPI，仅用标准库做计算）。
范围严格限定在**质量比与速度增量核算**，无前端、无账户体系。

除"构型完整给定 → 前向算出速度增量"的核算方向外，还提供**给目标反推构型**：
给定目标净速度增量、有效载荷、各级空重/排气速度/工作时间/加注上下限与可选
总预算，迭代解出总推进剂最省的各级加注量。

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
    engine.py     # 编排：一次完整构型核算（反解也调用它，唯一物理真相源）
    constants.py  # g、端口
    errors.py     # 错误码与 RocketValidationError
  solver/
    inverse.py    # 反解求解器：拉格朗日坐标下降 + 乘子二分（只依赖 core.engine）
  services/
    validator.py          # 前向输入合法性校验（独立模块）
    inverse_validator.py  # 反解请求合法性校验（独立模块）
    calculator.py # 单次核算 / 批量调度（逐项独立）
    inverse.py    # 反解调度：校验 → 求解 → 三种结局序列化
    reference.py  # 内置两级算例 + 同等结构比单级对照
  schemas/models.py  # Pydantic 接口模型
  api/
    delta_v.py    # POST /api/v1/delta-v
    batch.py      # POST /api/v1/delta-v/batch
    inverse.py    # POST /api/v1/inverse/solve
    reference.py  # GET  /api/v1/reference/example
  main.py         # FastAPI 装配、统一错误信封、启动打印算例
tests/            # 锁住不变量的自动化测试
```

依赖方向：`api → services → solver → core.engine`，以及前向的
`api → services.calculator → core.engine`；**前向链路不反向依赖 solver**。
反解每次试探都调用前向内核 `engine.evaluate`，两侧共用同一套火箭方程与
同一条质量链。

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
- `POST /api/v1/inverse/solve`：**给目标反推构型**，解出总推进剂最省的各级加注量

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

## 给目标反推构型（反解）

`POST /api/v1/inverse/solve`：工程师报出**目标净速度增量**、有效载荷、
各级确定量（有效排气速度、结构空重、有效工作时间、允许加注推进剂的
上下限）与可选的推进剂总预算，引擎在约束内解出**总推进剂最省**的一组
各级加注量，使整支构型按前向内核算出的净速度增量落入目标容差带
（`tolerance` 随请求给定，缺省 1 m/s）。

```json
{
  "target_delta_v": 4200,
  "payload_mass": 2000,
  "propellant_budget": null,
  "tolerance": 1.0,
  "stages": [
    {"ve": 3200, "structural_mass": 2000, "burn_time": 120,
     "propellant_min": 0, "propellant_max": 20000},
    {"ve": 3200, "structural_mass": 2000, "burn_time": 80,
     "propellant_min": 0, "propellant_max": 20000}
  ],
  "drag": {"mode": "none"}
}
```

求解器只依赖标准库：对拉格朗日函数 `Σx − λ·净Δv` 做多起点坐标下降
（固定 λ 时沿每级坐标严格凸，驻点由同一套火箭方程的解析导数定位），
再对 λ 倍增夹取 + 二分，把解钉在容差带下沿；每次试探构型一律调用
`core.engine.evaluate` 评估。预算贴边时额外做级间保总量腾挪步。

响应如实区分**三种结局**（HTTP 均为 200/422，机器可读）：

1. `"status": "solved"`：逐级加注量、逐级质量比与 Δv、总推进剂、
   达成净速度增量 `achieved_net_delta_v`、与目标的 `deviation`、
   迭代轮数 `iterations`；
2. `"status": "infeasible"`：`reason_code`
  （`TARGET_UNREACHABLE` / `TARGET_OVERSHOT_AT_MINIMUM` /
   `BUDGET_BELOW_MINIMUM`）+ `binding_constraint`
  （`propellant_budget` / `stage_propellant_max` /
   `propellant_budget+stage_propellant_max` / `stage_propellant_min`）
  + `binding_stage_indices` + `max_achievable_net_delta_v` 等诊断量，
  绝不返回差得离谱却假装成功的解；
3. 输入不成立（负目标、负载荷、非有限值、下限高于上限、负预算、
   非正容差、级数为 0 等）：开算前 422 + 统一错误信封，
   `stage_index`/`field` 定位到具体级与字段。

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
6. 批量中各构型结果独立，非法项不覆盖相邻合法项；
7. **反解往返一致**：反解出的加注量原样组装回喂前向内核（随机 24 组内核级 + 10 组 HTTP 全链路 + 内置算例），两侧净 Δv、逐级质量比/Δv 严格对得上，且落入目标容差带、约束全满足；
8. **反解的物理单调性**：目标调高 → 总推进剂不变少（单/双/三级、含线性阻力）；某级 ve 调高 → 达成同一目标的总推进剂不变多；预算放宽 → 原本可行的目标不会突然不可行、总用量不变多；
9. **反解三种结局诚实**：可行解逐级结果齐备；不可行时给出原因码与卡死侧（预算/级上限/双重/下限/预算低于下限），其"最大可达净 Δv"与独立前向核算一致；非法输入开算前以结构化信封挡回；
10. **依赖方向**：`core/` 与 `api/`、前向服务模块不反向 import 求解器（测试扫描源码锁死）。
