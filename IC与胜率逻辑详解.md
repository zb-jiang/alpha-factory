# IC与胜率逻辑详解

> 本文档基于工程代码，详细解释从因子评估到回测买卖的完整逻辑链条。
> 所有结论均有代码依据，不做猜测性说明。

---

## 一、所有概念一览表

在开始详细解释之前，先列出本文涉及的所有概念：

| 概念 | 英文名 | 定义 | 计算位置 |
|-----|-------|------|---------|
| **IC** | Information Coefficient | 因子值与未来收益的相关性系数 | common.py |
| **mean_ic** | Mean IC | 每日IC的平均值 | common.py |
| **mean_rank_ic** | Mean Rank IC | 每日Rank IC的平均值（使用排序相关性） | common.py |
| **ic_ir** | IC Information Ratio | IC均值除以IC标准差，衡量IC稳定性 | common.py |
| **rank_ic_ir** | Rank IC IR | Rank IC均值除以Rank IC标准差 | common.py |
| **positive_ic_ratio** | Positive IC Ratio | IC为正的天数占总天数的比例 | common.py |
| **win_rate** | Win Rate | 规律稳定的天数占比（回测筛选时计算） | step08_backtest.py |
| **direction** | Direction | LLM对因子方向的猜测（higher_better或lower_better） | LLM生成 |
| **adjusted_score** | Adjusted Score | 根据direction调整后的因子值 | step07_eval_factor.py |
| **raw_score** | Raw Score | 原始因子值，未经方向调整 | step07_eval_factor.py |

---

## 二、概念详解

### 2.1 IC (Information Coefficient)

**定义**：因子值与未来收益的相关性系数。

**计算代码**（`common.py:565-568`）：
```python
# 普通IC：Pearson相关系数
ic_daily = merged.groupby(level="datetime").apply(
    lambda frame: frame["score"].corr(frame["label"])
)

# Rank IC：Spearman相关系数（使用排序）
rank_ic_daily = merged.groupby(level="datetime").apply(
    lambda frame: frame["score"].corr(frame["label"], method="spearman")
)
```

**含义**：
- `IC > 0`：因子值越大，未来收益越高（正相关）
- `IC < 0`：因子值越大，未来收益越低（负相关）
- `IC = 0`：因子与收益无相关性

**普通IC vs Rank IC**：
- 普通IC：对异常值敏感
- Rank IC：先排序再算相关，更稳健，**工程中主要使用Rank IC**

---

### 2.2 mean_ic 和 mean_rank_ic

**定义**：每日IC（或Rank IC）的平均值。

**计算代码**（`common.py:571-572`）：
```python
mean_ic = float(ic_daily.mean() or 0.0) if not ic_daily.empty else 0.0
mean_rank_ic = float(rank_ic_daily.mean() or 0.0) if not rank_ic_daily.empty else 0.0
```

**含义**：
- 衡量因子的**平均预测能力**
- `|mean_rank_ic|` 越大，因子效果越强
- 行业标准：`|mean_rank_ic| >= 0.02` 算有效因子

---

### 2.3 ic_ir 和 rank_ic_ir

**定义**：IC的信息比率 = IC均值 / IC标准差。

**计算代码**（`common.py:575-576`）：
```python
ic_std = float(ic_daily.std(ddof=0) or 0.0) if not ic_daily.empty else 0.0
rank_ic_std = float(rank_ic_daily.std(ddof=0) or 0.0) if not rank_ic_daily.empty else 0.0

"ic_ir": float(mean_ic / ic_std) if ic_std else 0.0,
"rank_ic_ir": float(mean_rank_ic / rank_ic_std) if rank_ic_std else 0.0,
```

**含义**：
- 衡量IC的**稳定性**
- `|rank_ic_ir|` 越大，IC越稳定
- 行业标准：`|rank_ic_ir| >= 0.2` 算稳定

**为什么用IR而不是直接用标准差？**
- IR = 收益（IC均值）/ 风险（IC标准差）
- 类似夏普比率，是风险调整后的收益
- 高IC均值 + 低IC波动 = 高IR = 好因子

---

### 2.4 positive_ic_ratio

**定义**：IC为正的天数占总天数的比例。

**计算代码**（`common.py:582`）：
```python
"positive_ic_ratio": float((ic_daily > 0).mean()) if not ic_daily.empty else 0.0,
```

