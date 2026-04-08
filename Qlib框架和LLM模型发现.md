## 第 0 步：先把目标缩小，不要一上来就做太大

最容易失败的原因，就是目标太大。

所以第一版建议你只做下面这件事：

### 第一版目标

> 用 Qlib 的历史日线数据，  
> 让大模型自动生成新的“选股因子”，  
> 然后用 Qlib 回测这些因子是否有效。

为什么先从这里开始？

- 比直接预测涨跌更简单
- 比直接做交易系统更安全
- 更容易看出大模型到底有没有帮助
- 工程工作量最小

### 第一版不要做的事

先不要碰这些：

- 不要直接做实盘交易
- 不要一上来就做分钟级高频
- 不要同时搜索几百种模型
- 不要让大模型直接决定买卖

先把“小闭环”跑通，后面再扩展。

---

## 补充知识：为什么选股因子经常被称为“Alpha (阿尔法) 因子”？

在量化投资（和传统金融学）中，你经常会听到“找 Alpha”、“挖 Alpha 因子”。这源于资本资产定价模型（CAPM）对股票收益的拆解公式：

$$ 股票收益 = \alpha + \beta \times 市场收益 + \epsilon $$

这里面有两个核心希腊字母：

### 1. Beta (贝塔)：随波逐流的收益
- **含义**：代表股票跟随大盘（如沪深300）一起涨跌的部分。
- **通俗理解**：如果今年是牛市，连“猪”都能飞上天，你买了一只股票赚了20%，这20%里可能有15%是因为大盘涨了15%带起来的。这部分收益就是 Beta。
- **特点**：获得 Beta 很容易，买个指数基金躺着就行，不需要什么技术含量。

### 2. Alpha (阿尔法)：逆天改命的收益
- **含义**：代表股票**剥离了市场大盘影响后，独立走出来的超额收益**。
- **通俗理解**：还是上面那个例子，大盘涨了15%，你的股票因为你独特的选股眼光涨了25%，那么多出来的这 **10%**（25% - 15%）就是你的 Alpha。甚至大盘跌了10%，你的股票还涨了5%，那你的 Alpha 就是 +15%！
- **特点**：获得 Alpha 极难。它代表了基金经理（或量化模型）真正的**选股能力**。

### 结论
所以，当我们让系统或者大模型去“挖掘选股因子”时，我们的根本目的不是找那些“大盘涨它才涨”的特征，而是要找那些**“无论大盘怎么走，只要符合这个特征的股票，大概率能跑赢大盘”**的独特规律。

这就是为什么量化界把用来选出这类优质股票的特征，统称为 **“Alpha 因子”**。寻找好因子的过程，就被称为“挖掘 Alpha”。

---

## 第 1 步实操：准备好你要喂给 Qlib 的历史数据 (对应 step01_init_qlib.py)

这一阶段的任务只有一个：

> 让 Qlib 能稳定读到你的历史股票数据。

你需要准备的数据至少包括：

- 开盘价
- 收盘价
- 最高价
- 最低价
- 成交量
- 成交额
- 复权信息

如果后面想做更强的因子，还可以加：

- 市盈率
- 市净率
- ROE
- 营收增长率
- 净利润增长率

### 这一阶段你要完成的检查

你要确保：

- 数据没有明显缺失
- 股票代码格式统一
- 日期格式统一
- 没有把未来数据混进今天

### 最终结果

到这一步结束时，你应该已经做到：

- Qlib 能正常初始化
- Qlib 能读取某段历史数据
- Qlib 能取出一只或多只股票的基础字段

如果这一步没完成，后面所有“大模型发现”都没法开始。

---

## 第 2 步：先定一批基础特征，不要让大模型凭空发挥

你前面说得很对：

> 大模型不能凭空提出想法，  
> 它应该先基于 Qlib 的数据做分析，再提新方案。

所以你要先准备一批 **基础特征**，让系统有“原材料”可分析。

### 什么叫基础特征？

就是最基础、最常见、最容易理解的数据指标。

比如：

- 过去 5 天收益率
- 过去 20 天收益率
- 过去 5 天平均成交量
- 过去 20 天波动率
- 过去 20 天最大回撤
- 市盈率
- 市净率
- ROE
- 营收增长率

### 为什么一定要先有这一步？

因为大模型最擅长的是：

- 组合已有信息
- 改写已有规则
- 从历史结果里总结模式

它不擅长的是：

- 在完全没有边界的情况下乱生成有用公式

所以你最好先给它一个“可选材料清单”。

---

## 第 3 步：先别急着让大模型出主意，先让 Qlib 做基础体检 (对应 step03_health_check.py)

这一步非常重要。

你要先让 Qlib 对这些基础特征做一轮“体检”，看看每个特征到底怎么样。

### 要做哪些体检？

至少做这几项：

- 看每个特征有没有太多缺失值
- 看每个特征数值是否异常
- 看每个特征和未来收益有没有一点关系
- 看不同特征之间是不是高度重复

### 通俗理解

这一步就像招人之前先看简历：

- 有些简历信息不全，先淘汰
- 有些信息和岗位没关系，价值低
- 有些信息其实说的是同一件事，没必要都留

### 最终你要得到什么？

你最好整理出一张“特征体检表”，至少包括：

- 特征名称
- 缺失率
- 波动大小
- 和未来收益的相关性
- 是否和别的特征高度重复

这张表，就是后面给大模型看的第一份核心材料。

---

## 第 4 步：把 Qlib 的体检结果整理成“给大模型看的摘要” (对应 step04_build_summary.py)

注意：

> 不建议把海量原始表格直接全部扔给大模型。

更好的做法是：

> 先由程序把 Qlib 的体检结果整理成摘要，  
> 再把摘要交给大模型。

### 这个摘要里最好有什么？

如果你现在是按 **第一版系统** 来做，这里的摘要要尽量简单，目标只有一个：

> 让大模型快速看懂：  
> 哪些基础特征值得继续研究，  
> 哪些基础特征应该先排除。

所以第一版摘要建议只保留 **程序已经明确算出来的体检结果**，不要混入太多推测。

### 第一版摘要建议包含这 6 类信息

#### 1. 表现最好的基础特征

比如：

- 前 10 个或前 20 个表现最好的特征
- 每个特征对应的简单得分

作用是告诉大模型：

- 哪些特征目前最值得优先参考

#### 2. 表现最差的基础特征

比如：

- 前 10 个或前 20 个表现最差的特征

作用是告诉大模型：

- 哪些特征暂时可以少考虑

#### 3. 高度重复的特征

比如：

- 哪两个特征高度相关
- 哪些特征其实表达的是同一种信息

作用是告诉大模型：

- 不要反复用几乎一样的东西

#### 4. 不稳定的特征

比如：

- 某个特征在 2019 年有效
- 但在 2020 年、2021 年效果很差

作用是告诉大模型：

- 这个特征可能不够稳，不能太依赖

#### 5. 数据质量差的特征

比如：

- 缺失值太多
- 异常值太多
- 某些时间段根本没有数据

作用是告诉大模型：

- 这些特征可能连“研究原料”都算不上

#### 6. 上一轮验证结果的双向反馈（非常重要）

比如：

