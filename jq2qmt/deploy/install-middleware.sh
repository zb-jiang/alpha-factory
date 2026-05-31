#!/bin/bash
set -e

INSTALL_DIR="$HOME/jq2qmt"
JAR_NAME="jq2qmt-middleware.jar"

echo "=========================================="
echo " JQ2QMT Middleware - Service Installation"
echo "=========================================="

echo "[1/4] Creating installation directory..."
mkdir -p ${INSTALL_DIR}
mkdir -p ${INSTALL_DIR}/logs

echo "[2/4] Checking application files..."

if [ -f "${INSTALL_DIR}/${JAR_NAME}" ]; then
    echo "[INFO] JAR found: ${INSTALL_DIR}/${JAR_NAME}"
else
    echo "[ERROR] JAR file not found in ${INSTALL_DIR}/. Make sure you uploaded it via scp."
    exit 1
fi

echo "[3/4] Detecting Redis configuration..."

REDIS_HOST="127.0.0.1"
REDIS_PORT=6379
REDIS_PASSWORD=""
REDIS_DATABASE=1

if [ -f ${INSTALL_DIR}/redis-password.txt ]; then
    REDIS_PASSWORD=$(cat ${INSTALL_DIR}/redis-password.txt)
    echo "[INFO] Redis password loaded from ${INSTALL_DIR}/redis-password.txt"
else
    echo "[WARN] Redis password file not found. Using empty password."
fi

if [ -f ${INSTALL_DIR}/redis-db.txt ]; then
    REDIS_DATABASE=$(cat ${INSTALL_DIR}/redis-db.txt)
    echo "[INFO] Redis database loaded from ${INSTALL_DIR}/redis-db.txt: db=${REDIS_DATABASE}"
fi

if [ -f ${INSTALL_DIR}/redis-jq2qmt.conf ]; then
    REDIS_PORT=6380
    echo "[INFO] Detected jq2qmt-dedicated Redis on port 6380"
fi

echo "[INFO] Redis config: host=${REDIS_HOST} port=${REDIS_PORT} database=${REDIS_DATABASE}"

API_KEY=$(openssl rand -hex 16)
echo "[INFO] Generated API Key: ${API_KEY}"

echo "[4/4] Generating application.yml..."
tee ${INSTALL_DIR}/application.yml > /dev/null <<EOF
server:
  port: 8080

spring:
  data:
    redis:
      host: ${REDIS_HOST}
      port: ${REDIS_PORT}
      password: "${REDIS_PASSWORD}"
      database: ${REDIS_DATABASE}
      timeout: 5000ms
      lettuce:
        pool:
          max-active: 20
          max-idle: 10
          min-idle: 5
          max-wait: 3000ms

jq2qmt:
  stream:
    prefix: "factor_factory:"
    max-length: 1000
    consumer-group: "qmt_workers"
    consumer-name: "worker-1"
  signal:
    expire-seconds: 300
    max-slippage-pct: 0.02
    max-position-pct: 0.15
  auth:
    enabled: true
    api-key: "${API_KEY}"
  cors:
    allowed-origins: "*"

logging:
  level:
    com.factorfactory.jq2qmt: INFO
  file:
    name: ${INSTALL_DIR}/logs/jq2qmt.log
  logback:
    rollingpolicy:
      max-file-size: 50MB
      max-history: 30
EOF

echo "${API_KEY}" > ${INSTALL_DIR}/api-key.txt
chmod 600 ${INSTALL_DIR}/api-key.txt

echo ""
echo "=========================================="
echo " Installation Complete!"
echo "=========================================="
echo ""
echo " Start:   cd ${INSTALL_DIR} && java -jar ${JAR_NAME} --spring.config.location=file:${INSTALL_DIR}/application.yml"
echo " Stop:    Ctrl+C"
echo " Logs:    tail -f ${INSTALL_DIR}/logs/jq2qmt.log"
echo ""
echo " Redis:   ${REDIS_HOST}:${REDIS_PORT} database=${REDIS_DATABASE}"
echo " API Key: ${API_KEY}"
echo " API Key saved to: ${INSTALL_DIR}/api-key.txt"
echo ""
echo " Config file: ${INSTALL_DIR}/application.yml"
echo " Monitor UI:  http://<server-ip>:8080/?apiKey=${API_KEY}"
echo ""
echo "=========================================="
