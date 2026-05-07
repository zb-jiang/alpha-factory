# 多 Agent 团队式因子挖掘设计文档

## 1. 背景与目标

### 1.1 当前痛点

`runtime/src/step05_call_llm.py` 当前的因子挖掘采用**单 Prompt 直接生成**模式：
- 一个 system prompt 定义角色 + 一个 user prompt 拼接所有上下文信息
- LLM 一次性输出候选因子列表
- 缺乏中间思考过程，生成的因子质量完全依赖单次推理能力
- 没有"讨论"、"辩论"、"评审"环节，无法识别和纠正潜在问题

### 1.2 设计目标

在不改变 `step05` 与上下游接口（输入文件、输出文件 schema）的前提下，将内部的单 Prompt 调用替换为**多 Agent 团队协作流程**，实现：
- **专业化分工**：不同 Agent 从不同专业视角分析市场和特征
- **思辨过程**：Agent 之间有明确的输出-输入依赖关系，形成可审计的思考链
- **质量把关**：独立评审 Agent 对候选因子做最终质量把关
- **模型异构**：每个 Agent 可配置不同的 LLM 后端，发挥各模型优势

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  输入层（与现有 step05 输入完全一致）                              │
│  ├── env.yaml (含 llm_agents 新配置块)                           │
│  ├── feature_pool.yaml (特征池)                                  │
│  ├── outputs/health/llm_summary.json (特征体检报告)              │
│  ├── outputs/health/market_context.json (市场环境)               │
│  └── outputs/backtest/top3_factors.json / skipped_factors.json   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  第一层：专业分析师团队（并行，6个Agent）                          │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ 趋势动量    │  │ 反转均值    │  │ 波动率风险  │             │
│  │ 分析师      │  │ 回复分析师  │  │ 分析师      │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │                │                     │
│  ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐             │
│  │ 量价关系    │  │ 微观结构    │  │ 筹码分布    │             │
│  │ 分析师      │  │ 分析师      │  │ 分析师      │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│                                                                  │
│  每个分析师独立调用 LLM，读取相同的输入上下文，                     │
│  从自己的专业视角输出本轮因子设计建议。                             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  第二层：首席分析师（串行，1个Agent）                              │
│                                                                  │
│  读取 6 个专业分析师的建议，做整合与裁决：                          │
│  - 识别共识方向 vs 分歧意见                                       │
│  - 结合当前市场状态给出统一的因子设计方向                         │
│  - 明确推荐特征、规避特征、风险警示                               │
│  - 确定本轮候选因子数量目标                                       │
│                                                                  │
│  输出：outputs/llm/design_direction.json                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  第三层：因子生成器（串行，1个Agent）                              │
│                                                                  │
│  严格遵循首席分析师的设计方向，生成具体因子公式列表。               │
│  输出格式与现有 raw_response.json schema 一致。                   │
│                                                                  │
│  输出：outputs/llm/raw_response_draft.json                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  第四层：因子评审员（串行，1个Agent）                              │
│                                                                  │
│  对每个候选因子逐一评审，只给出两种决定：                          │
│  - PASS：通过，进入最终候选池                                    │
│  - REJECT:<原因>：拒绝，说明具体原因                             │
│                                                                  │
│  评审维度：未来函数风险、逻辑合理性、与已淘汰因子相似度、          │
│           特征使用合规性、过度拟合嫌疑                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  输出层（与现有 step05 输出格式完全一致）                          │
│  └── outputs/llm/raw_response.json                               │
│       （符合现有 schema 的 factors 列表）                        │
└─────────────────────────────────────────────────────────────────┘
```

## 3. Agent 角色定义

### 3.1 专业分析师（6个，并行执行）

每个专业分析师拥有独立的 `model` / `base_url` / `api_key` / `system_prompt` 配置。

| Agent ID | 角色名称 | 专业领域 | 核心可用特征 | 经济学逻辑基础 |
|---------|---------|---------|------------|--------------|
| `trend_momentum` | 趋势动量分析师 | 价格趋势、动量持续性、区间突破 | `ret_5d/20d/60d`, `breakout_20d`, `price_pos_20d` | 反应不足假说、信息缓慢扩散、趋势跟踪策略 |
| `reversal_mean_reversion` | 反转均值回复分析师 | 过度反应后的价格回归 | `ret_1d/5d`, `max_drawdown_20d`, `close_to_high/low`, `gap_open_ret` | 投资者过度反应、短期反转效应、价格锚定偏差 |
| `volatility_risk` | 波动率风险分析师 | 波动率择时、风险调整、低波动异象 | `realized_vol_20d`, `high_low_range`, `volume_vol_20d` | 低波动异象、波动率聚集效应、风险补偿不对称 |
| `volume_price` | 量价关系分析师 | 成交量确认、量价背离、资金强度 | `volume_ratio_20d`, `amount_mean_20d`, `vwap`, `volume_vol_20d` | 知情交易假说、流动性冲击、筹码交换速率 |
| `microstructure` | 微观结构分析师 | 日内模式、开盘收盘行为、价格区间结构 | `intraday_ret`, `gap_open_ret`, `close_to_high/low`, `high_low_range` | 开盘/收盘效应、日内信息不对称、价格发现过程 |
| `chip_distribution` | 筹码分布分析师 | 筹码成本结构、获利盘压力、支撑阻力 | `chip_concentration_90d/210d`, `profit_ratio_90d/210d`, `avg_cost_distance_90d/210d`, `peak_distance_90d/210d` | 筹码锁定效应、获利盘抛压、成本支撑阻力 |

**每个分析师的输出格式**（统一 schema，便于首席分析师解析）：

```json
{
  "agent_id": "trend_momentum",
  "recommendation_score": 0.75,
  "rationale": "当前市场处于震荡上行阶段，20日趋势指标呈多头排列，动量因子有显著预测能力。",
  "recommended_features": ["ret_20d", "price_pos_20d", "breakout_20d"],
  "avoid_features": ["ret_1d", "max_drawdown_20d"],
  "risk_warnings": ["趋势因子在波动率急剧放大时容易失效", "需警惕动量崩溃风险"],
  "suggested_factor_types": ["动量延续", "突破确认", "趋势强度"]
}
```

### 3.2 首席分析师（1个，串行执行）

**职责**：
- 读取 6 个专业分析师的输出
- 识别共识方向（多个分析师一致推荐的因子类型）
- 处理分歧意见（如动量分析师推荐追涨，反转分析师推荐抄底）
- 结合 `market_context.json` 中的 `train_context` 做最终裁决
- 输出统一的设计方向，作为生成器的唯一输入

**输出格式**：

```json
{
  "primary_focus": "反转+波动率调整",
  "recommended_features": ["ret_5d", "realized_vol_20d", "volume_ratio_20d"],
  "avoid_features": ["ret_60d", "breakout_20d"],
  "risk_warnings": ["高波动环境下动量因子容易失效，本轮以反转为主", "需控制换手率惩罚"],
  "diversification_goal": "至少覆盖2种不同逻辑（反转+量价）",
  "candidate_count": 10,
  "analyst_consensus": {
    "high_agreement": ["反转因子当前环境适用性高"],
    "disagreements": ["动量分析师建议追涨，但波动率分析师和微观结构分析师均提示风险"]
  }
}
```

### 3.3 因子生成器（1个，串行执行）

**职责**：
- 严格遵循 `design_direction.json` 中的设计方向
- 只能使用 `recommended_features` 中列出的特征
- 生成 `candidate_count` 个候选因子
- 输出格式与现有 `raw_response.json` 完全一致（包含 `factors` 列表）

**system prompt 核心约束**：
- "你的唯一输入是首席分析师的设计方向建议"
- "你必须严格遵循设计方向，不得偏离"
- "你只能使用 recommended_features 中列出的特征"
- "每个因子必须有明确的经济学逻辑解释"

### 3.4 因子评审员（1个，串行执行）

**职责**：
- 对生成器输出的每个因子逐一评审
- 只给出两种决定：`PASS` 或 `REJECT:<原因>`
- 评审通过的因子写入最终 `raw_response.json`
- 评审拒绝的因子记录到 `review_rejected.json`（用于审计）

**评审维度**（按优先级排序）：
1. **未来函数风险**：公式中是否使用了未来数据（如 `.shift(-1)`）
2. **特征合规性**：公式中使用的特征是否在 `feature_pool.yaml` 的 `base_features` 中
3. **逻辑一致性**：因子逻辑是否与 `design_direction.json` 中的 `primary_focus` 一致
4. **与已淘汰因子相似度**：公式是否与 `skipped_factors.json` 中的因子过度相似
5. **过度拟合嫌疑**：公式是否过于复杂、参数过多、缺乏经济学直觉

**评审输出格式**：

```json
{
  "review_results": [
    {
      "factor_name": "momentum_vol_ratio_v1",
      "decision": "PASS",
      "reason": ""
    },
    {
      "factor_name": "complex_interaction_v3",
      "decision": "REJECT:公式包含5层嵌套运算，过度复杂，存在过度拟合风险",
      "reason": "过度拟合嫌疑"
    }
  ]
}
```

## 4. 配置设计

### 4.1 新增 `env.yaml` 配置块

在现有 `env.yaml` 中新增 `llm_agents` 配置块。**仅配置 LLM 连接参数**，`system_prompt` 统一在 Python 代码中定义（与当前 `step05_call_llm.py` 保持一致）：

```yaml
# ==============================================================================
# 多 Agent 因子挖掘配置（新增）
# ==============================================================================

