"""
聚宽测试策略 - 用于验证 JQ 信号与中间件 Redis 消息的对应关系

使用方法（请严格按以下步骤操作）：

步骤 1：安装依赖
    在聚宽研究环境新建 cell 运行：
        !pip install requests

步骤 2：上传 Stub 文件
    将 jq_signal_sender.py 的内容复制到聚宽策略编辑器顶部

步骤 3：将本脚本内容复制到 jq_signal_sender.py 下方

步骤 4：修改配置
    修改下方 MIDDLEWARE_URL 和 API_KEY 为你的中间件地址和密钥

步骤 5：设置回测区间
    建议设置为 2024-01-01 至 2024-02-01，初始资金 100万，足够覆盖所有调仓日

步骤 6：运行回测
    查看日志中的 [JQ-TEST] 标记，确认信号发送情况

步骤 7：核对信号
    回测结束后，访问中间件监控面板核对信号数量
    http://<your-server>:8080/?apiKey=<your-api-key>

注意：本脚本使用 mode=0（回测时也发信号），测试完成后请改回 mode=1
"""

# ==================== 用户配置区 ====================
MIDDLEWARE_URL = 'http://YOUR_SERVER_IP:8080'   # 替换为你的中间件地址
API_KEY = 'YOUR_API_KEY'                          # 替换为你的 API Key
STRATEGY_NAME = 'test_strategy'
# ===================================================

