# Redis 信号数据清理指南

在重新测试之前，建议先清理 Redis 中的历史信号数据，避免旧数据干扰结果核对。

## 快速清理（推荐）

只清除 `test_strategy` 策略的信号，不影响其他策略或中间件配置：

```bash
# 连接到 Redis（如果设置了密码，加上 -a 参数）
redis-cli -p 6379

# 选择 db1（中间件默认使用的数据库）
SELECT 1

# 删除 test_strategy 的信号 Stream、结果 Stream 和死信 Stream
DEL factor_factory:test_strategy
DEL factor_factory:test_strategy:result
DEL factor_factory:test_strategy:dead

# 验证删除成功
EXISTS factor_factory:test_strategy
EXISTS factor_factory:test_strategy:result
EXISTS factor_factory:test_strategy:dead
# 都应返回 0
```

## 彻底清空 db1（谨慎操作）

如果中间件只用于本策略，可以清空整个 db1：

```bash
redis-cli -p 6379

SELECT 1

# 查看当前有多少条 Stream
DBSIZE

# 清空整个数据库
FLUSHDB

# 验证
DBSIZE
# 应返回 0
```

## 带密码的 Redis 连接

如果 Redis 设置了密码（部署时生成的 `redis-password.txt`）：

```bash
# 方式 1：命令行带密码
redis-cli -p 6379 -a "$(cat ~/jq2qmt/redis-password.txt)"

# 方式 2：先连接再认证
redis-cli -p 6379
AUTH $(cat ~/jq2qmt/redis-password.txt)
```

## 测试前确认清单

1. **删除历史 Stream**
   ```bash
   redis-cli -p 6379 -a "YOUR_PASSWORD" DEL factor_factory:test_strategy factor_factory:test_strategy:result factor_factory:test_strategy:dead
   ```

2. **确认监控面板无数据**
   - 打开 `http://<your-server>:8080/?apiKey=<your-api-key>`
   - 选择 Strategy: `test_strategy`
   - 确认 Signals 列表为空

3. **重新粘贴最新代码到聚宽策略编辑器**
   - 将更新后的 `jq_signal_sender.py` 粘贴到策略编辑器顶部
   - 将 `test_strategy_jq.py` 粘贴到下方
   - **注意**：聚宽策略编辑器不会自动同步本地文件修改，每次修改后都需要手动重新粘贴

4. **运行回测并核对**
   - 聚宽端查看 `[JqSignalSender]` 开头的 print 日志
   - 回测结束后对比中间件收到的信号数

## 常见问题

| 问题 | 解决方法 |
|------|---------|
| 删除后监控面板仍显示旧数据 | 刷新浏览器页面（可能是前端缓存） |
| `NOAUTH Authentication required` | 使用 `AUTH 密码` 或 `redis-cli -a 密码` |
| `ERR wrong number of arguments` | 检查 Redis 版本是否 ≥ 5.0（Stream 需要 5.0+） |
| 误删了其他策略的数据 | 重新运行对应策略生成信号即可 |

## 相关文件

- 策略发送端：`jq_signal_sender.py`（粘贴到聚宽策略编辑器顶部）
- 测试策略：`test_strategy_jq.py`（粘贴到 jq_signal_sender.py 下方）
- QMT 信号消费端：`qmt_signal_worker.py`（在本地 QMT 环境运行）
