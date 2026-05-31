# 国金证券开户与 QMT/miniQMT 安装配置指南

## 1. 国金证券简介

| 维度 | 详情 |
|------|------|
| 全称 | 国金证券股份有限公司 |
| 股票代码 | 600109（上交所上市） |
| 成立时间 | 1996年，总部成都 |
| 证监会分类评级 | A类AA级 |
| 主体信用评级 | AAA |
| 特色 | "佣金宝"低佣金开创者，互联网券商先驱 |
| QMT 门槛 | **10万元**（行业最低档） |

---

## 2. 开户流程

### 2.1 前置条件

| 条件 | 要求 |
|------|------|
| 年龄 | 18-65 周岁 |
| 身份证件 | 二代身份证在有效期内 |
| 银行卡 | 一类银行卡（建议工行/建行/招行） |
| 资金门槛 | 账户资产 ≥ 10 万元（普通柜台）/ ≥ 300 万元（极速柜台） |
| 交易经验 | 至少半年以上股票交易经验 |
| 风险等级 | 积极型或激进型 |

### 2.2 开户步骤

#### 步骤1：联系客户经理（重要！）

**不要直接在官网或 APP 自助开户**，先联系客户经理，原因：
- 通过客户经理渠道可以协商更低佣金（默认万3，可谈到万1-万1.5）
- 客户经理可以协助降低 QMT 开通门槛
- 后续技术问题有专人对接

获取客户经理方式：
- 国金证券官网在线客服
- 佣金宝 APP → 在线客服 → 要求分配客户经理
- 搜索"国金证券 客户经理"找到联系方式

#### 步骤2：线上开户

通过客户经理提供的专属链接或二维码开户：

1. 扫描客户经理提供的开户二维码
2. 上传身份证正反面照片（清晰无遮挡）
3. 填写个人信息（姓名、地址、职业等）
4. 绑定银行卡（设置三方存管）
5. 设置交易密码和资金密码
6. 完成风险测评（选择"积极型"或"激进型"）
7. 视频认证（人工视频，回答几个简单问题）
8. 提交申请，等待审核（通常 1 个工作日）

#### 步骤3：入金

开户成功后，将资金转入证券账户：
- 通过银行 APP 或柜台转账
- 建议一次性转入 ≥ 10 万元，满足 QMT 开通门槛

#### 步骤4：协商佣金

联系客户经理协商佣金费率：

| 交易类型 | 默认费率 | 可协商费率 |
|----------|----------|-----------|
| 股票佣金 | 万3 | 万1 ~ 万1.5 |
| 印花税 | 千1（卖出） | 不可协商（国家税收） |
| 过户费 | 十万分之1.5 | 不可协商 |

---

## 3. QMT 权限开通

### 3.1 申请量化交易权限

#### 方式A：佣金宝 APP 线上申请（推荐）

1. 打开 **佣金宝 APP**
2. 点击 **我的** → **我的业务**
3. 点击 **权限开通**
4. 选择 **开通 PTrade/QMT/底仓增强权限**
5. 阅读并签署《量化交易协议》
6. 提交申请，等待审核（1-3 个工作日）

#### 方式B：联系客户经理协助

如果 APP 中找不到入口，直接联系客户经理，提供：
- 资金账号
- 策略类型说明（如"日频量化选股"）
- 预计使用场景

### 3.2 程序化交易报备（必须！）

根据证监会监管规定，使用 QMT 进行程序化交易必须完成报备：

1. 打开 **佣金宝 APP**
2. 进入 **业务办理** → **问卷调查**
3. 选择 **程序化交易问卷**
4. 按照实际情况填写，**注意第19题选择"国金证券智能策略交易终端（QMT）"**
5. 提交报备

> ⚠️ 未完成报备可能被限制交易频率或暂停交易权限

### 3.3 审核通过后

审核通过后，客户经理会：
- 发送 QMT 客户端下载链接（专属链接，非公开下载）
- 发送安装文档和配置说明
- 提供模拟账号用于测试

---

## 4. QMT 客户端安装

### 4.1 硬件要求

| 项目 | 要求 |
|------|------|
| 操作系统 | **Windows 10/11 64位**（Mac 需虚拟机） |
| 内存 | 建议 16GB+ |
| 硬盘 | **200GB+ 非 C 盘空间**（1min K线数据约占 200GB） |
| 网络 | 稳定的宽带连接 |

### 4.2 安装步骤

1. **下载安装包**
   - 从客户经理提供的专属链接下载
   - 文件名类似 `国金证券QMT交易端_XXXX.exe`
   - ⚠️ 不要从第三方网站下载，存在账号泄露风险

2. **运行安装向导**
   - 双击安装包，弹出「ThinkTrader 安装向导」
   - 点击「下一步」，同意用户协议