**含义**：
- 衡量IC的**方向一致性**
- `positive_ic_ratio = 0.8`：80%的天数IC为正
- `positive_ic_ratio = 0.2`：20%的天数IC为正（即80%的天数IC为负）

**重要提示**：
- 这个名称有误导性，它**不是**"预测正确的比例"
- 它只是统计"IC为正的天数占比"

---

### 2.5 win_rate（胜率）

**定义**：规律稳定的天数占比。

**计算代码**（`step08_backtest.py:185-186`）：
```python
is_negative_ic = mean_rank_ic < 0
win_rate = pos_ratio if not is_negative_ic else (1 - pos_ratio)
```

**含义**：
- 衡量因子的**实际稳定性**
- 是回测筛选时使用的**核心指标**

**计算逻辑**：

| mean_rank_ic符号 | is_negative_ic | win_rate计算 | 含义 |
|-----------------|----------------|-------------|------|
| > 0（IC整体为正） | False | `positive_ic_ratio` | IC为正的天数占比 |
| < 0（IC整体为负） | True | `1 - positive_ic_ratio` | IC为负的天数占比 |

**为什么这样算？**

因为`positive_ic_ratio`只统计"IC为正的比例"：
- 当IC整体为正时，IC为正 = 规律稳定
- 当IC整体为负时，IC为负 = 规律稳定
- 所以需要根据IC方向切换计算方式

**阈值**：`win_rate >= 0.4` 才能进入回测

---

### 2.6 direction（因子方向）

**定义**：LLM对因子方向的猜测。

**来源**：LLM生成因子时给出

**取值**：
- `higher_better`：因子值越大越好（因子大→收益高）
- `lower_better`：因子值越小越好（因子小→收益高）

**重要提示**：
- 这只是LLM的**猜测**，不是事实
- 数据验证后可能发现方向相反
- 回测代码**不使用**这个字段

---

### 2.7 raw_score 和 adjusted_score

**定义**：
- `raw_score`：根据公式计算的原始因子值
- `adjusted_score`：根据direction调整后的因子值

**计算代码**（`step07_eval_factor.py:28-31`）：
```python
raw_score = evaluate_formula(str(item["formula"]), factor_input)
adjusted_score = raw_score if item["direction"] == "higher_better" else -raw_score
```

**调整逻辑**：

| direction | adjusted_score | 目的 |
|-----------|---------------|------|
| higher_better | raw_score（不变） | 已经是"越大越好" |
| lower_better | -raw_score（取反） | 转换为"越大越好" |

**为什么要调整？**
- 统一所有因子的方向
- 后续IC计算和回测逻辑可以统一处理
- 所有因子的`adjusted_score`都是"越大越好"

---

## 三、概念之间的关系图