# 是否启用多 Agent 模式（false 时回退到现有单 Prompt 模式）
enable_multi_agent: true

# Agent 配置（每个 Agent 可独立配置 LLM 后端；system_prompt 在代码中定义）
llm_agents:
  # ── 专业分析师（并行层）──
  trend_momentum:
    model: gpt-4o
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    temperature: 0.3
    timeout_seconds: 60
    max_retries: 2

  reversal_mean_reversion:
    model: deepseek-chat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    temperature: 0.2
    timeout_seconds: 60
    max_retries: 2

  volatility_risk:
    model: claude-3-5-sonnet-20241022
    base_url: https://api.anthropic.com/v1
    api_key: ${ANTHROPIC_API_KEY}
    temperature: 0.2
    timeout_seconds: 60
    max_retries: 2

  volume_price:
    model: gpt-4o
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    temperature: 0.3
    timeout_seconds: 60
    max_retries: 2

  microstructure:
    model: deepseek-chat
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    temperature: 0.2
    timeout_seconds: 60
    max_retries: 2

  # ── 首席分析师（整合层）──
  chief_analyst:
    model: claude-3-5-sonnet-20241022
    base_url: https://api.anthropic.com/v1
    api_key: ${ANTHROPIC_API_KEY}
    temperature: 0.1
    timeout_seconds: 90
    max_retries: 2

  # ── 因子生成器（执行层）──
  generator:
    model: gpt-4o
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    temperature: 0.2
    timeout_seconds: 90
    max_retries: 2

  # ── 因子评审员（质量层）──
  reviewer:
    model: claude-3-haiku-20240307
    base_url: https://api.anthropic.com/v1
    api_key: ${ANTHROPIC_API_KEY}
    temperature: 0.1
    timeout_seconds: 60
    max_retries: 2
