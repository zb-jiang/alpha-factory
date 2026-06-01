import uuid
import json
import logging
import requests
import time

logger = logging.getLogger(__name__)


class JqSignalSender:
    """
    JoinQuant signal sender stub.
    Sends trade signals to the Java middleware via HTTP API.
    Usage in JoinQuant strategy:
        sender = JqSignalSender(
            middleware_url='http://your-server:8080',
            api_key='your-api-key',
            strategy='factor_alpha_001'
        )
        sender.send_buy('600519.SH', pct=0.05, price=1850.50)
        sender.send_sell('000001.SZ', pct=1.0, price=12.30)
    """

    def __init__(self, middleware_url, api_key='', strategy='', mode=1):
        """
        Args:
            middleware_url: middleware URL, e.g. 'http://1.2.3.4:8080'
            api_key: API key for authentication
            strategy: strategy name, also used as Redis Stream key suffix
            mode: 0=test(always send), 1=production(only send in sim_trade)
        """
        self.middleware_url = middleware_url.rstrip('/')
        self.api_key = api_key
        self.strategy = strategy
        self.mode = mode
        self._session = requests.Session()
        if api_key:
            self._session.headers.update({'X-API-Key': api_key})
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })

    def _is_sim_trade(self):
        try:
            if hasattr(g, '_context_ref') and g._context_ref is not None:
                return g._context_ref.run_params.type == 'sim_trade'
        except Exception:
            pass
        return True

    def _should_send(self):
        if self.mode == 0:
            return True
        return self._is_sim_trade()

    def send_signal(self, action, code, pct, price=0.0, signal_id=None, signal_time=None):
        if not self._should_send():
            logger.debug('Signal skipped (not in sim_trade mode): %s %s', action, code)
            return None

        payload = {
            'action': action,
            'code': code,
            'pct': pct,
            'price': price,
            'strategy': self.strategy,
            'signalId': signal_id or str(uuid.uuid4()),
            'signalTime': signal_time or ''
        }

        url = f'{self.middleware_url}/api/v1/signals/send'
        # 使用 print 确保日志出现在聚宽回测输出中（logger 可能被聚宽过滤）
        print(f'[JqSignalSender] HTTP POST {url} payload={json.dumps(payload, ensure_ascii=False)}')

        for attempt in range(3):
            try:
                resp = self._session.post(url, json=payload, timeout=10)
                result = resp.json()
                if result.get('code') == 200:
                    print(f'[JqSignalSender] Signal sent OK: {action} {code} pct={pct:.4f} recordId={result.get("data", "")}')
                    return result.get('data', {})
                else:
                    print(f'[JqSignalSender] Failed (attempt {attempt + 1}/3): code={result.get("code")} msg={result.get("message", "Unknown")}')
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    return None
            except requests.exceptions.Timeout:
                print(f'[JqSignalSender] Timeout (attempt {attempt + 1}/3): {action} {code}')
                if attempt < 2:
                    time.sleep(1)
                    continue
                return None
            except Exception as e:
                print(f'[JqSignalSender] Error (attempt {attempt + 1}/3): {repr(e)}')
                return None

    def send_buy(self, code, pct, price=0.0):
        return self.send_signal('BUY', code, pct, price)

    def send_sell(self, code, pct, price=0.0):
        return self.send_signal('SELL', code, pct, price)

    def send_adjust(self, code, pct, price=0.0):
        return self.send_signal('ADJUST', code, pct, price)

    def send_batch(self, signals):
        if not self._should_send():
            return None

        payload = []
        for s in signals:
            payload.append({
                'action': s.get('action', 'BUY'),
                'code': s['code'],
                'pct': s['pct'],
                'price': s.get('price', 0.0),
                'strategy': self.strategy,
                'signalId': s.get('signal_id') or str(uuid.uuid4()),
                'signalTime': s.get('signal_time', '')
            })

        try:
            url = f'{self.middleware_url}/api/v1/signals/batch'
            resp = self._session.post(url, json=payload, timeout=30)
            result = resp.json()
            if result.get('code') == 200:
                logger.info('[JqSignalSender] Batch sent: %d signals', len(payload))
                return result.get('data', {})
            else:
                logger.error('[JqSignalSender] Batch failed: %s', result.get('message'))
                return None
        except Exception as e:
            logger.error('[JqSignalSender] Batch error: %s', repr(e))
            return None

    def health_check(self):
        try:
            url = f'{self.middleware_url}/api/v1/health'
            resp = self._session.get(url, timeout=5)
            result = resp.json()
            return result.get('code') == 200
        except Exception:
            return False


def signal_order(code, amount, style=None, context=None):
    """
    Wrapper for order() in JoinQuant strategy.
    Replaces all order() calls to also send signals to middleware.

    Usage:
        # Original: order(code, -shares_to_sell)
        # Replace: signal_order(code, -shares_to_sell, context=context)

        # Original: order(code, actual_shares)
        # Replace: signal_order(code, actual_shares, context=context)

        # Original: order(code, shares, style=LimitOrderStyle(p))
        # Replace: signal_order(code, shares, style=LimitOrderStyle(p), context=context)

    Args:
        code: stock code (e.g. '000001.XSHE')
        amount: number of shares. Positive=buy, Negative=sell
        style: order style (e.g. LimitOrderStyle), optional
        context: 显式传入聚宽 context 对象。如果不传，则回退到 g._context_ref。

    Note: g, order, get_current_data are global objects injected by JoinQuant
    framework into the strategy namespace. They cannot be imported from jqdata.
    """
    if context is None:
        context = g._context_ref
    current_data = get_current_data()
    is_buy = amount > 0
    is_sell = amount < 0

    pct = 0.0
    price = current_data[code].last_price

    if is_sell:
        abs_amount = abs(amount)
        if code in context.portfolio.positions:
            total_amount = context.portfolio.positions[code].total_amount
            remaining_amount = total_amount - abs_amount
            if remaining_amount > 0 and context.portfolio.total_value > 0:
                pct = (remaining_amount * price) / context.portfolio.total_value
            else:
                pct = 0.0
        else:
            pct = 0.0
    else:
        if context.portfolio.total_value > 0:
            pct = (amount * price) / context.portfolio.total_value

    if style is not None:
        my_order = order(code, amount, style=style)
    else:
        my_order = order(code, amount)

    if my_order is not None and hasattr(g, 'signal_sender') and g.signal_sender is not None:
        action = 'BUY' if is_buy else 'SELL'
        g.signal_sender.send_signal(action, code, pct, price)

    return my_order


def init_signal_sender(middleware_url, api_key, strategy, mode=1):
    """
    Initialize signal sender in JoinQuant initialize() function.

    Usage in JoinQuant strategy:
        def initialize(context):
            g._context_ref = context
            g.strategy = 'factor_alpha_001'
            init_signal_sender(
                middleware_url='http://1.2.3.4:8080',
                api_key='your-api-key',
                strategy=g.strategy
            )

    Note: g is a global object injected by JoinQuant framework into the
    strategy namespace. It cannot be imported from the jqdata package.
    """
    g.signal_sender = JqSignalSender(
        middleware_url=middleware_url,
        api_key=api_key,
        strategy=strategy,
        mode=mode
    )
    logger.info('[JqSignalSender] Initialized: url=%s, strategy=%s, mode=%d',
                middleware_url, strategy, mode)