```
┌─────────────────────────────────────────────────────────────────┐
│                    LLM生成因子                                    │
│  输出：formula, direction (higher_better / lower_better)         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              step07: 因子评估                                     │
│  1. 计算 raw_score（原始因子值）                                  │
│  2. 根据 direction 调整 → adjusted_score                         │
│  3. 计算 IC 指标：                                                │
│     - mean_ic, mean_rank_ic（预测能力）                          │
│     - ic_ir, rank_ic_ir（稳定性）                                │
│     - positive_ic_ratio（方向一致性）                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              step08: 回测筛选                                     │
│  1. 根据 mean_rank_ic 判断 is_negative_ic                        │
│  2. 计算 win_rate：                                               │
│     - IC为正：win_rate = positive_ic_ratio                       │
│     - IC为负：win_rate = 1 - positive_ic_ratio                   │
│  3. 筛选条件：                                                    │
│     - |mean_rank_ic| >= 0.01                                     │
│     - |rank_ic_ir| >= 0.1                                        │
│     - win_rate >= 0.4                                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│              step08: 回测买卖                                     │
│  1. 读取 adjusted_score                                          │
│  2. 如果 is_negative_ic 为 True，翻转：score = -adjusted_score   │
│  3. 按 score 排序，买入前 N 名                                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## 四、四个典型场景分析

### 场景A：IC稳定为正

```
mean_rank_ic = +0.05
rank_ic_ir = +0.35
positive_ic_ratio = 0.8
```

**计算win_rate**：
- `is_negative_ic = False`（因为 mean_rank_ic > 0）
- `win_rate = positive_ic_ratio = 0.8`

**筛选结果**：
- |mean_rank_ic| = 0.05 >= 0.01 ✅
- |rank_ic_ir| = 0.35 >= 0.1 ✅
- win_rate = 0.8 >= 0.4 ✅
- **结论：通过筛选**

**回测行为**：
- `is_negative_ic = False`，不翻转
- 买入 adjusted_score 大的股票

---

### 场景B：IC稳定为负

```
mean_rank_ic = -0.05
rank_ic_ir = -0.35
positive_ic_ratio = 0.2
```

**计算win_rate**：
- `is_negative_ic = True`（因为 mean_rank_ic < 0）
- `win_rate = 1 - positive_ic_ratio = 1 - 0.2 = 0.8`

**筛选结果**：
- |mean_rank_ic| = 0.05 >= 0.01 ✅
- |rank_ic_ir| = 0.35 >= 0.1 ✅
- win_rate = 0.8 >= 0.4 ✅
- **结论：通过筛选**

**回测行为**：
- `is_negative_ic = True`，翻转因子值
- 买入 adjusted_score 小的股票（翻转后变成大的）

---

### 场景C：IC为正但不稳定

```
mean_rank_ic = +0.02
rank_ic_ir = +0.08
positive_ic_ratio = 0.55
```

**计算win_rate**：
- `is_negative_ic = False`
- `win_rate = positive_ic_ratio = 0.55`

**筛选结果**：
- |mean_rank_ic| = 0.02 >= 0.01 ✅
- |rank_ic_ir| = 0.08 < 0.1 ❌
- **结论：被淘汰（IR太低）**

---

### 场景D：IC为负但不稳定

```
mean_rank_ic = -0.02
rank_ic_ir = -0.08
positive_ic_ratio = 0.45
```

**计算win_rate**：
- `is_negative_ic = True`
- `win_rate = 1 - positive_ic_ratio = 0.55`

**筛选结果**：
- |mean_rank_ic| = 0.02 >= 0.01 ✅
- |rank_ic_ir| = 0.08 < 0.1 ❌
- **结论：被淘汰（IR太低）**

---

## 五、常见误区澄清

### 误区1：positive_ic_ratio = 预测正确的比例

**正解**：positive_ic_ratio 只是"IC为正的天数占比"。

- 对于正向因子（IC > 0），IC为正 = 规律稳定
- 对于负向因子（IC < 0），IC为负 = 规律稳定
- 所以需要用 win_rate 来统一衡量

---

### 误区2：IC为正 = LLM猜对了

**正解**：IC的正负与LLM的direction无关。

- IC反映的是 adjusted_score 与收益的相关性
- adjusted_score 已经根据 direction 调整过
- IC为正只表示"adjusted_score越大收益越高"

---

### 误区3：回测时会根据LLM的direction买卖

**正解**：回测代码完全不看direction，只看IC正负。

- direction 只在 step07 用于调整因子值
- 回测时只根据 is_negative_ic 决定是否翻转
- is_negative_ic 由 mean_rank_ic 决定，与 direction 无关

---

### 误区4：win_rate 和 positive_ic_ratio 是同一个东西

**正解**：它们相关但不同。

- positive_ic_ratio：原始统计值，"IC为正的天数占比"
- win_rate：根据IC方向调整后的值，"规律稳定的天数占比"
- 只有当IC为正时，两者才相等

---

## 六、代码引用汇总

| 概念 | 文件 | 行号 |
|-----|------|------|
| IC计算 | common.py | 565-568 |
| mean_ic, mean_rank_ic | common.py | 571-572 |
| ic_ir, rank_ic_ir | common.py | 575-576 |
| positive_ic_ratio | common.py | 582 |
| direction调整 | step07_eval_factor.py | 28-31 |
| win_rate计算 | step08_backtest.py | 185-186 |
| 因子翻转 | step08_backtest.py | 66-67 |
| 筛选条件 | step08_backtest.py | 168-191 |

---

## 七、总结：一句话记住每个概念

| 概念 | 一句话记忆 |
|-----|-----------|
| IC | 因子和收益的相关性，正=正相关，负=负相关 |
| mean_rank_ic | IC的平均值，衡量预测能力 |
| rank_ic_ir | IC的稳定性，越高越稳定 |
| positive_ic_ratio | IC为正的天数占比（注意：不是胜率） |
| win_rate | 真正的胜率，规律稳定的天数占比 |
| direction | LLM的猜测，仅供参考 |
| adjusted_score | 统一方向后的因子值，都是"越大越好" |

---

*文档生成时间：基于代码分析*
*所有结论均有代码依据*
