# 择时过滤模块（Market Timing）设计文档

## 1. 概述

择时过滤模块是一个**叠加层**，不是独立选股策略。它在主策略（SoftTopK / TopKDropout）给出目标持仓后，根据市场状态统一调整总仓位和开仓条件。

**核心原则**：
- 主策略负责选股和权重分配，择时模块只做"减法"——缩放仓位、过滤开仓
- 择时模块不改变主策略的选股逻辑，只影响最终执行的仓位大小和是否允许新开仓
- 两层择时独立生效：市场级择时影响整体仓位，个股级择时影响单票是否允许新开仓

## 2. 配置参数

```yaml
MarketTiming:
  enabled: false                    # 是否启用择时过滤
  market_indicator: EMA_60          # 市场级择时指标类型
  reduce_to: 0.5                    # 风险状态下目标暴露比例
  stock_open_filter: rsi            # 个股开仓过滤器类型
  stock_ema_period: 60              # 个股EMA过滤周期
  rsi_period: 14                    # RSI计算窗口
  rsi_buy_max: 70.0                 # RSI开仓上限
```

### 参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | false | 是否启用择时过滤 |
| `market_indicator` | string | EMA_60 | 市场级择时指标，当前仅支持 EMA_60 |
| `reduce_to` | float | 0.5 | 风险状态下目标暴露比例（0~1） |
| `stock_open_filter` | string | rsi | 个股开仓过滤器：none / ema / rsi |
| `stock_ema_period` | int | 60 | 个股EMA过滤周期（仅 ema 模式生效） |
| `rsi_period` | int | 14 | RSI计算窗口（仅 rsi 模式生效） |
| `rsi_buy_max` | float | 70.0 | RSI开仓上限（仅 rsi 模式生效） |

## 3. 架构设计

### 3.1 整体流程

在信号日（调仓日），主策略先根据因子分数和当前持仓计算出原始的目标持仓计划（TradePlan）。随后，择时过滤模块介入，根据市场状态和个股条件对目标持仓计划进行调整：如果市场处于风险状态，则缩放整体仓位；如果个股不满足开仓条件，则过滤掉对应的新开仓。调整后的目标持仓计划被存储，等待在下一个交易日执行。

在交易日（调仓日+1），执行层按照调整后的目标持仓计划进行交易。

### 3.2 模块结构

择时过滤作为独立模块 `engine/market_timing.py`，不拆分为 soft_topk 和 topk_dropout 两个文件。因为择时过滤是叠加层，它操作的是 TradePlan 对象，不关心是哪个策略生成的——一个 MarketTimingFilter 类适用于所有策略。

需要修改的文件：
- `engine/__init__.py`：BacktestEngine 中调用择时过滤
- `engine/market_timing.py`：MarketTimingFilter（新增）

不需要修改的文件：
- `engine/soft_topk.py`：SoftTopKStrategy 不变
- `engine/topk_dropout.py`：TopKDropoutStrategy 不变

**关键设计决策**：策略代码（soft_topk.py / topk_dropout.py）不需要任何修改。择时过滤作为独立模块，在 BacktestEngine 的 `_on_signal_day` 中，主策略生成 TradePlan 之后介入。

### 3.3 MarketTimingFilter 类

```python
class MarketTimingFilter:
    def __init__(
        self,
        enabled: bool = False,
        market_indicator: str = "EMA_60",
        reduce_to: float = 0.5,
        stock_open_filter: str = "none",
        stock_ema_period: int = 60,
        rsi_period: int = 14,
        rsi_buy_max: float = 70.0,
    ) -> None: ...

    def apply(
        self,
        plan: TradePlan,
        market: MarketData,
        positions: dict[str, Position],
        signal_date: pd.Timestamp,
    ) -> TradePlan: ...
```

## 4. 市场级择时（EMA_60）

### 4.1 指标定义

**EMA_60**：当股票池合成基准的收盘价 < 60日EMA 时，视为风险抬升阶段。

**合成基准**：对股票池中所有股票的收盘价做等权平均，生成一条"合成基准价格序列"。

### 4.2 计算方法

