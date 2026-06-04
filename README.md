# Alpha Factory 🏭

> 基于 LLM 的全自动量化选股因子挖掘系统

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-green.svg)](https://www.python.org/)
[![Java 17](https://img.shields.io/badge/Java-17-orange.svg)](https://openjdk.org/)
[![Vue 3](https://img.shields.io/badge/Vue-3-brightgreen.svg)](https://vuejs.org/)

***

## 🎯 项目定位

Alpha Factory 是一套**端到端的量化因子挖掘流水线**，利用大语言模型（LLM）自动生成、验证、评估和筛选选股因子。系统覆盖从数据获取到因子回测的完整链路，支持多窗口训练、样本外盲测、第三方平台校验，并提供 Web 管理界面进行多任务编排。

### 核心特点

- 🤖 **LLM 驱动的因子发现**：多 Agent 协作（7 位专业分析师 → 首席分析师 → 因子生成器 → 评审员），自动生成可执行的因子公式
- 📊 **完整的因子分析流水线**：特征健康检查 → 因子评估（IC/RankIC/IR）→ 回测硬筛 → 综合打分 → 跨窗口验证
- 🔬 **严格的样本外检验**：训练/验证/测试三段式分离，杜绝过拟合
- ✅ **第三方平台校验**：支持 RiceQuant（米框）因子分析校验 + JoinQuant（聚宽）回测校验
- 🌐 **Web 管理界面**：多任务管理、YAML 配置编辑、脚本执行控制、实时日志推送
- 📁 **Staging 目录隔离**：多任务并行互不干扰，类似云计算的"租户"机制

***

## 📐 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Web 管理界面 (Vue 3)                        │
│            配置编辑 · 任务管理 · 执行控制 · 实时日志               │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP API / WebSocket
┌──────────────────────────▼───────────────────────────────────┐
│                  后端服务 (Spring Boot 3)                      │
│              认证 · 任务调度 · 配置管理 · 进程管理                │
└──────────────────────────┬───────────────────────────────────┘
                           │ ProcessBuilder
┌──────────────────────────▼───────────────────────────────────┐
│                  Python 因子挖掘流水线                          │
│                                                              │
│  Step00 ── Step01 ── Step02 ── ... ── Step09 ── Step14       │
│  清理     预缓存   特征体检   ...     综合打分   聚宽导出          │
│                                                              │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────┐    │
│  │ 数据提供层    │  │ LLM Agent 层 │  │   回测引擎层        │    │
│  │ (Tushare)   │  │ (多Agent协作) │  │ (TopK/SoftTopK/   │    │
│  │ SQLite缓存   │  │              │  │  择时/增强指数)     │    │
│  └─────────────┘  └──────────────┘  └───────────────────┘    │ 
└──────────────────────────────────────────────────────────────┘
```

***

## 🔄 因子挖掘流水线

### Step10：样本内训练

```
Step00(清理) → Step01(预缓存) → [每个训练窗口]
  → Step02(特征体检) → Step03(市场分析)
  → [每轮迭代] Step04(摘要) → Step05(LLM生成) → Step06(公式校验)
    → Step07(因子评估) → Step08(回测硬筛) → Step09(综合打分)
  → 窗口内候选汇总 → Validation(Step07~09)
→ 跨窗口总榜
```

### Step11：样本外盲测

```
Step01(预缓存) → 收集训练优胜因子 → Step07(OOS评估) → Step08(OOS回测) → Step09(OOS打分)
```

### 辅助步骤

| 步骤                              | 功能                     |
| ------------------------------- | ---------------------- |
| `train_window_selector.py`      | 训练窗口推荐（独立前置）           |
| `step12_alphalens_report.py`    | OOS 因子复盘（Alphalens 分析） |
| `step13_alphalens_dashboard.py` | Streamlit 交互式看板        |
| `step14_joinquant_export.py`    | 生成聚宽回测代码（第三方校验）        |

***

## 🚀 快速开始

### 环境要求

| 依赖      | 版本         | 用途          |
| ------- | ---------- | ----------- |
| Python  | 3.13+      | 因子挖掘流水线     |
| Java    | 17+        | Web 后端服务    |
| Node.js | 18+        | Web 前端开发    |
| MySQL   | 5.7+ / 8.x | Web 应用数据库   |
| Conda   | 最新         | Python 环境管理 |

### 1. 创建 Python 环境

```powershell
& D:\miniconda3\Scripts\conda.exe init powershell
# 重新打开终端后
conda create -n alpha-factory python=3.13 -y
conda activate alpha-factory
```

### 2. 安装依赖

```powershell
pip install -r runtime/src/requirements.txt
```

### 3. 配置

```powershell
# 复制配置模板并填入密钥
cp runtime/config/env.yaml.example runtime/config/env.yaml
# 编辑 env.yaml，填入 Tushare API Key 和 LLM API Key
```

### 4. 运行因子挖掘（命令行方式）

```powershell
# 设置 Staging 目录
$env:STAGING_DIR="path-to-staging"

# 样本内训练
python runtime/src/step10_iterate.py

# 样本外盲测
python runtime/src/step11_oos_test.py
```

### 5. 启动 Web 管理界面（可选）

```powershell
# 启动后端
cd backend
mvn spring-boot:run

# 启动前端（开发模式）
cd webapp
npm install
npm run dev
```

访问 `http://localhost:5173`，默认管理员账号 `admin` / `admin123`

***

## 📁 项目结构

```
factor-factory/
├── runtime/                    # 运行时目录
│   ├── config/                 # 配置文件（7 个 YAML）
│   │   ├── env.yaml.example    # 环境配置模板（需复制为 env.yaml）
│   │   ├── analysis_rule.yaml  # 分析规则
│   │   ├── backtest_rule.yaml  # 回测规则
│   │   ├── feature_pool.yaml   # 特征池
│   │   ├── market_context.yaml # 市场环境
│   │   ├── score.yaml          # 评分规则
│   │   └── selector.yaml       # 窗口选择器
│   ├── src/                    # Python 源码
│   │   ├── step00~step14       # 流水线各步骤
│   │   ├── data_provider/      # 数据提供层（Tushare + SQLite 缓存）
│   │   ├── engine/             # 回测引擎（TopK/SoftTopK/择时/增强指数）
│   │   ├── llm_agents/         # 多 Agent 协作层
│   │   ├── common.py           # 公共工具
│   │   ├── market_regime.py    # 市场状态识别
│   │   └── stock_pool_manager.py # 股票池管理
│   └── ricequant/              # RiceQuant 校验脚本
├── backend/                    # Spring Boot 后端
│   └── src/main/java/.../webapp/
│       ├── config/             # 安全、WebSocket、属性配置
│       ├── controller/         # REST API 控制器
│       ├── entity/             # JPA 实体
│       ├── service/            # 业务逻辑（认证/任务/配置/执行）
│       └── security/           # JWT 认证
├── webapp/                     # Vue 3 前端
│   └── src/
│       ├── api/                # API 调用层
│       ├── views/              # 页面组件
│       ├── stores/             # Pinia 状态管理
│       └── router/             # 路由配置
└── jq2qmt/                     # 聚宽→QMT 信号转发中间件
```

***

## 🧠 多 Agent 因子挖掘

系统采用多 Agent 协作架构，模拟专业量化研究团队的工作流程：

```
┌─────────────────────────────────────────────────┐
│           7 位专业分析师（并行）                    │
│  趋势动量 │ 反转均值 │ 波动风险 │ 量价关系            │
│  微结构   │ 筹码分布 │ 基本面   │                   │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│              首席分析师（整合）                    │
│         汇总 7 份分析，输出统一研究方向              │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│              因子生成器                           │
│         按研究方向生成可执行的因子公式               │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│              因子评审员                           │
│         PASS / REJECT 严格评审                    │
└─────────────────────────────────────────────────┘
```

每个 Agent 可独立配置 LLM 模型、温度、超时等参数，灵活适配不同场景。

***

## 📊 回测引擎

系统内置 3 种回测策略引擎：

| 策略                | 说明                | 设计文档   |
| ----------------- | ----------------- | ------ |
| **TopK-Dropout**  | 经典 TopK 选股 + 尾部替换 | 知识星球专属 |
| **Soft-TopK**     | 基于因子分数的连续权重分配     | 知识星球专属 |
| **Market Timing** | 市场择时 + 因子选股       | 知识星球专属 |

***

## ️ 第三方校验

为确保本地因子分析和回测结果的可靠性，系统支持两个第三方平台的交叉校验：

- **RiceQuant（米框）**：因子分析结果校验，通过 Jupyter Notebook 脚本比对 IC、RankIC 等指标
- **JoinQuant（聚宽）**：回测结果校验，通过 `step14_joinquant_export.py` 生成聚宽策略代码，逐调仓日比对交易行为

***

## 💬 社区与交流

### 知识星球


> 🌟 **加入知识星球，获取更多Vibe Coding和因子挖掘的深度内容**
>
> - 独家因子挖掘技巧与实战案例
> - LLM + 量化投资的前沿探索
> - 社群答疑与代码交流
>
> 🔗 知识星球链接：[https://wx.zsxq.com/group/51115845848284](https://wx.zsxq.com/group/51115845848284)
>
> 📱 扫码加入：
>
> ![知识星球二维码](知识星球.png)

### 量化入门教程

8 篇系统设计思路与解析教程（持续更新），从零开始讲解本项目的设计构思和实现细节。教程为**知识星球专属内容**，加入知识星球即可获取完整教程：

| 篇章    | 主题          |
| ----- | ----------- |
| 第 1 篇 | 导论篇         |
| 第 2 篇 | 数据源与股票池篇    |
| 第 3 篇 | 数据预处理与标签篇   |
| 第 4 篇 | 市场环境刻画篇     |
| 第 5 篇 | 因子挖掘与评估篇    |
| 第 6 篇 | 回测与策略执行篇    |
| 第 7 篇 | 评估迭代与第三方校验篇 |
| 第 8 篇 | 实操与进阶篇      |

### 其他联系方式

- 📕 小红书：![小红书二维码](小红书.jpg)
- 🎵 抖音：![抖音二维码](抖音.png)

***

## ⚠️ 免责声明

本项目仅供学习和研究使用，**不构成任何投资建议**。量化投资存在风险，过往业绩不代表未来表现。使用本系统产生的任何投资决策和结果，由使用者自行承担全部责任。

本项目使用的 Tushare 数据需要 [Tushare Pro](https://tushare.pro/) 账号，请遵守其数据使用协议。

***

## 📄 开源协议

本项目基于 [Apache License 2.0](LICENSE) 开源。

***

## 🙏 致谢

- [Tushare Pro](https://tushare.pro/) - A 股数据接口
- [Alphalens](https://github.com/quantopian/alphalens) - 因子分析框架
- [RiceQuant](https://www.ricequant.com/) - 因子分析校验平台
- [JoinQuant](https://www.joinquant.com/) - 回测校验平台