- `previous_round_top_factors`：上一轮大模型生成且在回测中表现最好的因子（`top3_factors.json`）。
- `previous_round_skipped_factors`：上一轮大模型生成，但因为 IC 太低、ICIR 太低或胜率不足，直接被淘汰的“垃圾因子”名单及具体原因（`skipped_factors.json`）。

作用是告诉大模型：

- **正向激励**：沿着成功的逻辑继续挖掘，或者寻找与之互补的正交逻辑。
- **负向避坑**：反思被淘汰因子的公式（例如过度拟合、用了无意义的高波动特征），绝对不要再生成类似或雷同的因子。

### 第一版先不要放什么？

先不要把这些内容混进摘要：

- 哪些特征组合后可能更强
- 哪个模型最适合这些特征
- 哪种策略最适合这些特征

因为这些已经不是“体检摘要”了，而是下一步才要讨论的问题。

### 你可以把第一版摘要理解成什么？

就是一份很简单的“特征体检报告”。

它不负责告诉大模型最终答案，只负责告诉它：

- 哪些材料比较好
- 哪些材料有问题
- 哪些材料太重复
- 哪些材料不稳定

这样大模型在第 5 步提出方案时，才会更靠谱。

### 摘要长什么样？

它不一定非得是自然语言，也可以是结构化内容。

比如：

```json
{
  "top_features": ["momentum_20d", "volume_ratio_5d", "roe"],
  "weak_features": ["pe_raw"],
  "high_corr_pairs": [["momentum_5d", "momentum_10d"]],
  "unstable_features": ["pb"],
  "poor_quality_features": ["roe_ttm_old"],
  "target": "未来5日收益率",
  "previous_round_top_factors": [...],
  "previous_round_skipped_factors": [...]
}
```

这一步的核心目的很简单：

- 先把第 3 步做出的体检结果压缩成一份短摘要
- 再让大模型基于这份摘要提出下一步候选因子

也就是说，第一版系统里：

- 第 3 步负责“体检”
- 第 4 步负责“整理体检报告”
- 第 5 步才开始让大模型正式出方案

---

## 第 5 步：给大模型一个明确任务，而不是一句“帮我找好因子”

很多人会失败，是因为给大模型的要求太模糊。

错误示例：

> 帮我找一些好的量化因子

这个太空了。

正确做法是给它一个很具体的任务，比如：

> 这里有一批基础特征的表现结果。  
> 请你基于这些结果，生成 10 个新的候选因子（对上涨有效的特征）。  
> 每个因子只能使用我给定的字段和算子。  
> 每个因子都要写明公式、设计理由、可能风险。

### 你要限制它什么？

最好限制：

- 只能用哪些输入字段
- 只能用哪些算子
- 因子表达式不能太复杂
- 不能使用未来数据
- 输出必须是固定格式

### 为什么一定要限制？

因为限制越清楚：

- 大模型越不容易胡说
- 结果越方便程序自动执行
- 越不容易出现“看起来很聪明，实际根本跑不了”的方案

---

## 第 6 步：让大模型输出“结构化方案”，不要只输出一段话 (对应 step06_validate_factor.py)

这一步很关键。

如果大模型只是输出一大段文字，你后面很难自动化。

所以你要让它按固定格式输出。  
但这里要特别注意：

> **只有“因子定义”，还不够直接算出收益率和换手率。**

因为：

- 因子告诉你“哪些股票更值得关注”
- 但它没有告诉你“什么时候买、买多少、什么时候卖”

所以第一版最稳的做法是让大模型输出两层信息：

- **第一层：因子定义**
- **第二层：回测时要套用的固定交易规则**

### 第一层：因子定义里至少要有什么？

- 因子名称
- 因子公式
- 使用的字段
- 因子方向
- 设计理由
- 风险提醒

这里的“因子方向”意思是：

- 因子值越大越看多
- 还是因子值越小越看多

### 第二层：第一版固定交易规则里至少要有什么？

为了让系统先跑起来，建议第一版不要让大模型自己发明买卖规则，而是先给所有因子统一套用同一套规则，比如：

- 调仓频率：每周一次
- 买入规则：买入因子排名前 20 的股票
- 卖出规则：卖出跌出前 40 名的股票
- 持仓数量：20 只
- 单只股票权重：等权
- 手续费：按固定费率计算

### 把固定交易规则再具体一点

如果你真的要让程序照着跑，建议第一版直接把规则写死成下面这样：

#### 1. 股票池

- 只在你指定的股票池里选股
- 第一版建议先用一个固定股票池
- 不要一开始就在全市场乱跑

#### 2. 调仓时间

- 每周第一个交易日调仓一次
- 调仓当天先根据最新因子值做排名
- 再决定哪些股票买入、哪些股票卖出

#### 3. 买入规则

- 把股票池里的股票按因子值从高到低排序
- 如果因子方向是“值越大越好”，就买排名靠前的
- 如果因子方向是“值越小越好”，就买排名靠后的
- 第一版固定买入前 20 名

#### 4. 卖出规则

- 当前持仓股票如果还在前 20 名，就继续持有
- 如果已经跌出前 40 名，就卖出
- 用“前 20 买入，跌出前 40 卖出”的做法，是为了减少来回频繁交易

#### 5. 持仓数量和权重

- 总共持有 20 只股票
- 每只股票资金占比相同
- 如果有 20 只股票，就每只先按 5% 权重分配

#### 6. 成交价格

- 第一版建议统一约定：
- 在调仓信号产生后的下一个交易日开盘价成交

这样做的目的，是避免出现“今天收盘才知道信号，却假装今天收盘已经买到”的未来函数问题。

#### 7. 手续费和滑点

- 买入手续费：固定一个费率
- 卖出手续费：固定一个费率
- 滑点：固定一个很小的交易冲击成本

第一版哪怕先用简单常数，也比完全不算成本强很多。

#### 8. 无法交易时怎么处理

- 如果股票停牌，就先跳过不买
- 如果股票当天涨停买不进去，也先跳过
- 如果股票当天跌停卖不出去，就继续保留到下次处理

#### 9. 现金怎么处理

- 没买出去的资金先留成现金
- 下次调仓时再重新分配

### 你可以把它理解成一份“统一考试规则”

第一版的重点不是找到最强交易策略，而是：

- 在同样规则下比较不同因子
- 看哪个因子更稳定
- 看哪个因子在真实交易约束下更有价值

这样做的好处是：

- 不同因子可以公平比较
- 你能先判断“因子本身有没有用”
- 不会把“因子好坏”和“买卖规则好坏”混在一起

### 一个简单例子

```json
{
  "factor_name": "momentum_volume_quality_v1",
  "formula": "(ret_20d * volume_ratio_5d) / (1 + volatility_20d)",
  "fields": ["ret_20d", "volume_ratio_5d", "volatility_20d"],
  "direction": "higher_better",
  "reason": "希望同时利用趋势、成交量确认和风险过滤",
  "risk": "可能在震荡市场中失效",
  "backtest_rule": {
    "stock_universe": "固定股票池",
    "rebalance": "weekly",
    "rebalance_day": "每周第一个交易日",
    "buy_rule": "买入因子排名前20",
    "sell_rule": "卖出跌出前40",
    "holding_count": 20,
    "weight_mode": "equal_weight",
    "trade_price": "next_open",
    "buy_cost": 0.0015,
    "sell_cost": 0.0025,
    "slippage": 0.0005,
    "suspend_action": "skip",
    "limit_up_action": "skip_buy",
    "limit_down_action": "delay_sell"
  }
}
```