1. **合成基准收盘价**：每个交易日，取股票池中所有有收盘价的股票，计算等权平均
   ```
   benchmark_close[t] = mean(close_i[t])  for all i in stock_pool where close_i[t] is valid
   ```

2. **60日EMA**：对合成基准收盘价序列计算60日指数移动平均
   ```
   EMA_60[t] = alpha * benchmark_close[t] + (1 - alpha) * EMA_60[t-1]
   alpha = 2 / (60 + 1)
   ```

3. **风险判定**：
   - `benchmark_close[t] < EMA_60[t]` → 风险状态（bearish）
   - `benchmark_close[t] >= EMA_60[t]` → 正常状态（bullish）

### 4.3 仓位缩放逻辑

当市场处于风险状态时，将所有目标权重乘以 `reduce_to`：

```
if benchmark_close < EMA_60:
    for each code in target_weights:
        target_weights[code] *= reduce_to
    # 剩余 1 - reduce_to 比例变为现金
```

**示例**：`reduce_to = 0.5`

| 股票 | 原始权重 | 风险状态权重 | 差额 |
|------|---------|-------------|------|
| A | 6% | 3% | 卖出3% |
| B | 4% | 2% | 卖出2% |
| C | 5% | 2.5% | 卖出2.5% |
| 现金 | 0% | 50% | - |

### 4.4 对不同策略的影响

**SoftTopK**：
- 所有目标权重等比例缩放，权重分配比例不变
- 例如：原权重 [6%, 4%, 5%] → 缩放后 [3%, 2%, 2.5%]

**TopKDropout**：
- 继续持有的股票：当前权重 > 目标权重 × reduce_to 时，产生部分卖出
- 新买入的股票：目标权重 = 等权 × reduce_to
- 卖出的股票：仍然全部卖出（权重 = 0 × reduce_to = 0）

**关键**：TopKDropout 的"继续持有不动"原则在择时缩放时需要打破——即使继续持有的股票，如果当前权重超过缩放后的目标权重，也需要部分卖出。这是择时的核心目的：控制整体暴露。

### 4.5 仓位缩放对 TradePlan 的修改

```python
def _apply_market_timing(self, plan, market, signal_date):
    if not self._is_risk_state(market, signal_date):
        return plan  # 正常状态，不修改

    new_target_weights = {}
    for code, weight in plan.target_weights.items():
        new_target_weights[code] = weight * self.reduce_to

    new_to_sell = {}
    for code, weight in plan.to_sell.items():
        new_to_sell[code] = weight * self.reduce_to

    new_to_buy = {}
    for code, weight in plan.to_buy.items():
        new_to_buy[code] = weight * self.reduce_to

    return TradePlan(to_sell=new_to_sell, to_buy=new_to_buy, target_weights=new_target_weights)
```

## 5. 个股级择时（开仓过滤）

### 5.1 设计原则

- **仅对新开仓生效**：老持仓不受个股级择时影响
- **被过滤的股票不替补**：跳过的股票不替换为排名靠后的股票，持仓数可能少于 holding_count
- **与市场级择时叠加**：先做市场级仓位缩放，再做个股级开仓过滤

### 5.2 EMA 过滤

**规则**：仅当个股 `close >= EMA(stock_ema_period)` 时才允许新开仓。

**计算**：
```
stock_ema[t] = alpha * close[t] + (1 - alpha) * stock_ema[t-1]
alpha = 2 / (stock_ema_period + 1)

允许开仓条件：close[t] >= stock_ema[t]
```

**语义**：股价在均线上方，趋势偏多，允许开仓；股价在均线下方，趋势偏空，禁止开仓。

### 5.3 RSI 过滤

**规则**：仅当个股 `RSI(rsi_period) <= rsi_buy_max` 时才允许新开仓。

**计算**：
```
delta = close.diff()
gain = delta.where(delta > 0, 0.0)
loss = (-delta).where(delta < 0, 0.0)

avg_gain = gain.rolling(rsi_period).mean()
avg_loss = loss.rolling(rsi_period).mean()

RS = avg_gain / avg_loss
RSI = 100 - 100 / (1 + RS)

允许开仓条件：RSI <= rsi_buy_max
```

