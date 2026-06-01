import time
import json
import uuid
import logging
import signal
import threading
from datetime import datetime
from typing import Optional, Dict, List

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s'
)
logger = logging.getLogger('QmtSignalWorker')


class RiskChecker:
    MAX_SLIPPAGE_PCT = 0.02
    SIGNAL_EXPIRY_SECONDS = 300
    MAX_POSITION_PCT = 0.15

    def __init__(self):
        self.processed_ids = set()
        self._lock = threading.Lock()

    def check_slippage(self, signal_price, current_price):
        if signal_price <= 0:
            return True, ''
        slippage = abs(current_price - signal_price) / signal_price
        if slippage > self.MAX_SLIPPAGE_PCT:
            return False, f'slippage {slippage:.4f} > {self.MAX_SLIPPAGE_PCT}'
        return True, ''

    def check_expiry(self, signal_time_str):
        if not signal_time_str:
            return True, ''
        try:
            signal_time = datetime.strptime(signal_time_str, '%Y-%m-%d %H:%M:%S')
            elapsed = (datetime.now() - signal_time).total_seconds()
            if elapsed > self.SIGNAL_EXPIRY_SECONDS:
                return False, f'expired {elapsed:.0f}s > {self.SIGNAL_EXPIRY_SECONDS}s'
        except ValueError:
            try:
                signal_time = datetime.fromisoformat(signal_time_str)
                elapsed = (datetime.now() - signal_time).total_seconds()
                if elapsed > self.SIGNAL_EXPIRY_SECONDS:
                    return False, f'expired {elapsed:.0f}s > {self.SIGNAL_EXPIRY_SECONDS}s'
            except Exception:
                pass
        return True, ''

    def check_duplicate(self, signal_id):
        with self._lock:
            if signal_id in self.processed_ids:
                return False, f'duplicate signal {signal_id}'
            self.processed_ids.add(signal_id)
            return True, ''

    def check_position(self, pct, action):
        if action == 'BUY' and pct > self.MAX_POSITION_PCT:
            return False, f'position {pct:.4f} > max {self.MAX_POSITION_PCT}'
        return True, ''