只要它输出成这种格式，你后面就能自动读取、自动测试、自动记录。

---

## 第 7、8 步：写一个“执行器”，把大模型的方案交给 Qlib 跑 (对应 step07_eval_factor.py 和 step08_backtest.py)

现在就进入最关键的工程环节了。

你需要写一个中间程序，专门做这件事：

> 读取大模型给出的候选因子  
> 转成 Qlib 可识别的表达式或特征配置  
> 先做因子评估，**然后进行行业标准的因子筛选**，最后对合格的因子按固定交易规则发起策略回测

### 这个执行器至少做 6 件事

1. 读取大模型输出  
2. 检查格式是否合法  
3. 让 Qlib 计算因子值  
4. **因子评估**：计算 Rank IC、ICIR、胜率等指标
5. **因子筛选（新增机制）**：基于行业标准（如 Rank IC > 0.01, ICIR > 0.1, 胜率 > 40%）过滤掉表现差或不稳定的因子，并自动识别负向因子进行翻转。
6. **策略回测**：只为合格的因子执行完整的交易回测，计算收益率和回撤，并保存结果。

### 为什么要分段并引入筛选？

因为不同阶段评估的东西不一样：

- **因子评估** 主要看 Rank IC、ICIR（信息比率）、胜率。这一步告诉你“这个因子和未来收益有没有关系、稳不稳定”。
- **因子筛选** 可以帮我们拦住大量“垃圾因子”，避免它们进入回测浪费算力，同时也避免实盘中踩坑。
- **策略回测** 才能看扣除手续费后的真实收益率、最大回撤、Sharpe 和换手率。

也就是说：

- 只有因子公式时，你可以知道“这个因子有没有预测能力”
- **经过严格筛选后，你留下的才是值得交易的“好因子”**
- 再加上明确的买卖规则后，你才能知道“按这个好因子去交易，最终能赚多少钱”

### 执行后要保存哪些结果？

建议分成两类保存：

#### 第一类：因子评估结果

- 因子名
- 因子公式
- 回测区间
- IC
- RankIC

#### 第二类：策略回测结果

- 年化收益
- 最大回撤
- Sharpe
- 换手率
- 调仓频率
- 持仓数量

### 这里最关键的一句话

> **没有买卖规则，只能做因子评估；**
> **有了买卖规则，才能做策略回测。**

这一步做完，你的系统就从“会想”变成“会验证”了。

---

## 第 9 步：不要只看收益，要建立一套统一评分规则 (对应 step09_score.py)

这是很多新手最容易忽略的地方。

不能因为某个方案回测收益高，就立刻认为它最好。

因为它可能：

- 回撤特别大
- 交易特别频繁
- 一加手续费就不赚钱
- 只在某两年有效

### 所以你要做一个综合评分

比如总分可以同时考虑：

- 收益
- 稳定性
- 最大回撤
- 换手率
- 不同年份是否都有效

### 通俗理解

这就像选学生代表，不是只看一次考试分数。

还要看：

- 平时稳不稳
- 会不会老犯错
- 能不能长期保持

量化里也是一样。

---

## 第 10 步：把实验结果再喂回大模型，形成“自动迭代” (对应 step10_iterate.py)

到这一步，系统才真正开始像“研究员”。

流程变成这样：

1. Qlib 先分析基础数据  
2. 大模型提出候选因子  
3. Qlib 回测  
4. 程序整理结果摘要  
5. 再把摘要发给大模型  
6. 大模型提出下一轮更好的方案

### 第二轮时，大模型可以做什么？

比如它会说：

- 某个因子在牛市有效，但熊市不稳
- 某个因子收益不错，但换手太高
- 某两个因子高度重复，可以删掉一个
- 可以把趋势因子和估值因子组合试试

这时它就不是瞎猜，而是在“看过实验记录以后继续改进”。

---

## 实操版：把第 0-10 步真正搭成一个能跑的第一版系统

上面的第 0-10 步，是“思路闭环”。  
下面这部分，是“程序员施工手册”。

目标很明确：

> 在 Windows 10 + Miniconda 环境下，  
> 搭出一个第一版系统：  
> 能下载 Qlib 数据、做基础特征体检、调用大模型生成候选因子、  
> 再按固定交易规则完成因子评估和策略回测。

### 先说第一版系统的边界

这版系统只做下面这些事：

- 市场：先用 Qlib 提供的中国市场日频数据
- 频率：只做日线
- 目标：先找“候选因子”，不做高频、不做实盘
- 策略：所有因子统一套用同一套固定交易规则
- LLM：只负责“提案”，不直接下单

### 这个第一版系统和 Qlib 自带 `qrun` 工作流是什么关系

这点要单独说清楚：

> **有关系，但不是同一个层级。**

更准确地说：

- `qrun + yaml` 是 Qlib 自带的标准研究执行方式
- 我前面给你设计的是一个“LLM 外挂总控层”
- 这个总控层负责把“大模型提案 -> Qlib 验证 -> 结果回灌 -> 下一轮再提案”串起来

所以：

- **Qlib 原生工作流** 更像“先把实验配置写好，然后按配置执行”
- **我给你设计的工作流** 更像“外面再包一层自动研究员，驱动 Qlib 一轮一轮跑”

也就是说，当前这套设计：

- 不是纯粹的 `qrun` 读取一个静态 yaml 然后一次跑完
- 但也不是脱离 Qlib，自己重写了一套量化平台

它本质上是：

- **外层**：自定义 LLM 迭代控制器
- **内层**：Qlib 的数据、实验管理、因子评估、回测能力

### 为什么第一版没有直接以 `qrun` 为主线

原因很简单：

- `qrun` 更适合“配置相对固定”的实验
- 而你这个题目里最关键的一点，是“大模型每一轮都会生成新候选因子”
- 这意味着配置不是固定不变的，而是会随着上一轮结果不断变化

所以第一版如果强行只靠一个固定 yaml 去跑，会很别扭。

### 第一版和 `qrun` 的真实分工

第一版里，Qlib 主要提供这些能力：

- 数据读取
- 表达式和特征计算
- 实验记录
- 因子评估
- 策略回测

而 LLM 相关这几步：

- 生成候选因子
- 校验候选因子
- 汇总上一轮结果
- 组织下一轮输入

这些并不是 `qrun` 原生最擅长的部分，所以我把它放在 Qlib 外层来做了。

### 如果你想更贴近 Qlib 原生风格，可以怎么改

其实可以走两条路线：

- 路线 A：像我现在这样，外层 Python 总控脚本直接调 Qlib API
- 路线 B：外层脚本让大模型每轮生成新的 yaml，然后每轮调用一次 `qrun`

其中：

- 路线 A 更灵活，适合现在这个第一版原型
- 路线 B 更接近 Qlib 原生工作流，但会增加“动态生成 yaml”的复杂度

所以我当前给你的方案，不是“和 Qlib 没关系”，  
而是“**把 Qlib 当内核，把 LLM 迭代逻辑放在外层总控里**”。

### 开始之前：先准备这些软件

第一版建议你准备下面这些软件或能力：

- Miniconda
- Git for Windows
- Python 3.8 环境
- 一个可调用的大模型 API

其中：