**语义**：RSI过高（如>70）表示短期偏热，追高风险大，禁止开仓；RSI适中或偏低，允许开仓。

### 5.4 过滤逻辑

```python
def _apply_stock_filter(self, plan, market, positions, signal_date):
    if self.stock_open_filter == "none":
        return plan

    new_to_buy = {}
    for code, weight in plan.to_buy.items():
        # 老持仓加仓不受过滤影响
        if code in positions and positions[code].shares > 0:
            new_to_buy[code] = weight
            continue

        # 新开仓需要通过过滤
        if self._is_stock_allowed(code, market, signal_date):
            new_to_buy[code] = weight
        else:
            # 被过滤的股票：从 to_buy 和 target_weights 中移除
            pass

    # 重新归一化 target_weights
    # 被过滤掉的股票权重归零，剩余股票权重等比例放大使总和 = 原始总和
    ...
```

### 5.5 权重归一化问题

个股被过滤后，其目标权重归零。剩余股票的权重需要重新归一化，否则总权重之和 < 1。

**归一化方式**：等比例放大剩余股票权重，使总权重之和等于过滤前的总权重之和。

**示例**（假设市场级择时已缩放到50%）：

| 股票 | 缩放后权重 | RSI | 过滤结果 | 归一化后权重 |
|------|-----------|-----|---------|-------------|
| A（老仓） | 3% | - | 保留 | 4.0% |
| B（新仓） | 2% | 65 | 保留 | 2.67% |
| C（新仓） | 2.5% | 75 | 过滤 | 0% |
| D（老仓） | 2.5% | - | 保留 | 3.33% |
| 现金 | 50% | - | - | 50% |

归一化前：A+B+D = 3% + 2% + 2.5% = 7.5%（C被过滤后权重归零）
归一化目标：缩放后总权重 = 10%（即原始缩放后的股票权重之和）
缩放因子 = 10% / 7.5% = 1.333
A: 3% × 1.333 = 4.0%，B: 2% × 1.333 = 2.67%，D: 2.5% × 1.333 = 3.33%

## 6. 两层择时的叠加顺序

```
1. 主策略生成 TradePlan（原始目标持仓）
2. 市场级择时：仓位缩放（所有权重 × reduce_to）
3. 个股级择时：过滤新开仓（被过滤的股票权重归零，剩余归一化）
4. 输出调整后的 TradePlan
```

**叠加效果**：
- 市场级先缩放整体暴露（如从100%缩到50%）
- 个股级再在缩放后的权重基础上过滤新开仓
- 被个股级过滤掉的股票，其权重重新分配给剩余股票（在缩放后的总权重范围内）

## 7. EMA/RSI 数据的获取

### 7.1 合成基准EMA

MarketData 当前不提供合成基准数据。需要在 MarketTimingFilter 中自行计算：

1. 从 MarketData 获取所有股票的收盘价序列
2. 每个交易日计算等权平均，得到合成基准序列
3. 对合成基准序列计算60日EMA

**实现方式**：在 `MarketTimingFilter.__init__` 中预计算合成基准和EMA，避免每次调用都重新计算。

### 7.2 个股EMA/RSI

同样从 MarketData 获取个股收盘价序列，在 MarketTimingFilter 中计算：

1. 获取每只股票的收盘价序列
2. 计算个股EMA或RSI
3. 在信号日查询对应值

**实现方式**：在 `MarketTimingFilter.__init__` 中预计算所有个股的EMA/RSI指标，存储为 `dict[str, pd.Series]`。

## 8. BacktestEngine 修改

### 8.1 修改点

在 `_on_signal_day` 方法中，主策略生成 TradePlan 后，调用择时过滤：

```python
def _on_signal_day(self, date: pd.Timestamp) -> None:
    # ... 原有逻辑：计算分数、检查覆盖率、生成 TradePlan ...

    plan = self._strategy.compute_trade_plan(...)

    # 新增：择时过滤
    if self._market_timing is not None:
        plan = self._market_timing.apply(plan, self._market, self._portfolio.positions, date)

    self._stored_trade_plan = plan
```

### 8.2 构造函数修改

BacktestEngine 新增可选参数 `market_timing`：