3. **选择安装路径（核心避坑！）**
   - **必须安装在非 C 盘**
   - **路径不能包含中文和特殊字符**
   - 推荐路径：`D:\GuojinQMT\bin.x64`
   - ❌ 错误路径：`C:\Program Files\国金QMT\`（有中文且在C盘）
   - ✅ 正确路径：`D:\GuojinQMT\bin.x64`

4. **完成安装**
   - 勾选「创建桌面快捷方式」
   - 点击「完成」

5. **首次启动**
   - **右键桌面快捷方式 → 以管理员身份运行**（Windows 必做，否则权限不足）
   - 输入资金账号和密码登录

### 4.3 下载 Python 库（必须！）

首次登录后需要下载 Python 库，否则无法使用策略功能：

1. 登录 QMT 客户端
2. 点击顶部菜单栏 **系统设置**（齿轮图标）
3. 切换到 **模块设置** 标签
4. 找到 **Python 库路径**
5. 点击 **下载 Python 库**
6. 等待下载完成（约 20 分钟，取决于网速）
7. 勾选 **提示 Python 库更新**
8. **重启 QMT 客户端**

> ⚠️ 如果下载失败：删除安装目录 `bin.x64` 下的 `Lib` 文件夹，重启 QMT 后重新下载

---

## 5. miniQMT 配置

### 5.1 什么是 miniQMT

miniQMT 是 QMT 的极简模式，核心区别：

| 维度 | 标准 QMT | miniQMT |
|------|----------|---------|
| 策略编写 | QMT 内置编辑器 | 任意 Python IDE（PyCharm/VSCode） |
| Python 版本 | 内置 3.6.8 | 可用本地 3.6-3.11 |
| 资源占用 | 高（完整 GUI） | 低（仅后台服务） |
| 调试体验 | 差（无断点调试） | 好（完整 IDE 支持） |
| 适用场景 | 简单策略 | 复杂策略 + 外部信号执行 |

### 5.2 启动 miniQMT

有两种方式启动 miniQMT：

#### 方式A：直接启动 XtMiniQmt.exe（推荐）

1. 找到安装目录下的 `XtMiniQmt.exe`
   - 路径示例：`D:\GuojinQMT\bin.x64\XtMiniQmt.exe`
2. 双击运行
3. 输入资金账号和密码登录
4. 登录成功后界面非常简洁，只有一个托盘图标

#### 方式B：从 QMT 客户端切换

1. 启动 QMT 客户端（`XtItClient.exe`）
2. 登录时勾选 **极简模式** 或 **独立交易**
3. 登录后自动进入 miniQMT 模式

> ⚠️ 使用 xtquant 程序前，**必须先启动 miniQMT 客户端**，否则会报错"无法连接行情服务"

### 5.3 配置本地 Python 环境

#### 步骤1：确认 xtquant 库位置

QMT 安装完成后，xtquant 库位于：

```
{QMT安装目录}\bin.x64\Lib\site-packages\xtquant\
```

例如：`D:\GuojinQMT\bin.x64\Lib\site-packages\xtquant\`

#### 步骤2：复制 xtquant 到本地 Python 环境

**方式A：直接复制（推荐，支持 IDE 代码补全）**

1. 找到 QMT 安装目录下的 xtquant 文件夹
   ```
   D:\GuojinQMT\bin.x64\Lib\site-packages\xtquant\
   ```
2. 复制整个 `xtquant` 文件夹
3. 粘贴到本地 Python 环境的 site-packages 目录
   ```
   D:\miniconda3\Lib\site-packages\xtquant\
   ```

> ⚠️ 缺点：每次 QMT 更新后需要重新复制

**方式B：sys.path.append（不推荐，无代码补全）**

在代码中动态添加路径：
```python
import sys
sys.path.append(r'D:\GuojinQMT\bin.x64\Lib\site-packages')
from xtquant import xtdata, xttrader
```

#### 步骤3：验证安装

```python
from xtquant import xtdata
print("xtquant 安装成功！")
```

如果报错 `ModuleNotFoundError`，检查复制路径是否正确。

#### 步骤4：Python 版本兼容性

| Python 版本 | 兼容性 | 说明 |
|-------------|--------|------|
| 3.6 | ✅ 完全兼容 | QMT 内置版本 |
| 3.7 | ✅ 完全兼容 | |
| 3.8 | ✅ 完全兼容 | |
| 3.9 | ⚠️ 部分兼容 | 可能出现类型注解问题 |
| 3.10 | ✅ 兼容 | 最新版 xtquant 已支持 |
| 3.11 | ✅ 兼容 | 最新版 xtquant 已支持 |
| 3.12+ | ❌ 不兼容 | 可能出现 C 扩展问题 |
| 3.13 | ❌ 不兼容 | 当前不推荐 |

> ⚠️ 你的项目使用 Python 3.13，但 xtquant 目前最高支持 3.11。建议创建一个独立的 Python 3.11 conda 环境用于 QMT 执行器：
> ```powershell
> conda create -n qmt python=3.11 -y
> conda activate qmt
> ```

### 5.4 连接测试脚本

将以下代码保存为 `test_qmt_connection.py`，用于验证 miniQMT 连接：

```python
import time
from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtdata

QMT_PATH = r'D:\GuojinQMT\userdata_mini'
ACCOUNT = '你的资金账号'

class TestCallback(XtQuantTraderCallback):
    def on_disconnected(self):
        print("❌ QMT 连接断开")

    def on_stock_order(self, order):
        print(f"📋 委托回报: {order.order_remark}")

    def on_stock_trade(self, trade):
        print(f"✅ 成交回报: {trade.order_remark} "
              f"价格={trade.traded_price} 数量={trade.traded_volume}")

    def on_order_error(self, order_error):
        print(f"❌ 委托错误: {order_error.error_msg}")

def test_connection():
    print("=" * 50)
    print("miniQMT 连接测试")
    print("=" * 50)

    # 测试1: 行情连接
    print("\n[测试1] 行情连接...")
    try:
        data = xtdata.get_market_data_ex(
            field_list=['open', 'close'],
            stock_list=['000001.SZ'],
            period='1d',
            count=5
        )
        if data:
            print("✅ 行情连接成功")
            print(data)
        else:
            print("❌ 行情数据为空")
    except Exception as e:
        print(f"❌ 行情连接失败: {e}")
        print("   请确认 miniQMT 客户端已启动")

    # 测试2: 交易连接
    print("\n[测试2] 交易连接...")
    try:
        session_id = int(time.time() * 1000)
        trader = XtQuantTrader(QMT_PATH, session_id)
        trader.register_callback(TestCallback())
        trader.start()

        connect_result = trader.connect()
        if connect_result == 0:
            print("✅ 交易连接成功")
        else:
            print(f"❌ 交易连接失败: {connect_result}")
            return

        # 测试3: 账户订阅
        print("\n[测试3] 账户订阅...")
        acc = StockAccount(ACCOUNT)
        sub_result = trader.subscribe(acc)
        if sub_result == 0:
            print("✅ 账户订阅成功")
        else:
            print(f"❌ 账户订阅失败: {sub_result}")

        # 测试4: 查询资产
        print("\n[测试4] 查询资产...")
        asset = trader.query_stock_asset(acc)
        if asset:
            print(f"✅ 总资产: {asset.m_dTotalAsset:.2f}")
            print(f"   可用资金: {asset.m_dAvailable:.2f}")
            print(f"   证券市值: {asset.m_dMarketValue:.2f}")
        else:
            print("❌ 查询资产失败")

        # 测试5: 查询持仓
        print("\n[测试5] 查询持仓...")
        positions = trader.query_stock_positions(acc)
        if positions:
            print(f"✅ 持仓数量: {len(positions)}")
            for pos in positions[:5]:
                print(f"   {pos.stock_code}: "
                      f"持有={pos.m_nVolume} 可卖={pos.m_nCanUseVolume}")
        else:
            print("   当前无持仓")

        trader.stop()

    except Exception as e:
        print(f"❌ 交易连接异常: {e}")

    print("\n" + "=" * 50)
    print("测试完成")
    print("=" * 50)

if __name__ == '__main__':
    test_connection()