- Miniconda 你已经有了，路径是 `D:\miniconda3`
- Git for Windows 如果还没装，先去官网安装，并勾选加入 PATH
- 大模型 API 可以是任何 OpenAI 兼容接口，只要支持文本输入输出即可

### 开始之前：先准备项目目录

建议你把第一版运行目录定成下面这样：

```text
D:\test\test\Qlib\
  Qlib框架和LLM模型发现.md
  runtime\
    data\
      qlib_cn\
    outputs\
      health\
      llm\
      backtest\
      mlruns\
    config\
      env.yaml
      feature_pool.yaml
      backtest_rule.yaml
    src\
      step01_init_qlib.py
      step02_build_feature_pool.py
      step03_health_check.py
      step04_build_summary.py
      step05_call_llm.py
      step06_validate_factor.py
      step07_eval_factor.py
      step08_backtest.py
      step09_score.py
      step10_iterate.py
```

你现在先不用一次性把所有代码都写完。  
但目录最好一开始就定好，不然后面文件会很乱。

### 开始之前：先创建 Python 环境

第一次在 PowerShell 里用 conda 的话，先初始化一次：

```powershell
& D:\miniconda3\Scripts\conda.exe init powershell
```

关掉终端，重新打开后，再执行：

```powershell
conda create -n qlib_v1 python=3.8 -y
conda activate qlib_v1
python -V
```

这里建议先用 Python 3.8，是为了尽量贴近 Qlib 官方文档在 Windows 下更稳的使用方式。

### 开始之前：安装第一版需要的 Python 包

激活 `qlib_v1` 环境后，先安装这些包：

```powershell
pip install --upgrade pip
pip install pyqlib lightgbm torch pandas numpy scipy scikit-learn pyarrow openai pydantic python-dotenv mlflow jupyterlab matplotlib
```

这批包的作用大概是：

- `pyqlib`：Qlib 主体
- `lightgbm`、`torch`：Qlib 常用依赖
- `pandas`、`numpy`：做特征处理
- `openai`：调用大模型接口
- `pydantic`：校验大模型输出格式
- `mlflow`：保存实验记录

### 开始之前：单独准备 Qlib 源码仓库

虽然你已经可以通过 `pip install pyqlib` 使用 Qlib，  
但为了拿到官方的 `scripts/get_data.py` 下载脚本，建议再单独 clone 一份源码。

注意：

> 不要把你的业务代码写在 Qlib 源码目录里。  
> Qlib 文档明确提醒，不要在 Qlib 仓库目录下直接 import qlib。

建议这样做：

```powershell
# 假设你已经把源码下载或解压到了 D:\test\qlib-main
```

以后：

- `D:\test\qlib-main` 只用来拿脚本和参考示例
- `D:\test\test\Qlib\runtime` 才是你自己的第一版系统目录

### 开始之前：先做 3 个最基础的验证

先别急着写业务代码，先确认环境没问题：

```powershell
python -c "import qlib; print(qlib.__version__)"
python -c "import lightgbm; print('lightgbm ok')"
python -c "import torch; print('torch ok')"
```

如果这 3 条都过了，再继续往下。

### 开始之前：配置大模型 API

第一版建议直接用环境变量，不要先搞复杂的密钥管理系统。

如果你用的是 DeepSeek，由于 Windows 下 `setx` 命令设置后**需要重启终端才能生效**，建议你直接在当前的 PowerShell 里用 `$env:` 的方式设置（这样当前窗口立刻生效）：

```powershell
$env:OPENAI_API_KEY="sk-13b123ca44064df0a0fd7a2f7b0c7b88"
$env:OPENAI_BASE_URL="https://api.deepseek.com"
$env:OPENAI_MODEL="deepseek-reasoner"
```

如果你希望永久保存（下次打开终端自动生效），也可以执行 `setx`：

```powershell
setx OPENAI_API_KEY "sk-13b123ca44064df0a0fd7a2f7b0c7b88"
setx OPENAI_BASE_URL "https://api.deepseek.com"
setx OPENAI_MODEL "deepseek-reasoner"
```
> **注意**：执行完 `setx` 后，当前终端依然读不到新变量，**必须关闭当前终端并重新打开**才能生效！

第一版系统里，我更建议先用 `deepseek-chat`，因为它更适合稳定输出结构化 JSON。

设置完以后，重开一个终端，再验证：

```powershell
python -c "import os; print(os.getenv('OPENAI_MODEL'))"
```

看到模型名能正常打印，就说明环境变量生效了。

---

## 第 0 步实操：把第一版目标和默认参数先写死

这一步不要写代码，先把第一版系统的边界定死，不然后面会一直改。

### 你现在就先确定这几个默认值

- 市场：`cn`
- 数据频率：日线
- 标签：未来 5 个交易日收益率
- 因子数量：每轮让大模型生成 10 个
- 回测策略：统一固定交易规则
- 调仓频率：每周一次
- 持仓数量：20 只

### 建议你创建第一个配置文件

文件路径建议：

`D:\test\test\Qlib\runtime\config\env.yaml`

建议内容：

```yaml
project_root: D:/test/test/Qlib/runtime
provider_uri: D:/test/test/Qlib/runtime/data/qlib_cn
region: cn
market: all
freq: day
start_date: 2018-01-01
end_date: 2024-05-20
max_instruments: 100
sample_instrument: SH600000
target_label: future_5d_return
label_horizon: 5
llm_model: ${OPENAI_MODEL}
llm_base_url: ${OPENAI_BASE_URL}
llm_candidate_count: 10
iteration_count: 3
```

**关键参数说明：**

- `market`: 定义股票池范围。在 Qlib 的中国市场（`region: cn`）数据集中，常见取值有：
  - `all`: 所有 A 股股票（包含数据集中能找到的所有代码）。
  - `csi300`: 沪深 300 成分股。
  - `csi500`: 中证 500 成分股。
- `max_instruments`: 截取数量。第一版为了让原型跑得快，我们设为 `100`（即只取前 100 只股票做体检和回测）。如果你想跑全市场，把它改成 `0` 或删掉即可。
- `llm_candidate_count`: 告诉大模型每次迭代提出多少个候选因子。
- `iteration_count`: 自动化工作流的循环迭代次数。

### 这一步完成的标准

你应该已经明确：

- 第一版只做什么
- 第一版不做什么
- 数据放哪里
- 输出放哪里
- LLM 配置从哪里读

---

## 第 1 步实操：下载 Qlib 数据，并验证能正常读取 (对应 step01_init_qlib.py)

这是第一版最关键的一步。  
没有数据，后面所有步骤都没意义。

### 2.1 创建运行目录

先创建目录：

```powershell
New-Item -ItemType Directory -Force D:\test\test\Qlib\runtime\data\qlib_cn
New-Item -ItemType Directory -Force D:\test\test\Qlib\runtime\outputs\health
New-Item -ItemType Directory -Force D:\test\test\Qlib\runtime\outputs\llm
New-Item -ItemType Directory -Force D:\test\test\Qlib\runtime\outputs\backtest
New-Item -ItemType Directory -Force D:\test\test\Qlib\runtime\outputs\mlruns
New-Item -ItemType Directory -Force D:\test\test\Qlib\runtime\config
New-Item -ItemType Directory -Force D:\test\test\Qlib\runtime\src
```

