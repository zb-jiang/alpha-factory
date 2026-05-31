# JQ2QMT 信号中间件 - Ubuntu Linux 部署指南

## 1. 系统要求

| 项目 | 要求 |
|------|------|
| OS | Ubuntu 20.04 / 22.04 / 24.04 LTS |
| Java | JDK 17+ |
| Redis | 7.0+（已有实例可复用） |
| 内存 | ≥ 1GB（Redis 512MB + 中间件 256MB） |
| 磁盘 | ≥ 2GB |
| 网络 | 公网IP，端口 8080 可访问 |

## 2. 架构总览

```
┌─────────────────┐     HTTP      ┌──────────────────────────────┐
│   聚宽策略       │ ──────────→  │   Ubuntu 服务器               │
│   (云端)         │              │   ┌──────────────────────┐   │
│                  │              │   │ Java Middleware       │   │
│  jq_signal_      │              │   │ (Spring Boot 8080)   │   │
│  sender.py       │              │   │        │              │   │
└─────────────────┘              │   │        ▼              │   │
                                 │   │ Redis Stream          │   │
┌─────────────────┐     HTTP      │   │ (6379)               │   │
│   本地电脑       │ ←────────── │   └──────────────────────┘   │
│   (miniQMT)     │              └──────────────────────────────┘
│                  │
│  qmt_signal_     │  HTTP轮询 / Redis直连
│  worker.py       │
└─────────────────┘
```

## 3. 安装步骤

### 3.1 Ubuntu 服务器安装 JDK 17

```bash
sudo apt-get update
sudo apt-get install -y openjdk-17-jdk

java -version
# openjdk version "17.x.x"
```

### 3.2 Windows 开发机编译 JAR

```bash
cd d:\test\factor-factory\jq2qmt
mvn clean package -DskipTests
```

编译产物：`target/jq2qmt-middleware.jar`

### 3.3 上传文件到 Ubuntu 服务器

在 Windows 开发机上执行 scp，将 JAR 和部署脚本上传到服务器的 `~/jq2qmt/` 目录：

```bash
# 替换 user 和 your-server 为实际值
scp d:\test\factor-factory\jq2qmt\target\jq2qmt-middleware.jar user@your-server:~/jq2qmt/
scp d:\test\factor-factory\jq2qmt\deploy\install-redis.sh user@your-server:~/jq2qmt/
scp d:\test\factor-factory\jq2qmt\deploy\install-middleware.sh user@your-server:~/jq2qmt/
```

上传后服务器 `~/jq2qmt/` 目录内容：

```
~/jq2qmt/
├── jq2qmt-middleware.jar
├── install-redis.sh
└── install-middleware.sh
```

### 3.4 Ubuntu 服务器安装 Redis

ssh 登录服务器后执行：

```bash
cd ~/jq2qmt
bash install-redis.sh
```

脚本会自动检测已有 Redis 实例，并智能选择部署方式：

| 场景 | 脚本行为 |
|------|---------|
| 6379 端口已有 Redis（无密码） | 复用现有实例，使用 db=1 隔离，跳过安装 |
| 6379 端口已有 Redis（有密码） | 复用现有实例，使用 db=1 隔离，跳过安装 |
| 6379 端口被占用但非 Redis | 新建 Redis 实例在 6380 端口 |
| 无 Redis | 安装 Redis 7.2.4 到 6379 端口 |

**重要：复用已有 Redis 时，jq2qmt 使用 `database=1` 与现有服务的 `database=0` 完全隔离**，无需额外开放端口。

脚本执行成功后会在 `~/jq2qmt/` 下生成：
- `redis-password.txt` — Redis 密码
- `redis-db.txt` — 使用的数据库编号（默认 1）

手动安装（如果脚本不适用）：

```bash
sudo apt-get install -y redis-server

# 编辑配置
sudo vi /etc/redis/redis.conf
# 修改以下配置：
# bind 0.0.0.0
# requirepass your-strong-password
# daemonize yes
# maxmemory 512mb
# appendonly yes

sudo systemctl restart redis-server
```

