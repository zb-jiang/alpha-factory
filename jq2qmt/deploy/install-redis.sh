#!/bin/bash
set -e

REDIS_VERSION="7.2.4"
INSTALL_DIR="$HOME/jq2qmt"
REDIS_DIR="$HOME/local/redis"
JQ2QMT_REDIS_DB=1

echo "=========================================="
echo " JQ2QMT Middleware - Redis Setup"
echo "=========================================="

detect_existing_redis() {
    if command -v redis-cli &> /dev/null; then
        if redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q PONG; then
            echo "noauth"
            return 0
        fi
    fi

    if command -v redis-cli &> /dev/null; then
        local test_pass
        if [ -f ${INSTALL_DIR}/redis-password.txt ]; then
            test_pass=$(cat ${INSTALL_DIR}/redis-password.txt)
        elif [ -f /etc/redis/password.txt ]; then
            test_pass=$(cat /etc/redis/password.txt)
        fi
        if [ -n "$test_pass" ] && redis-cli -h 127.0.0.1 -p 6379 -a "$test_pass" ping 2>/dev/null | grep -q PONG; then
            echo "auth:$test_pass"
            return 0
        fi
    fi

    if ss -tlnp 2>/dev/null | grep -q ':6379' || netstat -tlnp 2>/dev/null | grep -q ':6379'; then
        echo "port_busy"
        return 0
    fi

    echo "none"
}

echo "[1/2] Detecting existing Redis..."
mkdir -p ${INSTALL_DIR}
REDIS_STATUS=$(detect_existing_redis)

case "$REDIS_STATUS" in
    noauth)
        echo "[INFO] Redis detected on port 6379 (no password)."
        echo "[INFO] jq2qmt will use the existing Redis instance with database=${JQ2QMT_REDIS_DB}."
        echo ""
        echo "  Existing Redis:  127.0.0.1:6379 db=0 (other service)"
        echo "  jq2qmt Redis:    127.0.0.1:6379 db=${JQ2QMT_REDIS_DB} (isolated)"
        echo ""
        echo "${JQ2QMT_REDIS_DB}" > ${INSTALL_DIR}/redis-db.txt
        echo "" > ${INSTALL_DIR}/redis-password.txt

        echo ""
        echo "=========================================="
        echo " Redis setup complete (reuse existing)!"
        echo "=========================================="
        echo " Redis Host:     127.0.0.1"
        echo " Redis Port:     6379"
        echo " Redis Database: ${JQ2QMT_REDIS_DB}"
        echo " Redis Password: (none)"
        echo "=========================================="
        exit 0
        ;;

    auth:*)
        REDIS_PASSWORD="${REDIS_STATUS#auth:}"
        echo "[INFO] Redis detected on port 6379 (with password)."
        echo "[INFO] jq2qmt will use the existing Redis instance with database=${JQ2QMT_REDIS_DB}."
        echo ""
        echo "  Existing Redis:  127.0.0.1:6379 db=0 (other service)"
        echo "  jq2qmt Redis:    127.0.0.1:6379 db=${JQ2QMT_REDIS_DB} (isolated)"
        echo ""
        echo "${JQ2QMT_REDIS_DB}" > ${INSTALL_DIR}/redis-db.txt
        echo "${REDIS_PASSWORD}" > ${INSTALL_DIR}/redis-password.txt
        chmod 600 ${INSTALL_DIR}/redis-password.txt

        echo ""
        echo "=========================================="
        echo " Redis setup complete (reuse existing)!"
        echo "=========================================="
        echo " Redis Host:     127.0.0.1"
        echo " Redis Port:     6379"
        echo " Redis Database: ${JQ2QMT_REDIS_DB}"
        echo " Redis Password: ${REDIS_PASSWORD}"
        echo " Password saved: ${INSTALL_DIR}/redis-password.txt"
        echo "=========================================="
        exit 0
        ;;

    port_busy)
        echo "[WARN] Port 6379 is in use but redis-cli cannot connect."
        echo "[INFO] The service on port 6379 may not be Redis, or requires a password not found."
        echo "[INFO] Will install a new Redis instance on port 6380."
        INSTALL_PORT=6380
        ;;

    none)
        echo "[INFO] No Redis detected. Will install Redis on port 6379."
        INSTALL_PORT=6379
        ;;
esac

echo ""
echo "[2/2] Installing Redis ${REDIS_VERSION} on port ${INSTALL_PORT}..."