### 2.2 下载官方示例数据

进入 Qlib 源码目录，执行：

```powershell
conda activate qlib_v1
cd D:\test\qlib-main
python scripts\get_data.py qlib_data --target_dir D:\test\test\Qlib\runtime\data\qlib_cn --region cn
```

> **💡 网络报错备用方案（强烈推荐）：**
> 如果你在运行上面命令时遇到 `SSLError` 或 `ProxyError`（国内访问 GitHub 极易发生），**最快且最稳妥的解决办法是手动下载并解压**：
> 1. 直接用浏览器或下载工具（如迅雷）访问这个国内加速链接下载数据集压缩包（约 1GB）：
>    `https://mirror.ghproxy.com/https://github.com/SunsetWolf/qlib_dataset/releases/download/v2/qlib_data_cn_1d_0.9.zip`
> 2. 将下载好的压缩包解压。
> 3. 将解压出来的所有文件夹（如 `calendars`、`features`、`instruments` 等）直接放进你刚才创建的 `D:\test\test\Qlib\runtime\data\qlib_cn` 目录中。（注意不要套娃，确保 `qlib_cn` 目录下直接就是这些文件夹）。

这批数据来自公开源，适合做第一版原型。  
如果你后面有更高质量的数据，再替换成自己的 CSV 并转成 qlib 格式。

### 2.2.1 关于“Qlib 数据是不是持续更新”的说明

这里要分清两件事：

- `Qlib` 框架本身是工具，不是实时数据服务商
- `scripts/get_data.py` 下载到的是官方准备好的示例数据集

所以答案是：

> **不是“只要用了 Qlib，数据就会自动持续更新到今天”。**

更准确地说：

- 你第一次跑 `get_data.py` 时，拿到的是当时可下载到的一份公开数据快照
- 这份数据可以拿来做学习、原型验证、流程联调
- 但官方文档也明确提醒，这些数据来自公开源，数据质量和完整性不一定完美
- 如果你要做正式研究，最好准备自己的高质量数据

如果你想让数据继续往后更新，不是重新等 Qlib 项目“发新版”，而是要自己运行它的数据采集脚本。

### 2.2.2 第一版你应该怎么理解这批数据

你可以把它理解成：

- `get_data.py qlib_data`：先给你一包“能马上开箱跑通 Qlib 的基础数据”
- `scripts/data_collector/...`：真正负责后续补数据、更新到新交易日的工具

也就是说：

- Qlib 提供了“下载一份示例数据”的能力
- Qlib 也提供了“自己继续更新数据”的脚本
- 但**默认不会替你每天自动更新**

### 2.2.3 如果你想更新到当前日期，应该怎么做

第一版你可以这样处理：

1. 先用 `get_data.py qlib_data` 把整套流程跑通  
2. 等第一版系统稳定后，再接入 `scripts/data_collector` 做日更  
3. 最后如果你要长期使用，再换成自己的正式数据源

### 2.2.4 对第一版系统的实际建议

如果你的目标是：

- 先验证“LLM 能不能提出候选因子”
- 先验证“Qlib 能不能完成评估和回测”

那么示例数据已经够用了。

如果你的目标变成：

- 做更接近真实生产的研究
- 想要最近的市场数据
- 想控制复权方式、停牌处理、字段质量

那就不要长期依赖这份示例数据，而应该尽快换成你自己的数据更新链路。

### 2.2.5 Qlib 数据如何手动更新到最新日期

如果你已经下载过：

```text
D:\test\test\Qlib\runtime\data\qlib_cn
```

那么后面想补到最新交易日，重点不是再跑一次整包下载，  
而是运行 `scripts/data_collector` 里的更新脚本。

第一版建议你这样做。

#### 第一步：先确认你要更新的是哪个数据目录

你的目标目录应该还是：

```text
D:\test\test\Qlib\runtime\data\qlib_cn
```

后面的更新命令里，`--qlib_data_1d_dir` 就指向这个目录。

#### 第二步：先手动补一段日期

进入 Qlib 源码目录后，执行：

```powershell
conda activate qlib_v1
cd D:\dev\qlib-src
python scripts\data_collector\yahoo\collector.py update_data_to_bin --qlib_data_1d_dir D:\test\test\Qlib\runtime\data\qlib_cn --trading_date 2026-01-01 --end_date 2026-04-06
```

这条命令的意思是：

- `update_data_to_bin`：把新抓到的数据直接更新进 qlib 格式目录
- `--qlib_data_1d_dir`：你已有的 qlib 日线数据目录
- `--trading_date`：本次更新的起始日期
- `--end_date`：本次更新的结束日期，不包含当天

你实际使用时，可以把日期改成：

- 上次更新结束后的下一天
- 到你今天想补到的日期

#### 第三步：更新后先做目录级验证

更新完成后，先不要急着跑回测，先验证数据目录还在：

```powershell
Get-ChildItem D:\test\test\Qlib\runtime\data\qlib_cn
```

你至少应该还能看到类似这些目录：

- `calendars`
- `features`
- `instruments`

如果这些目录还在，说明 qlib 数据结构没有被破坏。

#### 第四步：再做 Qlib 读取验证

更新完以后，重新跑一次你的初始化脚本：

```powershell
python D:\test\test\Qlib\runtime\src\step01_init_qlib.py
```

如果 `qlib.init()` 正常，说明更新后的数据还能被系统识别。

#### 第五步：再做“最新日期”验证

第一版建议你额外写一个很小的检查脚本，或者直接在 Python 里读取一只股票最近几行数据，确认最新日期已经进来了。

你验证的目标不是“所有字段都完美”，  
而是先确认两件事：

- 数据确实补到了你指定的日期范围
- Qlib 还能正常读取

#### Windows 下怎么做定期更新

官方文档里给的是 Linux 的 `crontab` 例子。  
你是 Windows 10，所以不要照抄 `crontab`。

你应该这样理解：

- Linux：用 `crontab`
- Windows：用“任务计划程序”

也就是说，第一版你可以先手动执行更新命令；  
等验证稳定后，再把同一条命令挂到 Windows 任务计划程序里，每个交易日收盘后执行一次。

#### 第一版最实用的更新策略

我建议你先按下面这个节奏来：

1. 初次安装时，先跑 `get_data.py qlib_data`
2. 第一版系统开发阶段，每周手动更新一次
3. 等闭环跑稳后，再改成工作日自动更新

#### 什么时候不建议继续依赖这个更新链路

如果你后面发现自己越来越在意这些问题：

- 数据是否完整覆盖全部股票
- 复权规则是否符合你的研究口径
- 停牌、涨跌停、缺失值处理是否符合你的要求
- 字段是否足够丰富，比如财务、行业、资金流

那就说明你已经快要从“原型阶段”走向“正式研究阶段”了。  
这时候更合适的做法，是切换到你自己的正式数据源，而不是长期依赖 Qlib 自带的公开更新脚本。

### 2.3 写一个最小初始化脚本

文件路径建议：

`D:\test\test\Qlib\runtime\src\step01_init_qlib.py`

它至少要做 3 件事：

- 读取 `env.yaml`
- 调用 `qlib.init`
- 取一小段数据打印出来

### 2.4 最小初始化示例