如果已有 Redis 实例在 6379 端口，只需在后续生成的 application.yml 中确认 `database: 1`：

```yaml
spring:
  data:
    redis:
      host: 127.0.0.1
      port: 6379
      password: "your-existing-password"
      database: 1    # 使用 db=1 与现有服务(db=0)隔离
```

### 3.5 Ubuntu 服务器安装中间件

```bash
cd ~/jq2qmt
bash install-middleware.sh
```

安装脚本会自动：
- 读取 `redis-password.txt` 和 `redis-db.txt` 获取 Redis 连接信息
- 生成 `application.yml`（自动填入 Redis 密码、database 编号、API Key）
- 生成 `api-key.txt` 保存 API Key

安装完成后 `~/jq2qmt/` 目录内容：

```
~/jq2qmt/
├── jq2qmt-middleware.jar
├── application.yml          ← 自动生成的配置文件
├── api-key.txt              ← API Key
├── redis-password.txt       ← Redis 密码
├── redis-db.txt             ← Redis 数据库编号
├── logs/                    ← 日志目录
├── install-redis.sh
└── install-middleware.sh
```

### 3.6 启动中间件

```bash
cd ~/jq2qmt
java -jar jq2qmt-middleware.jar --spring.config.location=file:~/jq2qmt/application.yml
```

日志直接输出到控制台，同时写入 `~/jq2qmt/logs/jq2qmt.log`。

停止服务：`Ctrl+C`

后台运行（可选）：

```bash
nohup java -jar jq2qmt-middleware.jar \
    --spring.config.location=file:~/jq2qmt/application.yml \
    > ~/jq2qmt/logs/console.log 2>&1 &

# 查看进程
ps aux | grep jq2qmt

# 停止
pkill -f jq2qmt-middleware
```

### 3.7 验证部署

```bash
API_KEY=$(cat ~/jq2qmt/api-key.txt)

# 健康检查
curl http://localhost:8080/api/v1/health -H "X-API-Key: ${API_KEY}"

# 预期返回
# {"code":200,"message":"OK","data":{"status":"UP","redis":"connected",...}}

# 发送测试信号
curl -X POST http://localhost:8080/api/v1/signals/send \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${API_KEY}" \
  -d '{
    "action": "BUY",
    "code": "600519.SH",
    "pct": 0.05,
    "price": 1850.50,
    "strategy": "test_strategy"
  }'

# 查看监控面板
# 浏览器访问: http://your-server:8080/?apiKey=${API_KEY}
```

## 4. 聚宽端配置

### 4.1 安装依赖

在聚宽研究环境新建 cell 运行：

```python
!pip install requests
```

### 4.2 上传 Stub 文件

将 `stubs/jq_signal_sender.py` 的内容复制到聚宽策略编辑器顶部。

### 4.3 策略改造示例

```python
# ===== 在策略顶部添加 =====
from jq_signal_sender import JqSignalSender, signal_order_target_value, init_signal_sender

# ===== 在 initialize() 中初始化 =====
def initialize(context):
    g.strategy = 'factor_alpha_001'
    g._context_ref = context
    init_signal_sender(
        middleware_url='http://YOUR_SERVER_IP:8080',
        api_key='YOUR_API_KEY',
        strategy=g.strategy,
        mode=1  # 1=仅在模拟交易/实盘时发信号（回测时不发，避免误发历史信号）
                # 0=始终发信号（本地调试用，回测时也会发）
    )
    # ... 原有初始化代码 ...

# ===== 替换所有 order_target_value =====
# 原代码: order_target_value(code, 0)
# 改为:   signal_order_target_value(code, 0)

# 原代码: order_target_value(code, target_value)
# 改为:   signal_order_target_value(code, target_value)

# 原代码: order_target_value(code, target_value, style=LimitOrderStyle(p))
# 改为:   signal_order_target_value(code, target_value, style=LimitOrderStyle(p))
```