```

**system prompt 定义位置**：所有 Agent 的 `system_prompt` 统一在 `runtime/src/llm_agents/prompts.py`（或同目录下的各模块文件）中以 Python 字符串常量定义，与当前 `step05_call_llm.py` 的 `system_prompt` / `user_prompt` 定义方式保持一致。这样做的好处：
- 便于 IDE 进行语法高亮、拼写检查和重构
- prompt 版本与代码版本同步
- 支持 Python 字符串格式化（f-string）动态注入变量

### 4.2 `llm_candidate_count` 的使用

`env.yaml` 中现有的 `llm_candidate_count` 配置继续作为**因子生成器的默认生成数量**：
- 因子生成器在调用时读取 `env.yaml` 中的 `llm_candidate_count`
- 首席分析师在 `design_direction.json` 中输出的 `candidate_count` 字段默认继承该值
- 首席分析师有权根据市场状态调整该值（如高波动环境下建议生成更多因子以增加多样性），但默认必须保持与 `llm_candidate_count` 一致
- 因子生成器最终以 `design_direction.json` 中的 `candidate_count` 为准，若该字段缺失则 fallback 到 `llm_candidate_count`

### 4.3 向后兼容性

- `enable_multi_agent: false` 时，完全回退到现有的单 Prompt 调用逻辑
- 保留现有的 `llm_model` / `llm_base_url` / `llm_api_key` 配置项作为 fallback
- 如果 `llm_agents` 配置缺失或某个 Agent 配置不完整，使用全局 `llm_*` 配置作为默认值

## 5. 数据流与文件接口

### 5.1 输入文件（与现有 step05 一致）

| 文件路径 | 说明 | 使用者 |
|---------|------|--------|
| `env.yaml` | 环境配置（含 `llm_agents` 新配置块） | 所有 Agent |
| `feature_pool.yaml` | 特征池定义 | 所有 Agent |
| `outputs/health/llm_summary.json` | 特征体检报告 | 所有 Agent |
| `outputs/health/market_context.json` | 市场环境上下文 | 所有 Agent |
| `outputs/backtest/top3_factors.json` | 上一轮 Top3 因子（可选） | 所有 Agent |
| `outputs/backtest/skipped_factors.json` | 上一轮被淘汰因子（可选） | 所有 Agent |

### 5.2 新增中间产物

| 文件路径 | 说明 | 写入者 | 读取者 |
|---------|------|--------|--------|
| `outputs/llm/agent_outputs/<agent_id>.json` | 每个专业分析师的原始输出 | 专业分析师 | 首席分析师、审计 |
| `outputs/llm/design_direction.json` | 首席分析师整合后的设计方向 | 首席分析师 | 因子生成器、审计 |
| `outputs/llm/raw_response_draft.json` | 生成器产出的候选因子（评审前） | 因子生成器 | 因子评审员、审计 |
| `outputs/llm/review_report.json` | 评审员的详细评审报告 | 因子评审员 | 审计 |
| `outputs/llm/review_rejected.json` | 被评审员拒绝的因子列表 | 因子评审员 | 审计 |

### 5.3 输出文件（与现有 step05 完全一致）

| 文件路径 | 说明 | 写入者 |
|---------|------|--------|
| `outputs/llm/raw_response.json` | 最终候选因子列表（通过评审的因子） | 因子评审员 |

**schema 兼容性保证**：`raw_response.json` 的内容格式与现有单 Prompt 模式完全一致，包含 `factors` 数组，每个因子有 `factor_name` / `formula` / `fields` / `direction` / `reason` / `risk` / `expected_failure_regime` 字段。step06 及后续流程无需任何修改。

## 6. 错误处理策略

### 6.1 专业分析师失败

- **单个分析师失败**：记录错误日志，其他分析师继续执行。首席分析师在整合时忽略该分析师的输出。
- **全部分析师失败**：回退到现有单 Prompt 模式（使用全局 `llm_model` 配置）。

### 6.2 首席分析师失败

- 回退到现有单 Prompt 模式。
- 或者，如果至少有一个专业分析师成功，使用第一个成功分析师的建议作为设计方向（降级模式）。

### 6.3 因子生成器失败

- 重试一次（使用相同的设计方向）。
- 仍然失败则回退到现有单 Prompt 模式。

### 6.4 因子评审员失败

- 生成器产出的所有因子默认全部通过（偏激进策略）。
- 或者回退到现有单 Prompt 模式（偏保守策略）。
- **推荐**：默认全部通过，但记录评审员失败日志。

## 7. 实现要点

### 7.1 代码结构

建议将多 Agent 逻辑抽取为独立模块，保持 `step05_call_llm.py` 的入口简洁：

```
runtime/src/
├── step05_call_llm.py              # 入口，根据 enable_multi_agent 路由
├── llm_agents/
│   ├── __init__.py
│   ├── agent_runner.py             # Agent 调用封装（统一处理 timeout/retry/错误）
│   ├── analyst_team.py             # 专业分析师团队并行执行
│   ├── chief_analyst.py            # 首席分析师整合
│   ├── generator.py                # 因子生成器
│   ├── reviewer.py                 # 因子评审员
│   └── prompts/                    # 各 Agent 的 prompt 模板
│       ├── system_prompts.yaml     # 所有 system prompt 集中管理
│       └── user_prompt_templates/  # user prompt 模板
```

### 7.2 并行执行策略

- 使用 `concurrent.futures.ThreadPoolExecutor` 并行执行 6 个专业分析师的 LLM 调用
- 设置统一的超时时间（如 120 秒），避免某个 Agent 卡死阻塞整体流程
- 每个 Agent 的调用相互独立，不共享状态

### 7.3 Prompt 管理

- 所有 Agent 的 `system_prompt` 以 Python 字符串常量的形式定义在 `runtime/src/llm_agents/` 各模块中（如 `analyst_team.py`、`chief_analyst.py`、`generator.py`、`reviewer.py`）
- 与当前 `step05_call_llm.py` 的定义方式完全一致：直接在代码中写多行字符串
- user prompt 的构建逻辑同样放在各模块中，通过函数参数接收动态上下文数据
- 这种方式便于 IDE 进行语法高亮、拼写检查和重构，同时保证 prompt 版本与代码版本同步

## 8. 扩展性

### 8.1 新增分析师角色

当 `feature_pool.yaml` 中补充了新的特征域（如基本面特征、宏观数据特征）时，可在 `llm_agents` 配置中新增对应的分析师角色，无需修改代码：

```yaml
llm_agents:
  value_analyst:        # 新增：价值分析师（仅配置连接参数）
    model: gpt-4o
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
  macro_analyst:        # 新增：宏观分析师（仅配置连接参数）
    model: claude-3-5-sonnet
    base_url: https://api.anthropic.com/v1
    api_key: ${ANTHROPIC_API_KEY}
```

对应的 `system_prompt` 在 `runtime/src/llm_agents/analyst_team.py` 中新增 Python 字符串常量即可。

### 8.2 调整评审严格度

可通过 `env.yaml` 新增配置项控制评审严格度：

```yaml
llm_agents:
  reviewer:
    strictness: normal   # 可选: strict / normal / lenient
```

## 9. 验收标准

- [ ] `enable_multi_agent: true` 时，step05 执行多 Agent 流程，输出 `raw_response.json`
- [ ] `enable_multi_agent: false` 时，step05 完全回退到现有单 Prompt 模式
- [ ] step06~step09 无需任何修改即可正常工作
- [ ] 所有中间产物（`agent_outputs/`、`design_direction.json`、`review_report.json`）正确生成
- [ ] 每个 Agent 可独立配置不同的 `model` / `base_url` / `api_key`
- [ ] 单个 Agent 失败不影响其他 Agent 的执行
- [ ] 全部 Agent 失败时，系统能优雅回退到单 Prompt 模式