```python
from __future__ import annotations
from pathlib import Path
from qlib.data import D
from common import OUTPUT_DIR, env_config, ensure_runtime_dirs, init_qlib, list_instruments, write_table

def run() -> None:
    ensure_runtime_dirs()
    config = env_config()
    init_qlib(config)
    instruments = list_instruments(config)
    sample_instrument = str(config.get("sample_instrument") or instruments[0])
    preview = D.features(
        instruments=[sample_instrument],
        fields=["$open", "$close", "$high", "$low", "$volume"],
        start_time=str(config["start_date"]),
        end_time=str(config["end_date"]),
        freq=str(config.get("freq", "day")),
    )
    preview.columns = [column.replace("$", "") for column in preview.columns]
    preview.index = preview.index.set_names(["instrument", "datetime"])
    write_table(OUTPUT_DIR / "health" / "sample_preview.csv", preview.head(50))
    print(f"qlib init ok, instruments={len(instruments)}, sample={sample_instrument}")

if __name__ == "__main__":
    run()
```

### 2.5 这一步完成的标准

运行脚本：

```powershell
conda activate qlib_v1
cd D:\test\test\Qlib\runtime
python src\step01_init_qlib.py
```

你要确认：

- 终端能打印出 `qlib init ok, instruments=100, sample=SH600000` 等信息
- `D:\test\test\Qlib\runtime\outputs\health` 目录下成功生成了 `sample_preview.csv` 文件
- `sample_preview.csv` 里面有正常的时间和开高低收价格数据

---

## 第 2 步实操：先把基础特征池写成配置，不要直接交给大模型乱发明 (对应 step02_build_feature_pool.py)

第一版不要追求花哨，先做一个 **人工定义的基础特征池**。

### 3.1 先定义允许使用的字段

文件路径建议：

`D:\test\test\Qlib\runtime\config\feature_pool.yaml`

建议先只放这些原始字段：

```yaml
raw_fields:
  - open
  - close
  - high
  - low
  - volume
  - factor

base_features:
  - name: ret_5d
    expr: close.pct_change(5)
  - name: ret_20d
    expr: close.pct_change(20)
  - name: volume_mean_5d
    expr: volume.rolling(5).mean()
  - name: volume_ratio_5d
    expr: volume / volume.rolling(5).mean()
  - name: volatility_20d
    expr: close.pct_change().rolling(20).std()
  - name: max_drawdown_20d
    expr: close / close.rolling(20).max() - 1
  - name: price_pos_20d
    expr: (close - close.rolling(20).min()) / (close.rolling(20).max() - close.rolling(20).min())
```

### 3.2 第一版允许的大模型算子

再补一个字段，明确只允许这些算子：

```yaml
allowed_operators:
  - add
  - sub
  - mul
  - div
  - rolling_mean
  - rolling_std
  - rank
  - zscore
  - minmax
```

这些算子主要是给 **大模型提案模块** 用的，不是给你手工每天去敲的。

更准确地说，它们同时服务 3 个对象：

- 给大模型用：限制它只能在这些“合法拼装动作”里组合新因子
- 给校验程序用：检查大模型输出的公式有没有越界
- 给执行程序用：把合法公式真正算成因子值

你可以把它理解成：

- 原始字段、基础特征：是“原材料”
- `allowed_operators`：是“允许使用的加工动作”
- 大模型：是“提菜谱的人”
- 校验器和执行器：是“检查菜谱、照着做菜的人”

比如：

- `ret_20d` 和 `volume_ratio_5d` 是基础特征
- `mul` 表示它们可以相乘
- 所以大模型可以提一个候选因子：`mul(ret_20d, volume_ratio_5d)`

但如果大模型擅自输出一个不在白名单里的动作，比如某个你根本没开放的自定义函数，  
那么第 6 步的校验器就应该直接拒绝它。

所以这一段的核心作用，不是“教人算因子”，而是给第一版系统建立一套 **可控的因子生成语法边界**。

### 3.3 这一步完成的标准

运行脚本：

```powershell
conda activate qlib_v1
cd D:\test\test\Qlib\runtime
python src\step02_build_feature_pool.py
```

你要得到一份清晰的“材料清单”并且确认：

- 终端打印出 `feature pool ready` 等信息
- `outputs/health/feature_pool_manifest.json` 成功生成
- JSON 文件中明确了哪些是原始字段、基础特征、允许的算子

---

## 第 3 步实操：生成特征体检表 (对应 step03_health_check.py)

这一步就是把“分析”真正落地成程序。

### 4.1 写健康检查脚本

文件路径建议：

`D:\test\test\Qlib\runtime\src\step03_health_check.py`

这个脚本至少要完成下面 6 件事：

1. 从 Qlib 读取股票池和价格数据  
2. 计算 `feature_pool.yaml` 里定义的基础特征  
3. 构造标签：未来 5 日收益率  
4. 计算每个特征的缺失率  
5. 计算每个特征和标签的相关性  
6. 计算特征两两之间的相关性

### 4.2 第一版标签先固定

第一版建议固定成：

```text
future_5d_return = 第 t+5 天收盘价 / 第 t 天收盘价 - 1
```

不要一开始就让大模型也去设计标签，不然变量太多。

### 4.3 健康检查结果至少输出 3 份文件

建议输出到：

- `outputs/health/feature_stats.csv`
- `outputs/health/feature_corr.csv`
- `outputs/health/health_summary.json`

这 3 份文件分别保存：

- 单个特征统计信息
- 特征之间的相关性
- 给后面大模型看的摘要

### 4.4 第一版 feature_stats.csv 至少要有这些列

- `feature_name`：特征名称
- `missing_ratio`：缺失值比例
- `std`：标准差
- `label_corr`：与目标收益率的相关系数
- `yearly_stability_score`：年度稳定性得分。衡量的是特征预测能力的跨期一致性（即每年与收益率相关系数的波动率倒数）。得分越接近1，说明特征在不同年份表现越稳定；得分越低，说明特征容易受市场风格切换影响，过拟合风险较高。
- `quality_flag`：质量标记。只有同时满足以下两个条件才会被标记为 `ok`，否则标记为 `review`：
  - `missing_ratio <= max_missing_ratio`：缺失率必须小于等于配置文件中设定的阈值（默认 0.2 即 20%）。缺失太多说明数据源不稳定。
  - `std > 0`：标准差必须大于 0。如果标准差为 0 说明特征是个常数（一条直线），对模型毫无信息量。

### 4.5 运行脚本并验证结果

一切就绪后，在终端执行以下命令：

```bash
cd D:\test\test\Qlib\runtime
python src\step03_health_check.py
```

### 4.6 这一步完成的标准

你要能回答这些问题：

- 哪些特征缺失值太多
- 哪些特征和未来收益有一点关系
- 哪些特征彼此高度重复
- 哪些特征在不同年份表现不稳

---

## 第 4 步实操：把体检结果整理成 LLM 能读懂的摘要 (对应 step04_build_summary.py)

这一步不要让大模型直接看 CSV，  
而是先由程序把 CSV 压缩成一份短小、结构化的摘要。

### 5.1 写摘要脚本

文件路径建议：

`D:\test\test\Qlib\runtime\src\step04_build_summary.py`

这个脚本输入：

- `feature_stats.csv`
- `feature_corr.csv`

这个脚本输出：

- `outputs/health/llm_summary.json`

### 5.2 第一版摘要里只保留 5 类信息