## 5. QMT 端配置

### 5.1 安装依赖

```bash
pip install requests redis
```

### 5.2 HTTP 模式运行（推荐）

```bash
python qmt_signal_worker.py \
    --middleware-url http://YOUR_SERVER_IP:8080 \
    --api-key YOUR_API_KEY \
    --strategy factor_alpha_001 \
    --qmt-path "C:/国金QMT/userdata_mini" \
    --account-id YOUR_ACCOUNT_ID \
    --poll-interval 2 \
    --mode http
```

### 5.3 Redis 直连模式运行（更低延迟）

```bash
python qmt_signal_worker.py \
    --middleware-url http://YOUR_SERVER_IP:8080 \
    --api-key YOUR_API_KEY \
    --strategy factor_alpha_001 \
    --qmt-path "C:/国金QMT/userdata_mini" \
    --account-id YOUR_ACCOUNT_ID \
    --mode redis \
    --redis-host YOUR_SERVER_IP \
    --redis-port 6379 \
    --redis-password YOUR_REDIS_PASSWORD \
    --redis-db 1
```

### 5.4 Dry Run 模式（测试用）

```bash
python qmt_signal_worker.py \
    --middleware-url http://YOUR_SERVER_IP:8080 \
    --api-key YOUR_API_KEY \
    --strategy factor_alpha_001 \
    --dry-run
```

## 6. API 接口参考

### 6.1 信号发送

```
POST /api/v1/signals/send
Body: {
    "action": "BUY|SELL|ADJUST",
    "code": "600519.SH",
    "pct": 0.05,
    "price": 1850.50,
    "strategy": "factor_alpha_001",
    "signalId": "uuid",        // 可选，自动生成
    "signalTime": "2026-05-30 09:35:00"  // 可选，自动填充
}
```

### 6.2 批量信号发送

```
POST /api/v1/signals/batch
Body: [ {signal1}, {signal2}, ... ]
```

### 6.3 信号消费（HTTP 模式）

```
GET /api/v1/signals/consume?strategy=xxx&consumer=worker-1&count=10
```

### 6.4 信号消费（阻塞模式）

```
GET /api/v1/signals/consume/blocking?strategy=xxx&consumer=worker-1&count=1&timeoutSeconds=5
```

### 6.5 信号确认

```
POST /api/v1/signals/ack?strategy=xxx&recordId=xxx
```

### 6.6 执行结果上报

```
POST /api/v1/results/report
Body: {
    "signalId": "uuid",
    "status": "FILLED|PARTIAL|REJECTED|SKIPPED|ERROR",
    "orderId": 12345,
    "filledPrice": 1852.00,
    "filledVolume": 100,
    "filledAmount": 185200.00,
    "executeTime": "2026-05-30 09:35:02",
    "remark": "",
    "strategy": "factor_alpha_001"
}
```

### 6.7 监控接口

```
GET /api/v1/monitor/dashboard          # 仪表盘概览
GET /api/v1/monitor/streams            # 所有 Stream 列表
GET /api/v1/monitor/signals/{strategy} # 信号历史
GET /api/v1/monitor/results/{strategy} # 执行结果历史
GET /api/v1/monitor/pending/{strategy} # 待处理信号
GET /api/v1/health                     # 健康检查
```

## 7. 安全配置

### 7.1 Redis 安全

如果 QMT 端使用 HTTP 模式（推荐），Redis 只需本机访问，可修改 bind 为 127.0.0.1：

```bash
vi ~/jq2qmt/redis.conf
# bind 127.0.0.1
# 然后重启 Redis
~/local/redis/redis-cli -p 6379 -a "$(cat ~/jq2qmt/redis-password.txt)" shutdown
~/local/redis/redis-server ~/jq2qmt/redis.conf
```