class QmtSignalWorker:
    """
    QMT signal worker that consumes trade signals from the Java middleware
    and executes them via miniQMT (xtquant).

    Two modes:
    1. HTTP mode (default): Polls middleware for signals via REST API
    2. Redis mode: Reads directly from Redis Stream (lower latency)

    Usage:
        worker = QmtSignalWorker(
            middleware_url='http://1.2.3.4:8080',
            api_key='your-api-key',
            strategy='factor_alpha_001',
            qmt_path='C:/国金QMT/userdata_mini',
            account_id='12345678'
        )
        worker.start()
    """

    def __init__(self, middleware_url, api_key='', strategy='',
                 qmt_path='', account_id='',
                 consumer_name='worker-1',
                 poll_interval=2,
                 mode='http',
                 redis_host='127.0.0.1', redis_port=6379, redis_password='',
                 redis_db=1,
                 dry_run=False):
        self.middleware_url = middleware_url.rstrip('/')
        self.api_key = api_key
        self.strategy = strategy
        self.qmt_path = qmt_path
        self.account_id = account_id
        self.consumer_name = consumer_name
        self.poll_interval = poll_interval
        self.mode = mode
        self.dry_run = dry_run

        self.risk_checker = RiskChecker()
        self._running = False
        self._session = requests.Session()
        if api_key:
            self._session.headers.update({'X-API-Key': api_key})
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })

        self._xt_trader = None
        self._xt_account = None
        self._redis_client = None

        if mode == 'redis':
            self._init_redis(redis_host, redis_port, redis_password, redis_db)

    def _init_redis(self, host, port, password, db=1):
        try:
            import redis
            self._redis_client = redis.Redis(
                host=host, port=port, password=password,
                db=db, decode_responses=True
            )
            self._redis_client.ping()
            logger.info('Redis connected: %s:%d db=%d', host, port, db)
        except ImportError:
            raise ImportError('redis package required for Redis mode: pip install redis')
        except Exception as e:
            raise ConnectionError(f'Redis connection failed: {e}')

    def connect_qmt(self):
        if self.dry_run:
            logger.info('[DRY RUN] QMT connection skipped')
            return True

        try:
            from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
            from xtquant.xttype import StockAccount

            session_id = int(time.time() * 1000)
            self._xt_trader = XtQuantTrader(self.qmt_path, session_id)

            class QMTCallback(XtQuantTraderCallback):
                def on_stock_order(self, order):
                    logger.info('Order callback: %s dir=%s price=%s vol=%s',
                                order.order_remark, order.order_type,
                                order.price, order.order_volume)

                def on_stock_trade(self, trade):
                    logger.info('Trade callback: %s price=%s vol=%s',
                                trade.order_remark,
                                trade.traded_price, trade.traded_volume)

                def on_order_error(self, order_error):
                    logger.error('Order error: %s err=%s',
                                 order_error.order_remark, order_error.error_msg)

                def on_disconnected(self):
                    logger.warning('QMT disconnected')

            self._xt_trader.register_callback(QMTCallback())
            self._xt_trader.start()

            connect_result = self._xt_trader.connect()
            if connect_result == 0:
                logger.info('QMT connected successfully')
            else:
                logger.error('QMT connection failed: %d', connect_result)
                return False

            self._xt_account = StockAccount(self.account_id)
            logger.info('QMT account: %s', self.account_id)
            return True

        except ImportError:
            logger.error('xtquant not available. Install miniQMT first.')
            return False
        except Exception as e:
            logger.error('QMT connection error: %s', repr(e))
            return False

    def start(self):
        if not self.connect_qmt():
            logger.error('Failed to connect QMT, exiting')
            return

        self._running = True
        logger.info('QmtSignalWorker started: strategy=%s, mode=%s, poll=%ds',
                     self.strategy, self.mode, self.poll_interval)

        if self.mode == 'redis':
            self._consume_loop_redis()
        else:
            self._consume_loop_http()

    def stop(self):
        self._running = False
        logger.info('QmtSignalWorker stopping...')

    def _consume_loop_http(self):
        while self._running:
            try:
                signals = self._poll_signals_http()
                if signals:
                    for sig in signals:
                        self._process_signal(sig)
                else:
                    time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error('Consume loop error: %s', repr(e))
                time.sleep(self.poll_interval)

        logger.info('QmtSignalWorker stopped')

    def _consume_loop_redis(self):
        stream_key = f'factor_factory:{self.strategy}'
        group = 'qmt_workers'

        while self._running:
            try:
                messages = self._redis_client.xreadgroup(
                    group, self.consumer_name,
                    {stream_key: '>'},
                    count=10,
                    block=self.poll_interval * 1000
                )

                if messages:
                    for stream_name, msgs in messages:
                        for msg_id, fields in msgs:
                            signal_data = dict(fields)
                            signal_data['_redis_record_id'] = msg_id
                            signal_data['_stream_key'] = stream_key
                            self._process_signal(signal_data)
                            self._redis_client.xack(stream_key, group, msg_id)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error('Redis consume error: %s', repr(e))
                time.sleep(self.poll_interval)

        logger.info('QmtSignalWorker stopped')

    def _poll_signals_http(self):
        try:
            url = (f'{self.middleware_url}/api/v1/signals/consume'
                   f'?strategy={self.strategy}'
                   f'&consumer={self.consumer_name}'
                   f'&count=10')
            resp = self._session.get(url, timeout=10)
            result = resp.json()
            if result.get('code') == 200 and result.get('data'):
                return result['data']
            return []
        except Exception as e:
            logger.error('Poll signals error: %s', repr(e))
            return []

    def _ack_signal_http(self, strategy, record_id):
        try:
            url = (f'{self.middleware_url}/api/v1/signals/ack'
                   f'?strategy={strategy}'
                   f'&recordId={record_id}')
            self._session.post(url, timeout=5)
        except Exception as e:
            logger.error('ACK signal error: %s', repr(e))

    def _process_signal(self, signal_data):
        signal_id = signal_data.get('signal_id', '')
        action = signal_data.get('action', '')
        code = signal_data.get('code', '')
        pct = float(signal_data.get('pct', 0))
        price = float(signal_data.get('price', 0))
        signal_time = signal_data.get('signal_time', '')
        strategy = signal_data.get('strategy', self.strategy)
        record_id = signal_data.get('_redis_record_id', '')

        logger.info('Processing signal: action=%s code=%s pct=%.4f signalId=%s',
                     action, code, pct, signal_id)

        ok, reason = self.risk_checker.check_duplicate(signal_id)
        if not ok:
            self._report_result(strategy, signal_id, 'SKIPPED', remark=reason)
            if self.mode == 'http' and record_id:
                self._ack_signal_http(strategy, record_id)
            return

        ok, reason = self.risk_checker.check_expiry(signal_time)
        if not ok:
            self._report_result(strategy, signal_id, 'SKIPPED', remark=reason)
            if self.mode == 'http' and record_id:
                self._ack_signal_http(strategy, record_id)
            return

        ok, reason = self.risk_checker.check_position(pct, action)
        if not ok:
            self._report_result(strategy, signal_id, 'REJECTED', remark=reason)
            if self.mode == 'http' and record_id:
                self._ack_signal_http(strategy, record_id)
            return

        if action == 'BUY':
            self._execute_buy(strategy, signal_id, code, pct, price)
        elif action == 'SELL':
            self._execute_sell(strategy, signal_id, code, pct, price)
        elif action == 'ADJUST':
            logger.info('ADJUST signal received, skipping: %s', signal_id)
            self._report_result(strategy, signal_id, 'SKIPPED', remark='ADJUST not supported')
        else:
            logger.warning('Unknown action: %s', action)
            self._report_result(strategy, signal_id, 'REJECTED', remark=f'Unknown action: {action}')

        if self.mode == 'http' and record_id:
            self._ack_signal_http(strategy, record_id)

    def _execute_buy(self, strategy, signal_id, code, pct, signal_price):
        if self.dry_run:
            logger.info('[DRY RUN] BUY code=%s pct=%.4f', code, pct)
            self._report_result(strategy, signal_id, 'FILLED',
                                filled_price=signal_price, filled_volume=0,
                                filled_amount=0, remark='DRY RUN')
            return

        try:
            from xtquant import xtconstant

            current_price = self._get_current_price(code)
            if current_price <= 0:
                self._report_result(strategy, signal_id, 'REJECTED', remark='Cannot get current price')
                return

            ok, reason = self.risk_checker.check_slippage(signal_price, current_price)
            if not ok:
                self._report_result(strategy, signal_id, 'SKIPPED', remark=reason)
                return

            total_asset = self._xt_trader.query_stock_asset(self._xt_account)
            if total_asset is None:
                self._report_result(strategy, signal_id, 'ERROR', remark='Cannot query account asset')
                return

            available_cash = total_asset.m_dAvailable
            target_amount = available_cash * pct

            if target_amount < current_price * 100:
                self._report_result(strategy, signal_id, 'SKIPPED',
                                    remark=f'Insufficient funds: target={target_amount:.2f}')
                return

            order_id = self._xt_trader.order_stock(
                self._xt_account,
                code,
                xtconstant.STOCK_BUY,
                0,
                xtconstant.FIX_PRICE,
                current_price,
                strategy_name=strategy,
                order_remark=f'jq2qmt:{signal_id}'
            )

            if order_id > 0:
                logger.info('BUY order placed: code=%s price=%.2f amount=%.2f orderId=%d',
                            code, current_price, target_amount, order_id)
                self._report_result(strategy, signal_id, 'FILLED',
                                    order_id=order_id,
                                    filled_price=current_price,
                                    remark='Buy order placed')
            else:
                self._report_result(strategy, signal_id, 'REJECTED',
                                    remark=f'Order rejected, orderId={order_id}')

        except Exception as e:
            logger.error('Execute buy error: %s', repr(e))
            self._report_result(strategy, signal_id, 'ERROR', remark=str(e))

    def _execute_sell(self, strategy, signal_id, code, pct, signal_price):
        if self.dry_run:
            logger.info('[DRY RUN] SELL code=%s pct=%.4f', code, pct)
            self._report_result(strategy, signal_id, 'FILLED',
                                filled_price=signal_price, filled_volume=0,
                                filled_amount=0, remark='DRY RUN')
            return

        try:
            from xtquant import xtconstant

            positions = self._xt_trader.query_stock_positions(self._xt_account)
            target_pos = None
            for pos in positions:
                if pos.stock_code == code:
                    target_pos = pos
                    break

            if target_pos is None or target_pos.m_nCanUseVolume <= 0:
                self._report_result(strategy, signal_id, 'SKIPPED',
                                    remark='No available position to sell')
                return

            current_price = self._get_current_price(code)
            ok, reason = self.risk_checker.check_slippage(signal_price, current_price)
            if not ok:
                self._report_result(strategy, signal_id, 'SKIPPED', remark=reason)
                return

            total_asset = self._xt_trader.query_stock_asset(self._xt_account)
            if total_asset is None:
                self._report_result(strategy, signal_id, 'ERROR', remark='Cannot query account asset')
                return

            if pct <= 1e-6:
                sell_volume = target_pos.m_nCanUseVolume
            else:
                target_value = total_asset.m_dTotalAsset * pct
                current_value = target_pos.m_nCanUseVolume * current_price
                if current_value <= target_value:
                    self._report_result(strategy, signal_id, 'SKIPPED',
                                        remark=f'Already below target: current={current_value:.2f} target={target_value:.2f}')
                    return
                sell_value = current_value - target_value
                sell_volume = int(sell_value / current_price)

            lot_size = 200 if code.startswith('688') else 100
            sell_volume = (sell_volume // lot_size) * lot_size

            if sell_volume <= 0:
                self._report_result(strategy, signal_id, 'SKIPPED',
                                    remark='Sell volume too small after rounding')
                return

            order_id = self._xt_trader.order_stock(
                self._xt_account,
                code,
                xtconstant.STOCK_SELL,
                sell_volume,
                xtconstant.FIX_PRICE,
                current_price,
                strategy_name=strategy,
                order_remark=f'jq2qmt:{signal_id}'
            )

            if order_id > 0:
                logger.info('SELL order placed: code=%s vol=%d price=%.2f orderId=%d',
                            code, sell_volume, current_price, order_id)
                self._report_result(strategy, signal_id, 'FILLED',
                                    order_id=order_id,
                                    filled_price=current_price,
                                    filled_volume=sell_volume,
                                    remark='Sell order placed')
            else:
                self._report_result(strategy, signal_id, 'REJECTED',
                                    remark=f'Order rejected, orderId={order_id}')

        except Exception as e:
            logger.error('Execute sell error: %s', repr(e))
            self._report_result(strategy, signal_id, 'ERROR', remark=str(e))

    def _get_current_price(self, code):
        try:
            from xtquant import xtdata
            tick = xtdata.get_full_tick([code])
            if tick and code in tick:
                return tick[code]['lastPrice']
        except Exception as e:
            logger.error('Get price error for %s: %s', code, repr(e))
        return 0.0

    def _report_result(self, strategy, signal_id, status,
                       order_id=None, filled_price=None,
                       filled_volume=None, filled_amount=None,
                       remark=''):
        payload = {
            'signalId': signal_id,
            'status': status,
            'strategy': strategy,
            'executeTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'remark': remark
        }
        if order_id is not None:
            payload['orderId'] = order_id
        if filled_price is not None:
            payload['filledPrice'] = filled_price
        if filled_volume is not None:
            payload['filledVolume'] = filled_volume
        if filled_amount is not None:
            payload['filledAmount'] = filled_amount

        try:
            url = f'{self.middleware_url}/api/v1/results/report'
            resp = self._session.post(url, json=payload, timeout=10)
            result = resp.json()
            if result.get('code') == 200:
                logger.info('Result reported: signalId=%s status=%s', signal_id, status)
            else:
                logger.error('Report result failed: %s', result.get('message'))
        except Exception as e:
            logger.error('Report result error: %s', repr(e))


def main():
    import argparse

    parser = argparse.ArgumentParser(description='QMT Signal Worker')
    parser.add_argument('--middleware-url', required=True, help='Middleware URL')
    parser.add_argument('--api-key', default='', help='API key')
    parser.add_argument('--strategy', required=True, help='Strategy name')
    parser.add_argument('--qmt-path', default='', help='miniQMT path')
    parser.add_argument('--account-id', default='', help='QMT account ID')
    parser.add_argument('--consumer-name', default='worker-1', help='Consumer name')
    parser.add_argument('--poll-interval', type=int, default=2, help='Poll interval (seconds)')
    parser.add_argument('--mode', choices=['http', 'redis'], default='http', help='Consume mode')
    parser.add_argument('--redis-host', default='127.0.0.1', help='Redis host (redis mode)')
    parser.add_argument('--redis-port', type=int, default=6379, help='Redis port (redis mode)')
    parser.add_argument('--redis-password', default='', help='Redis password (redis mode)')
    parser.add_argument('--redis-db', type=int, default=1, help='Redis database number (redis mode)')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no real orders)')

    args = parser.parse_args()

    worker = QmtSignalWorker(
        middleware_url=args.middleware_url,
        api_key=args.api_key,
        strategy=args.strategy,
        qmt_path=args.qmt_path,
        account_id=args.account_id,
        consumer_name=args.consumer_name,
        poll_interval=args.poll_interval,
        mode=args.mode,
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        redis_password=args.redis_password,
        redis_db=args.redis_db,
        dry_run=args.dry_run
    )

    def signal_handler(sig, frame):
        logger.info('Received signal %s, stopping...', sig)
        worker.stop()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    worker.start()


if __name__ == '__main__':
    main()