- top_features
- weak_features
- high_corr_pairs
- unstable_features
- poor_quality_features

### 5.3 摘要示例

```json
{
  "target": "future_5d_return",
  "top_features": ["ret_20d", "volume_ratio_5d", "price_pos_20d"],
  "weak_features": ["max_drawdown_20d"],
  "high_corr_pairs": [["ret_5d", "ret_20d"]],
  "unstable_features": ["price_pos_20d"],
  "poor_quality_features": []
}
```

### 5.4 运行脚本并验证结果

在终端执行：

```bash
cd D:\test\test\Qlib\runtime
python src\step04_build_summary.py
```

检查 `outputs/health/health_summary.json` 是否生成成功，且内容符合预期。

### 5.5 这一步完成的标准

你要得到一份：

- 足够短
- 结构清楚
- 不混入猜测
- 可以直接传给大模型

---

## 第 5 步实操：把大模型调用做成一个独立模块 (对应 step05_call_llm.py)

第一版不要把大模型调用散落在各个脚本里。  
要单独做一个适配层。

### 6.1 写大模型调用脚本

文件路径建议：

`D:\test\test\Qlib\runtime\src\step05_call_llm.py`

这个脚本至少要做下面几件事：

- 读取系统环境变量里的 API 配置
- 读取 `llm_summary.json`
- 拼装固定提示词
- 调用大模型
- 把原始返回结果保存到 `outputs/llm/raw_response.json`

### 6.2 第一版提示词要明确限制

建议提示词里明确写清楚：

- 目标标签是未来 5 日收益率
- 只能基于给定摘要生成候选因子
- 只能使用给定基础特征和允许算子
- 一次生成 10 个候选因子
- 输出必须是 JSON
- 不允许使用未来数据

### 6.3 第一版建议创建一个“允许输入清单”

你可以把下面这些内容一起传给大模型：

- `top_features`
- `weak_features`
- `high_corr_pairs`
- 允许使用的基础特征列表
- 允许使用的算子列表
- 固定交易规则摘要

### 6.4 运行脚本并验证调用

在终端执行：

```bash
cd D:\test\test\Qlib\runtime
python src\step05_call_llm.py
```

检查 `outputs/llm/raw_response.json` 是否成功记录了大模型的原始返回数据。

### 6.5 这一步完成的标准

你要能做到：

- 大模型能稳定返回内容
- 返回内容是 JSON 风格
- 原始返回值被完整存档

---

## 第 6 步实操：把大模型输出校验成程序可以执行的结构化结果 (对应 step06_validate_factor.py)

大模型返回的 JSON，不要直接拿去跑。  
先做一次严格校验。

### 7.1 写校验脚本

文件路径建议：

`D:\test\test\Qlib\runtime\src\step06_validate_factor.py`

### 7.2 建议你用 Pydantic 定义统一结构

每个候选因子至少要有这些字段：

- `factor_name`
- `formula`
- `fields`
- `direction`
- `reason`
- `risk`
- `backtest_rule`

### 7.3 第一版强制要求

校验时必须检查：

- `formula` 里只能出现允许的特征名
- `formula` 里只能出现允许的算子
- `direction` 只能是 `higher_better` 或 `lower_better`
- `backtest_rule` 必须和第一版默认交易规则一致

### 7.4 校验后的输出文件

建议保存成：

- `outputs/llm/factors_validated.json`
- `outputs/llm/factors_rejected.json`

### 7.4 运行脚本并验证拦截

在终端执行：

```bash
cd D:\test\test\Qlib\runtime
python src\step06_validate_factor.py
```

分别去查看 `factors_validated.json`（成功解析的）和 `factors_rejected.json`（被拒绝的），看分类是否正确。

### 7.5 这一步完成的标准

你要保证：

- 非法公式进不了后续流程
- 结构不完整的输出会被拒绝
- 每次被拒绝的原因都能记录下来

---

## 第 7、8 步实操：把因子评估和策略回测真正跑起来 (对应 step07_eval_factor.py 和 step08_backtest.py)

这一步是第一版系统的核心执行器。

### 8.1 再建一个固定交易规则配置

文件路径建议：

`D:\test\test\Qlib\runtime\config\backtest_rule.yaml`

建议内容：

```yaml
stock_universe: all
rebalance: weekly
rebalance_day: first_trading_day_of_week
buy_top_n: 20
sell_drop_to: 40
holding_count: 20
weight_mode: equal_weight
trade_price: next_open
buy_cost: 0.0015
sell_cost: 0.0025
slippage: 0.0005
suspend_action: skip
limit_up_action: skip_buy
limit_down_action: delay_sell
```

### 8.2 写执行脚本

建议拆成两个脚本：

- `step07_eval_factor.py`
- `step08_backtest.py`

### 8.3 因子评估脚本做什么

`step07_eval_factor.py` 至少做这些事：

- 读取 `factors_validated.json`
- 计算每个因子的日度因子值
- 对每个因子计算 IC、RankIC
- 保存到 `outputs/backtest/factor_metrics.csv`

### 8.4 回测脚本做什么

这里要特别说明：

- `qrun` 常见示例里，经常是“特征 -> LGBModel 之类的模型 -> 预测分数 -> 回测”
- 而这个第一版流程走的是“候选因子 -> 直接作为打分信号 -> 排名选股 -> 回测”
- 也就是说，这里的回测默认不是模型预测式回测，而是因子直接排序式回测

`step08_backtest.py` 至少做这些事：

- 读取因子值
- 把因子值直接当作选股打分信号
- 按固定规则进行股票排名
- 生成每个调仓日的买卖列表
- 扣除手续费和滑点
- 输出策略净值曲线
- 输出年化收益、最大回撤、Sharpe、换手率

### 8.5 回测输出建议保存这些文件

- `outputs/backtest/factor_metrics.csv` （因子评估报告，核心指标说明如下）
  - **`mean_ic` (Information Coefficient 均值)**: 衡量因子的绝对预测能力。绝对值越大越好，多因子模型中稳定在 0.02~0.03 以上即为及格因子。（负数表示因子值越小，未来收益越高）。
  - **`mean_rank_ic` (Rank IC 均值)**: 衡量因子的排序选股能力。在选股策略中比 `IC` 更重要，因为它不受极端异常值的干扰。绝对值越大越好。
  - **`ic_ir` (IC Information Ratio)**: 衡量因子的稳定性 (`mean_ic` 除以 IC 的标准差)。绝对值在 0.5 以上较好。
  - **`rank_ic_ir` (Rank IC 信息比率)**: 衡量排名预测的稳定性。
  - **`positive_ic_ratio` (IC 胜率)**: IC 为正的交易日比例。好因子通常在 55% (0.55) 以上。
  - **`coverage` (覆盖率)**: 因子能计算出有效值的股票比例。1.0 (100%) 表示所有股票每天都能算出值，说明公式稳健。
- `outputs/backtest/strategy_metrics.csv`
- `outputs/backtest/positions.parquet`
- `outputs/backtest/orders.parquet`

### 8.6 运行评估与回测

在终端依次执行：

```bash
cd D:\test\test\Qlib\runtime
python src\step07_eval_factor.py
python src\step08_backtest.py
```

### 8.6 这一步完成的标准

你至少要能看到：