如果 QMT 端使用 Redis 直连模式，需要保持 `bind 0.0.0.0`，但务必：
- 设置强密码
- 使用防火墙限制 6379 端口访问来源

```bash
# 仅允许特定 IP 访问 Redis
sudo ufw allow from YOUR_QMT_IP to any port 6379
```

### 7.2 API Key 安全

```bash
# 查看当前 API Key
cat ~/jq2qmt/api-key.txt

# 修改 API Key
vi ~/jq2qmt/application.yml
# 修改 jq2qmt.auth.api-key 字段
# 然后重启中间件（Ctrl+C 后重新 java -jar）
```

### 7.3 HTTPS 配置（生产环境推荐）

使用 Nginx 反向代理：

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/sites-available/jq2qmt > /dev/null <<'EOF'
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

sudo ln -sf /etc/nginx/sites-available/jq2qmt /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# 申请 SSL 证书
sudo certbot --nginx -d your-domain.com
```

## 8. 运维操作

### 8.1 常用命令

```bash
# 启动中间件
cd ~/jq2qmt && java -jar jq2qmt-middleware.jar --spring.config.location=file:~/jq2qmt/application.yml

# 停止：Ctrl+C（前台）或 pkill -f jq2qmt-middleware（后台）

# 查看日志
tail -f ~/jq2qmt/logs/jq2qmt.log

# Redis 管理
redis-cli -a "$(cat ~/jq2qmt/redis-password.txt)"
> XINFO STREAM factor_factory:test_strategy
> XLEN factor_factory:test_strategy
> XRANGE factor_factory:test_strategy - + COUNT 10
```

### 8.2 监控告警

可使用 Web 监控面板：`http://your-server:8080/?apiKey=YOUR_API_KEY`

或通过 API 定时检查：

```bash
# 健康检查脚本（可加入 crontab）
#!/bin/bash
API_KEY=$(cat ~/jq2qmt/api-key.txt)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    http://localhost:8080/api/v1/health \
    -H "X-API-Key: ${API_KEY}")

if [ "$STATUS" != "200" ]; then
    echo "JQ2QMT middleware is down! HTTP status: $STATUS"
fi
```

### 8.3 数据备份

Redis 数据自动持久化到 `~/jq2qmt/redis-data/`，包含 RDB 快照和 AOF 日志。

```bash
# 手动触发备份
redis-cli -a "$(cat ~/jq2qmt/redis-password.txt)" BGSAVE

# 备份文件
cp ~/jq2qmt/redis-data/redis-dump.rdb ~/jq2qmt/redis-data/redis-dump-$(date +%Y%m%d).rdb
```

## 9. 故障排查

| 问题 | 排查方法 |
|------|---------|
| 中间件无法启动 | 查看控制台输出或 `tail -50 ~/jq2qmt/logs/jq2qmt.log` |
| Redis 连接失败 | `redis-cli -a PASSWORD ping` 测试连接 |
| 聚宽发信号失败 | 检查网络连通性：`curl http://SERVER:8080/api/v1/health` |
| QMT 收不到信号 | 检查 consumer group 是否正确，查看 pending 消息 |
| 信号丢失 | 检查 Redis 持久化配置，查看 dead letter stream |
| 延迟过高 | 检查网络延迟，考虑使用 Redis 直连模式 |

## 10. 完整部署检查清单

- [ ] Ubuntu 服务器 JDK 17 已安装
- [ ] Windows 开发机 JAR 已编译
- [ ] JAR 和脚本已 scp 到服务器 `~/jq2qmt/`
- [ ] `bash install-redis.sh` 执行成功
- [ ] `bash install-middleware.sh` 执行成功，application.yml 已生成
- [ ] `java -jar` 启动中间件，控制台日志正常
- [ ] 健康检查接口返回正常
- [ ] 聚宽端信号发送测试通过
- [ ] QMT 端信号接收测试通过
- [ ] 监控面板可访问
- [ ] API Key 已安全保存