```python
class BacktestEngine:
    def __init__(
        self,
        market_data: MarketData,
        strategy: BaseStrategy,
        market_timing: MarketTimingFilter | None = None,  # 新增
        ...
    ) -> None:
        self._market_timing = market_timing
```

### 8.3 step08_backtrader.py 修改

在 `_build_strategy` 函数旁边新增 `_build_market_timing` 函数，从配置中构建 MarketTimingFilter：

```python
def _build_market_timing(rule: dict[str, Any], market_data: MarketData) -> MarketTimingFilter | None:
    mt_cfg = dict(rule.get("MarketTiming", {}))
    enabled = bool(mt_cfg.get("enabled", False))
    if not enabled:
        return None
    return MarketTimingFilter(
        market_indicator=str(mt_cfg.get("market_indicator", "EMA_60")),
        reduce_to=float(mt_cfg.get("reduce_to", 0.5)),
        stock_open_filter=str(mt_cfg.get("stock_open_filter", "none")),
        stock_ema_period=int(mt_cfg.get("stock_ema_period", 60)),
        rsi_period=int(mt_cfg.get("rsi_period", 14)),
        rsi_buy_max=float(mt_cfg.get("rsi_buy_max", 70.0)),
        ohlcv_map=market_data._ohlcv,  # 传入行情数据用于预计算
    )
```

## 9. 场景列举

### 场景 1：正常状态 + 无个股过滤

**条件**：`benchmark_close >= EMA_60`，`stock_open_filter = none`

**操作**：择时模块不修改 TradePlan，按主策略原始计划执行。

### 场景 2：风险状态 + 无个股过滤

**条件**：`benchmark_close < EMA_60`，`stock_open_filter = none`，`reduce_to = 0.5`

**操作**：所有目标权重 × 0.5，现金比例增加至约 50%。

**SoftTopK 示例**：
- 原始：30只股票，权重之和 = 100%
- 缩放后：30只股票，权重之和 = 50%，现金 50%

**TopKDropout 示例**：
- 原始：卖出5只，买入5只，继续持有15只
- 缩放后：卖出5只（权重0），买入5只（权重×0.5），继续持有15只中当前权重>缩放后目标权重的需要部分卖出

### 场景 3：正常状态 + RSI过滤

**条件**：`benchmark_close >= EMA_60`，`stock_open_filter = rsi`，`rsi_buy_max = 70`

**操作**：市场级不缩放，但新开仓的股票需要 RSI <= 70。

**示例**：
- 主策略计划买入5只新股票：A(RSI=65), B(RSI=72), C(RSI=55), D(RSI=80), E(RSI=68)
- B和D被过滤，实际买入 A、C、E
- A、C、E 的权重归一化（等比例放大，使总权重之和 = 原始5只的总权重之和）

### 场景 4：风险状态 + RSI过滤

**条件**：`benchmark_close < EMA_60`，`reduce_to = 0.5`，`stock_open_filter = rsi`，`rsi_buy_max = 70`

**操作**：先缩放所有权重×0.5，再过滤新开仓。

**示例**：
- 主策略原始：卖出5只，买入5只（每只5%），继续持有15只
- 缩放后：卖出5只（权重0），买入5只（每只2.5%），继续持有15只（目标权重×0.5）
- RSI过滤：买入的5只中2只RSI>70被过滤
- 归一化：剩余3只买入股票权重从2.5%放大到 2.5% × 5/3 ≈ 4.17%
- 最终：现金 ≈ 50% + 被过滤的5% = 55%

### 场景 5：老持仓加仓不受个股过滤影响

**条件**：`stock_open_filter = rsi`，股票A已在持仓中，主策略计划加仓

**操作**：SoftTopK 中，如果A的当前权重 < 目标权重，需要加仓。即使A的RSI > 70，加仓仍然执行，因为个股过滤只影响"新开仓"。

**判断"新开仓"的标准**：当前持仓中 shares > 0 的股票不算新开仓。

### 场景 6：EMA数据不足

**条件**：回测起始阶段，合成基准的交易日数 < 60

**操作**：无法计算60日EMA，默认为正常状态（不缩放仓位）。这是保守选择——在数据不足时不做择时干预。

### 场景 7：个股RSI/EMA数据不足