- 每个候选因子的 IC、RankIC
- 每个候选因子的策略收益指标
- 每轮的持仓和换手情况

---

## 第 9 步实操：做一个统一评分器，把回测结果变成“下一轮输入” (对应 step09_score.py)

第一版不要拍脑袋选因子，要有明确打分器。

### 9.1 写评分脚本

文件路径建议：

`D:\test\test\Qlib\runtime\src\step09_score.py`

### 9.2 第一版建议统一评分公式

可以先用一个简单版本：

```text
总分 = 0.20 * IC稳定性
     + 0.45 * 年化收益得分
     - 0.15 * 最大回撤惩罚
     - 0.10 * 换手率惩罚
     - 0.10 * 不稳定性惩罚

> **注意：** 对负收益率因子会施加 0.5 分的直接惩罚，避免负收益因子获得高分。
```

### 9.3 输出结果建议保存成

- `outputs/backtest/final_score.csv`
- `outputs/backtest/top3_factors.json`

### 9.3 运行评分与反馈汇总

在终端执行：

```bash
cd D:\test\test\Qlib\runtime
python src\step09_score.py
```

### 9.4 这一步完成的标准

你能明确告诉系统：

- 哪 3 个因子最值得进入下一轮
- 为什么它们进入下一轮
- 为什么其余因子被淘汰

---

## 第 10 步实操：把结果再次喂给大模型，真正形成自动迭代 (对应 step10_iterate.py)

到这一步，你就要开始写“总控脚本”了。

### 10.1 写总控脚本

文件路径建议：

`D:\test\test\Qlib\runtime\src\step10_iterate.py`

### 10.2 这个脚本要负责的顺序

固定按下面顺序执行：

1. 初始化 Qlib  
2. 读取基础特征池  
3. 运行健康检查  
4. 生成 LLM 摘要  
5. 调用大模型生成候选因子  
6. 校验候选因子  
7. 评估因子  
8. 回测策略  
9. 统一打分  
10. 保存本轮总结  
11. 把总结再次喂给大模型  
12. 开始下一轮

### 10.3 第一版先只跑 3 轮

不要无限循环，建议先写死：

- 第 1 轮：基础特征摘要 -> 10 个因子
- 第 2 轮：基于第 1 轮结果再生成 10 个
- 第 3 轮：基于前两轮最优结果再生成 10 个

### 10.4 每一轮都要存档

建议按轮次建目录：

```text
outputs\
  iter_01\
  iter_02\
  iter_03\
```

每轮至少保存：

- 输入摘要
- LLM 原始输出
- 校验后的因子
- 因子评估结果
- 回测结果
- 最终评分

### 10.5 第一版整体验证顺序

正式跑完整闭环前，建议按下面顺序验收：

1. 先只跑 `step01_init_qlib.py`
2. 再只跑 `step03_health_check.py`
3. 再只跑 `step04_build_summary.py`
4. 再只跑 `step05_call_llm.py`
5. 再只跑 `step06_validate_factor.py`
6. 再跑 `step07_eval_factor.py`
7. 最后跑 `step08_backtest.py`
8. 全部单步通过后，再跑 `step10_iterate.py`

### 10.6 运行全流程迭代

在终端直接执行主控脚本即可（它会自动串起所有步骤）：

```bash
cd D:\test\test\Qlib\runtime
python src\step10_iterate.py
```

### 10.7 跑通第一版的完成标准

当你满足下面这些条件时，就算第一版真正搭起来了：

- 能下载并读取 Qlib 数据
- 能生成基础特征和体检报告
- 能把体检摘要发给大模型
- 能拿到结构化候选因子
- 能自动验证公式是否合法
- 能完成因子评估和策略回测
- 能按统一标准选出本轮最优因子
- 能连续跑完 3 轮迭代

---

## 第 11 步：第一版系统建议长什么样

为了避免你做太大，我建议第一版只包含这 5 个模块：

### 模块 1：数据读取模块

负责：

- 从 Qlib 读取历史数据
- 计算基础特征

### 模块 2：特征分析模块

负责：

- 计算缺失率
- 计算相关性
- 计算特征初步有效性

### 模块 3：大模型提案模块

负责：

- 接收分析摘要
- 生成新的候选因子

### 模块 4：Qlib 执行模块

负责：

- 读取候选因子
- 运行训练和回测
- 产出评估结果

### 模块 5：实验记录模块

负责：

- 保存每轮方案
- 保存每轮得分
- 保存失败原因

这 5 个模块够你跑通第一版了。

---

## 第 12 步：你应该先做哪些最具体的小任务

如果你明天就准备开工，我建议你按这个顺序来：

### 第 1 周目标：把 Qlib 数据跑通

- 能初始化 Qlib
- 能读出指定股票、指定日期的数据
- 能算出 10 个基础特征

### 第 2 周目标：把基础评估跑通

- 能算每个特征的缺失率
- 能算特征和未来收益的关系
- 能输出一份摘要结果

### 第 3 周目标：接入大模型

- 能把摘要发给大模型
- 能让大模型按固定 JSON 格式输出 10 个候选因子
- 能检查输出是否合法

### 第 4 周目标：跑自动回测

- 能把候选因子交给 Qlib 测试
- 能自动保存结果
- 能按统一标准打分

### 第 5 周目标：跑 3 轮自动迭代

- 第一轮生成 10 个因子
- 第二轮根据结果再生成 10 个
- 第三轮继续优化

到了这一步，你就已经不是在“听概念”了，而是真的搭出了一个可运行原型。

---

## ⚠️ 真正落地时最容易翻车的 6 个地方

### 1. 偷偷用了未来数据

这是最大风险。

比如用到了未来财报、未来价格、未来复权结果，都会让回测看起来过于完美。

### 2. 因子写得太复杂

太复杂的公式通常更容易只适合历史，不适合未来。

### 3. 只看收益，不看回撤

收益高但回撤巨大，现实里你可能根本拿不住。

### 4. 忽略手续费和滑点

有些策略表面赚钱，算上成本后就没利润了。

### 5. 大模型输出不规范

如果格式乱、字段乱、表达式乱，系统根本无法自动跑。

### 6. 一上来就做太大

一开始就做全市场、多频率、多模型，通常很快会把自己拖死。

---

## ✅ 最推荐的落地顺序

如果你问我“最稳的实现顺序是什么”，我的建议只有一句：

> 先做“因子发现闭环”，  
> 再做“模型搜索闭环”，  
> 最后再做“策略优化闭环”。

也就是：

1. 先让大模型发现新因子  
2. 再让大模型帮你组合模型  
3. 最后再让大模型优化买卖规则  

这样成功率最高，也最容易一步一步验证价值。

---

## 📝 最后给你一个最简落地版本

如果你只想先做一个能跑起来的版本，那么请只做下面这件事：

### 最简版任务

- 用 Qlib 的日线历史数据
- 准备 10 个基础特征
- 让大模型生成 10 个新因子
- 用 Qlib 回测这 10 个因子
- 选出前 3 个表现最稳定的因子
- 再让大模型基于这 3 个因子继续生成下一轮

只要这个闭环能跑通，你就已经搭出了：

> 一个“Qlib + 大模型”的自动研究原型系统。

这就是从概念走向落地的第一步。

---

*本文只用于学习交流，不构成任何投资建议。投资有风险，入市需谨慎。*
