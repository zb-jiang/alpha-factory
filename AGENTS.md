1. 本地工程是一个通过LLM挖掘量化因子的工程，有完整的因子分析，因子回测流水线
2. ricequant中的脚本是在ricequant平台Jupiter Notbook中运行的脚本。用来验证本地的因子分析结果和米框因子分析结果一致（用来保证我本地基于tushare数据源做的因子分析结果的质量）
3. 进行本地因子分析结果和ricequant因子分析结果比对的时候，如果结果比对遇到不一致的情况不要凭空猜测，而是需要借助于本地的parquest过程数据和ricequant的过程数据做横截面比对找到差距的根因并判断是否应该应该拉齐差距。你可以给我生成可直接粘贴到 RQ Notebook 单个 cell 运行的完整脚本。在本地需要运行的脚本你直接生成并直接运行，不用等我
4. 现在已知的ricequant和tushare之间的底层数据的情况。如果本地因子分析结果和ricequant因子分析结果比对不一致经过分析是下面原因造成的，可以不必深究了（对于因为成分股不同而造成的比对结果不同，需要明确找到成分股不同的证据）
   - tushare和ricequant的原始数据有一个口径不同是已知的，tushare是“千元”+“手”， ricequant是“元”+“股”
   - tushare在某些日期取不到指数成分股，本地代码是通过回溯的方式获取成分股的，所以会出现某些横截面成分股不同的情况， 但这个需要找到明确的成分股不同的证据
   - ricequant平台不支持turnover，assets_yoy，debt_to_assets，dt_netprofit_yoy，equity_yoy，q_ocf_to_sales字段的获取
   - dv_ttm特征值在ricequant和tushare之间存在已知口径差异：大多数股票可以对上，但少数股票（已发现主要集中在部分科创板股票和个别银行股）存在单位或口径不一致，因此dv_ttm不适合作为严格校验本地与ricequant因子分析链路是否一致的标准字段
   - netprofit_yoy、or_yoy字段在ricequant和tushare之间存在已知口径差异：本地使用tushare fina_indicator中的netprofit_yoy、or_yoy，同比口径按公告日做as-of对齐；ricequant侧当前只能稳定映射到net_profit_growth_ratio_ttm、operating_revenue_growth_ratio_ttm这类TTM增速字段。两边大方向通常相关，但不是同一口径，因此netprofit_yoy、or_yoy以及依赖它们的组合因子不适合作为严格校验本地与ricequant因子分析链路是否一致的标准字段
   - 对于包含 q_roe_acceleration 特征的因子在ricequant上的验证结果会与本地有偏差，因为ricequant没有 q_roe 字段，只能通过 return_on_equity_weighted_average_mrq_0 字段近似计算
   - 已经验证验证了多次，ricequant和tushare的每日OCHLV数据是严格能对上的
5. 该项目的缓存机制是SQLLite数据库。按照业务逻辑缓存的使用方式是：
   - 先从缓存读取数据
   - 如果缓存命中则直接读取
   - 如果缓存没有命中则去tushare读取，并将结果保存在缓存中
   - 下次读取相同数据就会在缓存中命中
6. joinquant目录中的脚本是通过 step14\_joinquant\_export.py 依据本地因子+回测配置信息生成的，用来在joinquant平台验证因子回测结果是否和本地结果一致。所以运行step14\_joinquant\_export.py之前肯定是运行过step11 OOS本地回测的。这是我评估本地回测代码逻辑是否正确的重要工具。
7. 本地回测结果和joinquant平台回测结果的对比的时候，如果结果比对遇到不一致的情况不要凭空猜测，而是需要借助于本地的parquest过程数据和joinquant日志信息做逐一调仓日比对找到差距，并用"5 why"的方式找到根因并判断是否应该拉齐差距（你可以给我生成joinquant的脚本，让我跑脚本生成日志粘贴给你）。通过每个调仓日的交易数据，找到差距的根因并判断是否应该拉齐差距。
8. 具体思路：先确认本地和joinquant平台的调仓日（信号日）是否一致，再确定信号日选出来的买进和卖出股票是否一致， 再确认每个交易日的交易股票是否一致（需要考虑停牌，涨停，跌停的情况）， 再确认每个交易日的交易股票的交易量是否一致（需要考虑停牌，涨停，跌停的情况），最后确认每个交易日的交易股票的交易价格是否一致（需要考虑停牌，涨停，跌停的情况）。
9. 已经明确的设计思路：
   - 参考topk-dropout-strategy-design.md，soft-topk-strategy-design.md和market-timing-strategy-design.md
   - 在调仓日进行相关交易参数的计算（例如因子分数，成分股权重等），真正交易日期是在调仓日的下一个交易日，用open价格进行交易
   - 例如：调仓日是2023-01-01，真正交易日期是2023-01-02
   - 信号日之间独立考虑，不要关联。A 信号日计划卖 5 只，只能卖 2 只就只卖 2 只，剩下的 3 只就算了，下一个信号日也不用考虑这 3 只股票
   - 跌停：不做卖出交易。后续交易日：**每日重试卖出**，直到卖出成功或到达下一个信号日。下一个信号日：独立重新决策，不再延续之前的卖出意图
10. 根据经验，可能造成本地和joinquant平台回测结果不一致的原因有：
   - 因为基础数据的ready程度不同而引起因子分数差异，导致某些股票在本地参与排名/交易而在joinquant平台不参与排名/交易，或者在本地不参与排名/交易而在joinquant平台参与排名/交易，如果是这个原因需要找到详细的数据证据
   - 因为成分股权重数据的ready程度不同而引起本地和joinquant平台的交易股票的交易量差异，如果是这个原因需要找到详细的数据证据
   - 本地交易是按照“手”（100股/200股）向下取整进行交易的，而joinquant平台order_target_value可能不会向下取整，导致交易量差异（因为是聚宽内部的机制，所以不确定，但是从交易日志中看到的现象）
11. 当你发现原因是joinquant平台的交易行为和本地不一致时，不要停止，需要查看交易行为为什么和本地不同？joinquant的交易脚本也是step14生成的呀，所以需要看看是不是step14的代码有问题
12. backend 和 webapp两个目录下的代码是通过web UI的方式实现多用户和多租户的因子挖掘流水线。每个用户可以启动多个流水线实例（step10-step14），实例间互不干涉。每个流水线实例的代码逻辑都follow runtime/src中的逻辑。通过staging(staging目录在当前工程的staging下)来进行实例隔离。所有的实例都有自己的配置项copy(符合runtime/config中的内容和格式，注意backend 和 webapp中配置的single truth of data是数据库，不是runtime/config中的YAML)。