**条件**：某只股票上市时间较短，无法计算14日RSI或60日EMA

**操作**：数据不足时默认允许开仓（不过滤）。理由同上——数据不足时不做择时干预。

## 10. 日志输出

择时过滤在信号日输出关键决策信息：

```
2026-05-11 15:01:00 - INFO  - [MarketTiming] benchmark_close=3256.78, EMA_60=3312.45 → RISK STATE, reduce_to=0.50
2026-05-11 15:01:00 - INFO  - [MarketTiming] stock filter (rsi): SZ300661 RSI=65.2 → ALLOW, SZ300433 RSI=72.1 → BLOCK, SZ300866 RSI=58.3 → ALLOW
2026-05-11 15:01:00 - INFO  - [MarketTiming] blocked new buys: [SZ300433], allowed: [SZ300661, SZ300866]
2026-05-11 15:01:00 - INFO  - [MarketTiming] weight renormalize factor: 1.667
```

## 11. 对 SoftTopK 的特殊处理

SoftTopK 允许"部分卖出"（权重再平衡），择时缩放后：

- 继续持有的股票：如果当前权重 > 缩放后目标权重，需要部分卖出
- 这与 SoftTopK 原有的权重再平衡逻辑一致，不需要特殊处理

## 12. 对 TopKDropout 的特殊处理

TopKDropout 原则上"继续持有的股票保持当前权重不变"，但择时缩放需要打破这个原则：

- **择时优先**：当市场级择时触发时，继续持有的股票如果当前权重 > 缩放后目标权重，也需要部分卖出
- **实现方式**：择时过滤修改 TradePlan 后，将需要部分卖出的股票加入 `to_sell`（目标权重 = 缩放后权重，而非0）
- **与原有逻辑的兼容**：TopKDropout 的 `to_sell` 原来只包含权重=0（完全卖出），择时缩放后可能包含权重>0（部分卖出）

**TradePlan 修改**：

```python
# TopKDropout 择时缩放后，继续持有的股票可能需要部分卖出
for code in kept_codes:
    current_weight = (pos.shares * close) / portfolio_value
    target_weight_after_scaling = original_target_weight * reduce_to
    if current_weight > target_weight_after_scaling + 1e-6:
        plan.to_sell[code] = target_weight_after_scaling  # 部分卖出
```

**执行层兼容**：当前执行层的卖出逻辑是"卖出全部持仓"（`shares_to_sell = pos.shares`），需要修改为支持部分卖出：

```python
# 当前逻辑（完全卖出）：
sell_orders.append((code, current_shares))

# 修改后（支持部分卖出）：
if target_weight == 0.0:
    shares_to_sell = current_shares  # 完全卖出
else:
    target_shares = int(target_weight * total_value / open_price)
    target_shares = _round_to_lot(code, target_shares)
    shares_to_sell = max(0, current_shares - target_shares)
    shares_to_sell = _round_to_lot(code, shares_to_sell)
if shares_to_sell > 0:
    sell_orders.append((code, shares_to_sell))
```

## 13. 实现步骤

1. **新增 `engine/market_timing.py`**：MarketTimingFilter 类
   - 预计算合成基准EMA、个股EMA/RSI
   - `apply()` 方法：市场级缩放 + 个股级过滤 + 权重归一化

2. **修改 `engine/__init__.py`**：
   - BacktestEngine 构造函数新增 `market_timing` 参数
   - `_on_signal_day` 中调用择时过滤
   - `_on_trade_day` 中支持部分卖出

3. **修改 `step08_backtrader.py`**：
   - 新增 `_build_market_timing()` 函数
   - `run_backtest_batch_export()` 中构建 MarketTimingFilter 并传入 BacktestEngine

4. **不修改** soft_topk.py 和 topk_dropout.py（叠加层设计，策略代码不变）

## 14. 聚宽脚本适配

step14_joinquant_export.py 需要同步生成择时过滤逻辑的聚宽脚本代码：
- 在聚宽脚本中生成合成基准EMA计算代码
- 在 `rebalance` 函数中，主策略选股后调用择时过滤逻辑
- 个股EMA/RSI使用聚宽内置函数计算