```

运行前修改两个参数：
- `QMT_PATH`：改为你的 QMT 安装目录下的 `userdata_mini` 路径
- `ACCOUNT`：改为你的资金账号

运行：
```powershell
conda activate qmt
python test_qmt_connection.py
```

---

## 6. 数据补充

### 6.1 基础数据补充（必做）

1. 启动 QMT 客户端（标准模式，非极简模式）
2. 点击顶部菜单栏 **操作** → **数据管理** → **补充数据**
3. 数据选项勾选：
   - 市场：上交所 / 深交所
   - 数据类型：除权数据、分笔成交数据
4. 数据范围选择 **全部**
5. 周期选择 **日线**（根据策略需要可选 1min）
6. 点击 **补充**
7. 建议 **盘后 16:00 后操作**，避开交易时段

### 6.2 进阶数据补充（按需）

| 数据类型 | 适用场景 |
|----------|----------|
| 期货历史主力合约 | 期货策略 |
| ETF 申赎清单 | ETF 策略 |
| 龙虎榜数据 | 短线策略 |
| 财务数据 | 基本面策略 |

### 6.3 数据维护

- 定期执行 **操作 → 数据管理 → 收盘清盘**，清理冗余数据
- 增量数据建议每天盘后自动补充

---

## 7. 模拟盘测试

### 7.1 申请模拟账号

1. 联系客户经理申请 QMT 模拟账号
2. 或在佣金宝 APP 中申请模拟交易权限
3. 下载 **国金 QMT 交易端模拟** 版本（与实盘版本不同）

### 7.2 模拟盘测试流程

1. 启动模拟版 miniQMT
2. 使用模拟账号登录
3. 运行连接测试脚本验证
4. 运行信号执行器（配置模拟账号）
5. 在聚宽模拟盘触发信号，观察执行器是否正确下单
6. **建议模拟盘运行至少 5 个交易日**，确认无异常后再切实盘

---

## 8. 实盘切换

### 8.1 切换检查清单

- [ ] 模拟盘连续运行 5+ 交易日无异常
- [ ] 所有信号均被正确执行
- [ ] 风控机制正常工作
- [ ] 程序化交易报备已完成
- [ ] 了解 QMT 实盘操作规范

### 8.2 切换步骤

1. **关闭模拟版 QMT**
2. **启动实盘版 QMT**（极简模式/独立交易模式）
3. **修改执行器配置**：
   ```yaml
   qmt:
     path: D:\GuojinQMT\userdata_mini  # 实盘版路径
     account: "实盘资金账号"
   ```
4. **首日建议**：
   - 使用总资金的 10% 进行实盘验证
   - 人工监控每个信号的执行情况
   - 对比聚宽模拟盘和实盘的执行结果
5. **逐步加仓**：
   - 首日无异常 → 加到 30%
   - 连续 3 日无异常 → 加到 60%
   - 连续 5 日无异常 → 加到 100%

### 8.3 实盘注意事项

| 注意项 | 说明 |
|--------|------|
| **miniQMT 必须保持运行** | 交易时段内 miniQMT 客户端不能关闭 |
| **网络稳定** | 建议使用有线网络，避免 WiFi 波动 |
| **电脑不休眠** | 设置电源选项为"从不休眠" |
| **开机自启** | 将 miniQMT 和执行器加入开机启动项 |
| **日志监控** | 每日检查执行器日志，确认无异常 |
| **收盘后检查** | 每日收盘后核对持仓和资金 |

---

## 9. 常见问题排查

### 9.1 安装相关

| 问题 | 解决方法 |
|------|----------|
| Python 库下载失败 | 删除 `bin.x64\Lib` 文件夹，重启 QMT 重新下载 |
| 安装路径有中文 | 卸载重装，选择纯英文路径 |
| 权限不足报错 | 右键 → 以管理员身份运行 |
| 缺失策略编辑功能 | 登录时选择"行情+交易"模式，不要选"独立交易" |

### 9.2 连接相关

| 问题 | 解决方法 |
|------|----------|
| "无法连接行情服务" | 先启动 miniQMT 客户端再运行 Python 程序 |
| 连接返回非0 | 检查 `userdata_mini` 路径是否正确 |
| 账户订阅失败 | 检查资金账号是否正确，是否已开通量化权限 |
| 数据补充后不显示 | 切换带"迅投"标识的行情服务器，重启客户端 |

### 9.3 交易相关

| 问题 | 解决方法 |
|------|----------|
| 下单返回 -1 | 检查资金是否充足、股票是否停牌、是否在交易时段 |
| 委托被拒 | 检查是否完成程序化交易报备 |
| 查询资产为空 | 确认已 subscribe 账户，等待 1-2 秒后重试 |
| 行情数据缺失 | 先 download_history_data 补充数据 |

### 9.4 xtquant 相关

| 问题 | 解决方法 |
|------|----------|
| ImportError: No module named 'xtquant' | 检查 xtquant 是否复制到正确的 site-packages 目录 |
| Python 版本不兼容 | 使用 Python 3.6-3.11，不要用 3.12+ |
| 代码补全不生效 | 确认 IDE 的 Python 解释器指向包含 xtquant 的环境 |

---

## 10. 费用汇总

| 项目 | 费用 |
|------|------|
| 证券开户 | 免费 |
| QMT 客户端 | 免费 |
| miniQMT | 免费 |
| 股票佣金 | 万1 ~ 万1.5（需协商） |
| 印花税 | 千1（卖出，国家税收） |
| 过户费 | 十万分之1.5 |
| Redis 云服务器 | ~50 元/月（2C4G） |
| 域名（可选） | ~50 元/年 |

---

## 11. 关键路径速查

```
开户（1天）
  → 入金10万+（即时）
  → 申请QMT权限（1-3个工作日）
  → 程序化交易报备（即时）
  → 下载安装QMT客户端（30分钟）
  → 下载Python库（20分钟）
  → 配置本地Python环境（30分钟）
  → 数据补充（1-2小时）
  → 连接测试（10分钟）
  → 模拟盘测试（5个交易日）
  → 实盘切换
```

**总计：开户到模拟盘约 1 周，模拟盘验证 1 周，约 2 周可开始实盘。**