if command -v redis-server &> /dev/null && [ "$INSTALL_PORT" = "6379" ]; then
    echo "[INFO] redis-server already in PATH, but not running. Starting existing installation..."
    sudo systemctl start redis 2>/dev/null || sudo systemctl start redis-server 2>/dev/null || true
    if redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q PONG; then
        echo "[INFO] Redis started successfully."
        exit 0
    fi
fi

echo "[2a/6] Installing build dependencies..."
sudo apt-get update
sudo apt-get install -y build-essential tcl wget pkg-config libssl-dev

echo "[2b/6] Downloading Redis ${REDIS_VERSION}..."
cd /tmp
if [ ! -f "redis-${REDIS_VERSION}.tar.gz" ]; then
    wget https://github.com/redis/redis/archive/refs/tags/${REDIS_VERSION}.tar.gz -O redis-${REDIS_VERSION}.tar.gz
fi

echo "[2c/6] Compiling Redis..."
tar -xzf redis-${REDIS_VERSION}.tar.gz
cd redis-${REDIS_VERSION}
make -j$(nproc)
make test || true

echo "[2d/6] Installing Redis to ${REDIS_DIR}..."
mkdir -p ${REDIS_DIR}
cp src/redis-server ${REDIS_DIR}/
cp src/redis-cli ${REDIS_DIR}/
sudo ln -sf ${REDIS_DIR}/redis-server /usr/local/bin/redis-server 2>/dev/null || true
sudo ln -sf ${REDIS_DIR}/redis-cli /usr/local/bin/redis-cli 2>/dev/null || true

echo "[2e/6] Configuring Redis on port ${INSTALL_PORT}..."
mkdir -p ${INSTALL_DIR}/redis-data
mkdir -p ${INSTALL_DIR}/logs

REDIS_PASSWORD=$(openssl rand -hex 16)
echo "[INFO] Generated Redis password: ${REDIS_PASSWORD}"
echo "${REDIS_PASSWORD}" > ${INSTALL_DIR}/redis-password.txt
chmod 600 ${INSTALL_DIR}/redis-password.txt

if [ "$INSTALL_PORT" = "6379" ]; then
    CONF_FILE="${INSTALL_DIR}/redis.conf"
else
    CONF_FILE="${INSTALL_DIR}/redis-jq2qmt.conf"
fi

tee ${CONF_FILE} > /dev/null <<EOF
bind 0.0.0.0
port ${INSTALL_PORT}
requirepass ${REDIS_PASSWORD}
daemonize yes
pidfile ${INSTALL_DIR}/redis.pid
logfile ${INSTALL_DIR}/logs/redis.log
dir ${INSTALL_DIR}/redis-data
dbfilename redis-dump.rdb

save 900 1
save 300 10
save 60 10000

maxmemory 512mb
maxmemory-policy allkeys-lru

appendonly yes
appendfilename "redis-appendonly.aof"
appendfsync everysec

timeout 300
tcp-keepalive 60
loglevel notice
databases 16
EOF

echo "[2f/6] Starting Redis..."
${REDIS_DIR}/redis-server ${CONF_FILE}

sleep 2
if ${REDIS_DIR}/redis-cli -p ${INSTALL_PORT} -a "${REDIS_PASSWORD}" ping 2>/dev/null | grep -q PONG; then
    echo "${JQ2QMT_REDIS_DB}" > ${INSTALL_DIR}/redis-db.txt
    echo ""
    echo "=========================================="
    echo " Redis installed and running!"
    echo "=========================================="
    echo " Redis Host:     127.0.0.1"
    echo " Redis Port:     ${INSTALL_PORT}"
    echo " Redis Database: ${JQ2QMT_REDIS_DB}"
    echo " Redis Password: ${REDIS_PASSWORD}"
    echo " Password saved: ${INSTALL_DIR}/redis-password.txt"
    echo " Config file:    ${CONF_FILE}"
    echo " Log file:       ${INSTALL_DIR}/logs/redis.log"
    echo " Data dir:       ${INSTALL_DIR}/redis-data"
    echo ""
    echo " Stop:   ${REDIS_DIR}/redis-cli -p ${INSTALL_PORT} -a \"\$(cat ${INSTALL_DIR}/redis-password.txt)\" shutdown"
    echo " Start:  ${REDIS_DIR}/redis-server ${CONF_FILE}"
    echo "=========================================="
else
    echo "[ERROR] Redis failed to start. Check: tail -f ${INSTALL_DIR}/logs/redis.log"
    exit 1
fi
