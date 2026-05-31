"""
聚宽测试策略 - 用于验证 JQ 信号与中间件 Redis 消息的对应关系

使用方法：
1. 将 jq_signal_sender.py 的全部内容复制到聚宽策略编辑器顶部
2. 将本脚本内容复制到 jq_signal_sender.py 下方
3. 修改 middleware_url 和 api_key 为你的中间件地址和密钥
4. 设置回测区间（建议 2024-01-01 至 2024-02-01，足够覆盖所有调仓日）
5. 运行回测，查看日志中的 [JQ-TEST] 标记
6. 回测结束后，访问中间件监控面板核对信号数量
   http://<your-server>:8080/?apiKey=<your-api-key>

注意：本脚本使用 mode=0（回测时也发信号），测试完成后请改回 mode=1
"""

from jq_signal_sender import signal_order_target_value, init_signal_sender

# ==================== 用户配置区 ====================
MIDDLEWARE_URL = 'http://YOUR_SERVER_IP:8080'   # 替换为你的中间件地址
API_KEY = 'YOUR_API_KEY'                          # 替换为你的 API Key
STRATEGY_NAME = 'test_strategy'
# ===================================================

def initialize(context):
    """策略初始化"""
    # ---- 信号发送器初始化 ----
    g.strategy = STRATEGY_NAME
    g._context_ref = context
    init_signal_sender(
        middleware_url=MIDDLEWARE_URL,
        api_key=API_KEY,
        strategy=g.strategy,
        mode=0  # 0=回测时也发信号（测试用）；测试完成后改回 1
    )

    # ---- 测试交易计划 ----
    # 格式: (调仓日序号, {股票代码: 目标市值})
    # 注意：聚宽回测中股票代码格式为 600519.XSHG / 000001.XSHE
    g.trade_plan = [
        (1, {'600519.XSHG': 50000, '000001.XSHE': 30000}),   # 第1天：买入2只
        (3, {'600519.XSHG': 80000}),                          # 第3天：调整600519加仓
        (5, {'000001.XSHE': 0}),                              # 第5天：卖出000001
        (7, {'600519.XSHG': 0, '000333.XSHE': 40000}),       # 第7天：卖出600519，买入000333
        (9, {'000333.XSHE': 0}),                              # 第9天：卖出000333
    ]

    # ---- 状态跟踪 ----
    g.trade_count = 0              # 当前调仓日计数
    g.total_signals_sent = 0       # 累计发送信号数
    g.expected_total = sum(len(plan) for _, plan in g.trade_plan)  # 预期总信号数

    # ---- 调仓设置 ----
    run_daily(trade, time='9:31')

    # ---- 初始化日志 ----
    log.info('')
    log.info('╔══════════════════════════════════════════════════════════════╗')
    log.info('║          [JQ-TEST] 信号通路测试策略初始化完成                ║')
    log.info('╠══════════════════════════════════════════════════════════════╣')
    log.info('║  中间件地址: %s', MIDDLEWARE_URL)
    log.info('║  策略名称  : %s', STRATEGY_NAME)
    log.info('║  调仓模式  : mode=0（回测时也发信号！测试完请改回1）         ║')
    log.info('║  计划调仓  : %d 次，预期信号数: %d', len(g.trade_plan), g.expected_total)
    log.info('║  交易计划  :')
    for day_num, plan in g.trade_plan:
        log.info('║    第%2d天 : %s', day_num, plan)
    log.info('╚══════════════════════════════════════════════════════════════╝')
    log.info('')


def trade(context):
    """每日调仓"""
    g.trade_count += 1
    current_date = str(context.current_dt.date())

    # 查找今日是否有交易计划
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

    for code, target_value in today_plan.items():
        action_desc = '买入' if target_value > 0 else '卖出'
        action_type = 'BUY' if target_value > 0 else 'SELL'

        # 打印操作前的持仓状态
        has_position = code in context.portfolio.positions
        log.info('[JQ-TEST] ┌────────────────────────────────────────────┐')
        log.info('[JQ-TEST] │ 计划: %s %s', action_desc, code)
        log.info('[JQ-TEST] │ 参数: target_value=%s, 当前持仓=%s', target_value, has_position)
        log.info('[JQ-TEST] └────────────────────────────────────────────┘')

        # 执行下单 + 发送信号
        signal_order_target_value(code, target_value)

        g.total_signals_sent += 1
        log.info('[JQ-TEST] ✓ 信号已发送 → action=%s, code=%s, 累计=%d/%d',
                 action_type, code, g.total_signals_sent, g.expected_total)

    log.info('[JQ-TEST] 第%02d天调仓完成', g.trade_count)
    log.info('')


def after_trading_end(context):
    """收盘后统计"""
    current_date = str(context.current_dt.date())
    current_holdings = list(context.portfolio.positions.keys())

    log.info('[JQ-TEST] -------- 收盘总结 (%s) --------', current_date)
    log.info('[JQ-TEST] 调仓日计数: %d', g.trade_count)
    log.info('[JQ-TEST] 累计信号数: %d / 预期 %d', g.total_signals_sent, g.expected_total)
    log.info('[JQ-TEST] 当前持仓  : %s', current_holdings)

    # 判断是否是最后一个调仓日
    last_trade_day = max(day for day, _ in g.trade_plan)
    if g.trade_count >= last_trade_day:
        log.info('')
        log.info('╔══════════════════════════════════════════════════════════════╗')
        log.info('║              [JQ-TEST] 全部调仓计划已执行完毕                ║')
        log.info('╠══════════════════════════════════════════════════════════════╣')
        log.info('║  聚宽端发送信号总数: %d', g.total_signals_sent)
        log.info('║  预期发送信号总数  : %d', g.expected_total)
        log.info('║  核对结果          : %s', '✓ 一致' if g.total_signals_sent == g.expected_total else '✗ 不一致！')
        log.info('╠══════════════════════════════════════════════════════════════╣')
        log.info('║  下一步：                                                      ║')
        log.info('║  1. 打开监控面板: %s/?apiKey=...', MIDDLEWARE_URL)
        log.info('║  2. 选择 Strategy: %s', STRATEGY_NAME)
        log.info('║  3. 对比 Redis 中收到的信号数量是否与上述数字一致              ║')
        log.info('║  4. 逐条核对每只股票、每个操作的 action/code/pct 是否匹配      ║')
        log.info('╚══════════════════════════════════════════════════════════════╝')
        log.info('')