def _round_to_lot(code, shares):
    """按手数向下取整（A股100股，科创板200股）"""
    lot_size = 200 if code.startswith('688') else 100
    return (shares // lot_size) * lot_size


def initialize(context):
    """策略初始化"""
    g.strategy = STRATEGY_NAME
    g._context_ref = context
    init_signal_sender(
        middleware_url=MIDDLEWARE_URL,
        api_key=API_KEY,
        strategy=g.strategy,
        mode=0
    )

    # ---- 测试交易计划 ----
    # 格式: (调仓日序号, {股票代码: 目标市值})
    # 正数=买入该市值，0=卖出全部持仓
    # 使用 order() 模拟 step14 的下单方式
    g.trade_plan = [
        (1, {'000001.XSHE': 50000, '601318.XSHG': 80000}),    # 第1天：买入2只
        (3, {'601318.XSHG': 150000}),                           # 第3天：加仓中国平安
        (5, {'000001.XSHE': 0}),                                # 第5天：卖出平安银行
        (7, {'601318.XSHG': 0, '000333.XSHE': 100000}),        # 第7天：卖出中国平安，买入美的
        (9, {'000333.XSHE': 0}),                                # 第9天：卖出美的
    ]

    g.trade_count = 0
    g.total_signals_sent = 0

    run_daily(trade, time='9:31')

    log.info('')
    log.info('╔══════════════════════════════════════════════════════════════╗')
    log.info('║          [JQ-TEST] 信号通路测试策略初始化完成                ║')
    log.info('╠══════════════════════════════════════════════════════════════╣')
    log.info('║  中间件地址: %s', MIDDLEWARE_URL)
    log.info('║  策略名称  : %s', STRATEGY_NAME)
    log.info('║  调仓模式  : mode=0（回测时也发信号！测试完请改回1）         ║')
    log.info('║  下单方式  : signal_order()（与 step14 生成脚本一致）        ║')
    log.info('║  计划调仓  : %d 次', len(g.trade_plan))
    log.info('║  交易计划  :')
    for day_num, plan in g.trade_plan:
        log.info('║    第%2d天 : %s', day_num, plan)
    log.info('║  原则      : 聚宽实际成交才发信号，未成交不发               ║')
    log.info('╚══════════════════════════════════════════════════════════════╝')
    log.info('')


def trade(context):
    """每日调仓 - 模拟 step14 的 order() 下单方式"""
    g.trade_count += 1
    current_date = str(context.current_dt.date())

    today_plan = None
    for day_num, plan in g.trade_plan:
        if g.trade_count == day_num:
            today_plan = plan
            break

    if today_plan is None:
        log.info('[JQ-TEST] 第%02d天(%s) -------- 无交易计划', g.trade_count, current_date)
        return

    log.info('')
    log.info('[JQ-TEST] ╔══════════════════════════════════════════════════════════════╗')
    log.info('[JQ-TEST] ║ 第%02d天(%s) 开始调仓                                          ║', g.trade_count, current_date)
    log.info('[JQ-TEST] ╚══════════════════════════════════════════════════════════════╝')

    current_data = get_current_data()

    for code, target_value in today_plan.items():
        if target_value == 0:
            # ---- 卖出：与 step14 一致，order(code, -total_amount) ----
            if code not in context.portfolio.positions:
                log.info('[JQ-TEST] 卖出跳过（无持仓）: %s', code)
                continue
            pos = context.portfolio.positions[code]
            shares_to_sell = pos.total_amount
            if shares_to_sell <= 0:
                log.info('[JQ-TEST] 卖出跳过（持仓为0）: %s', code)
                continue

            log.info('[JQ-TEST] 卖出 %s, shares=%d', code, shares_to_sell)
            my_order = signal_order(code, -shares_to_sell, context=context)

            if my_order is not None:
                g.total_signals_sent += 1
                log.info('[JQ-TEST] ✓ 成交 → SELL %s, 累计=%d', code, g.total_signals_sent)
            else:
                log.info('[JQ-TEST] ✗ 未成交 → SELL %s', code)

        else:
            # ---- 买入：与 step14 一致，先算股数再 order(code, shares) ----
            price = current_data[code].last_price
            if price <= 0:
                log.info('[JQ-TEST] 买入跳过（价格为0）: %s', code)
                continue

            shares = _round_to_lot(code, int(target_value / price))
            if shares <= 0:
                log.info('[JQ-TEST] 买入跳过（股数不足1手）: %s, target_value=%.0f, price=%.2f', code, target_value, price)
                continue

            log.info('[JQ-TEST] 买入 %s, shares=%d, target_value=%.0f', code, shares, target_value)
            my_order = signal_order(code, shares, context=context)

            if my_order is not None:
                g.total_signals_sent += 1
                log.info('[JQ-TEST] ✓ 成交 → BUY %s, 累计=%d', code, g.total_signals_sent)
            else:
                log.info('[JQ-TEST] ✗ 未成交 → BUY %s', code)

    log.info('[JQ-TEST] 第%02d天调仓完成', g.trade_count)
    log.info('')


def after_trading_end(context):
    """收盘后统计"""
    current_date = str(context.current_dt.date())
    current_holdings = list(context.portfolio.positions.keys())

    log.info('[JQ-TEST] -------- 收盘总结 (%s) --------', current_date)
    log.info('[JQ-TEST] 调仓日计数: %d', g.trade_count)
    log.info('[JQ-TEST] 累计信号数: %d', g.total_signals_sent)
    log.info('[JQ-TEST] 当前持仓  : %s', current_holdings)

    last_trade_day = max(day for day, _ in g.trade_plan)
    if g.trade_count >= last_trade_day:
        log.info('')
        log.info('╔══════════════════════════════════════════════════════════════╗')
        log.info('║              [JQ-TEST] 全部调仓计划已执行完毕                ║')
        log.info('╠══════════════════════════════════════════════════════════════╣')
        log.info('║  聚宽端实际成交并发信号总数: %d', g.total_signals_sent)
        log.info('║  （仅统计实际成交的订单，未成交的不发信号）                  ║')
        log.info('╠══════════════════════════════════════════════════════════════╣')
        log.info('║  下一步：                                                      ║')
        log.info('║  1. 打开监控面板: %s/?apiKey=...', MIDDLEWARE_URL)
        log.info('║  2. 选择 Strategy: %s', STRATEGY_NAME)
        log.info('║  3. 对比 Redis 中收到的信号数量是否与上述数字一致              ║')
        log.info('║  4. 逐条核对每只股票、每个操作的 action/code/pct 是否匹配      ║')
        log.info('╚══════════════════════════════════════════════════════════════╝')
        log.info('')
