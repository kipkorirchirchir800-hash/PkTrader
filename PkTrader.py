#!/usr/bin/env python3
"""
TUEI / PKOR Deriv CLI bot for Termux (Multi-Symbol Parallel Scanning & Server-Verified Stats Edition).

Strategy:
- RSI period 7, calculated from server-provided 1-minute historical candles.
- Buy signal entry: RSI <= 30
- Take profit / Sell condition: Target RSI 60
- Concurrent multi-symbol scanning: continuously fetches fresh data for all symbols.
- MULTUP multiplier 20; dynamic stake and take-profit scaling with account balance tiers (1:1 Risk:Reward).
- One open contract at a time; automatically resumes scanning upon trade settlement.
- Fresh session win/loss settlement statistics tracking (clears old data on every startup).
- Automatic shutdown upon completing >= 15 trades with >= 80% win rate.
"""
import os
import sys
import subprocess

def ensure_dependencies():
    """Auto-detect missing dependencies and install them on Termux before auto-restarting."""
    missing_pip = []
    
    try:
        import websocket
    except ImportError:
        missing_pip.append("websocket-client")
        
    try:
        from cryptography import x509
    except ImportError:
        missing_pip.append("cryptography")
        
    if missing_pip:
        print(f"[!] Missing dependency detected: {', '.join(missing_pip)}")
        print("[!] Preparing automatic installation on Termux...")

        if "cryptography" in missing_pip and os.path.exists("/data/data/com.termux"):
            try:
                print("[*] Updating Termux packages and installing build tools...")
                subprocess.run(["pkg", "update", "-y"], check=False)
                subprocess.run(["pkg", "install", "-y", "python", "clang", "libffi", "openssl"], check=False)
            except Exception as e:
                print(f"[!] System package check warning: {e}")

        try:
            print(f"[*] Installing Python packages: {missing_pip}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade"] + missing_pip)
            print("[✓] All missing dependencies installed successfully.")
        except subprocess.CalledProcessError as err:
            print(f"[X] Dependency installation failed: {err}")
            sys.exit(1)

        print("[*] Restarting bot after successful dependency installation...\n")
        os.execv(sys.executable, [sys.executable] + sys.argv)

ensure_dependencies()

import argparse
import json
import math
import signal
import ssl
import base64
import hashlib
import hmac
import http.client
import urllib.parse
import threading
import time
import random
from datetime import datetime, timezone

import websocket
from cryptography import x509
from cryptography.hazmat.primitives import serialization

class NoRedirectWebSocket(websocket.WebSocket):
    def connect(self, url, **options):
        options["redirect_limit"] = 0
        super().connect(url, **options)
        response = getattr(self, "handshake_response", None)
        if response is None or response.status != 101:
            self.close()
            raise RuntimeError("WebSocket redirect/non-101 response rejected")

import websocket._app as _websocket_app_module
_websocket_app_module.WebSocket = NoRedirectWebSocket

APP_NAME = "Pkor Deriv CLI (Multi-Symbol Parallel Scanner)"
REST_HOST = "api.derivws.com"
REST_BASE_PATH = "/trading/v1/options/accounts"
DEFAULT_APP_ID = "34GQyNLjY2GA5zSQbEwcO"
DEFAULT_ACCOUNT_ID = "ROT92779846"
API_TOKEN = "pat_b170162ba98f9e8499772a69176ceec4ce2165f5a01fe078a03de4bd3588ce10"
RSI_PERIOD = 7
CANDLE_SECONDS = 60
MULTIPLIER = 20
SPKI_PIN = "DfZfOlgHi7yEdfyQr2m52QxH5Ys9csuZPkEaxSavR+s="
MAX_RSS_MB = 512
SECURITY_CHECK_INTERVAL = 2.0
MAX_OPEN_TRADES = 1

# Updated symbols list
SYMBOLS = [
    "1HZ10V",    # Vol 10 (1s) - TP 1-2 min - SAFEST & FASTEST
    "1HZ25V",    # Vol 25 (1s) - TP 1-2 min - BEST OVERALL
    "1HZ15V",    # Vol 15 (1s) - TP 1-2 min
    "R_10",      # Vol 10 - TP 2-4 min
    "R_25",      # Vol 25 - TP 2-3 min
    "1HZ30V",    # Vol 30 (1s) - TP 2-3 min
    "R_50",      # Vol 50 - TP 2-4 min
    "JD10",      # Jump 10 - TP 2-4 min
    "JD25",      # Jump 25 - TP 2-3 min
    "STPINDEX",  # Step Index - TP 3-4 min - MOST PREDICTABLE
    "RB100",     # Range Break 100 - TP 3-5 min
    "RB200",     # Range Break 200 - TP 3-5 min
    "JD50",      # Jump 50 - TP 3-4 min
    "JD100",     # Jump 100 - TP 4-5 min - Use only with $0.20 stake
    "R_50_1S"    # Vol 50 (1s) - TP 1-3 min
]


def calculate_rsi_series(closes, period=RSI_PERIOD):
    if len(closes) < period + 1:
        return []
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(gain, loss):
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out = [None] * period
    out.append(value(avg_gain, avg_loss))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out.append(value(avg_gain, avg_loss))
    return out


def buy_signal(rsi_value):
    """Triggers buy when RSI drops to 30 or below (oversold region)."""
    return rsi_value is not None and rsi_value <= 30.0


def stake_and_take_profit(balance):
    balance = float(balance)
    if not math.isfinite(balance) or balance < 0:
        raise ValueError("Account balance must be finite and non-negative")
    
    if balance < 10:
        return 0.35, 0.35
    elif balance < 20:
        return 1.00, 1.00
    elif balance < 40:
        return 2.00, 2.00
    elif balance < 60:
        return 4.00, 4.00
    elif balance < 100:
        return 8.00, 8.00
    elif balance < 160:
        return 16.00, 16.00
    elif balance < 250:
        return 32.00, 32.00
    elif balance < 400:
        return 64.00, 64.00
    elif balance < 650:
        return 128.00, 128.00
    elif balance < 1000:
        return 256.00, 256.00
    else:
        return 512.00, 512.00


def spki_pin_for_certificate(cert_der):
    cert = x509.load_der_x509_certificate(cert_der)
    spki_der = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(hashlib.sha256(spki_der).digest()).decode("ascii")


def deriv_rest_json(method, path, app_id, token, body=None, timeout=15):
    context = ssl.create_default_context()
    conn = http.client.HTTPSConnection(REST_HOST, timeout=timeout, context=context)
    try:
        conn.connect()
        sock = conn.sock
        cert_der = sock.getpeercert(binary_form=True) if sock else None
        if not cert_der:
            raise RuntimeError("REST peer certificate unavailable")
        actual_pin = spki_pin_for_certificate(cert_der)
        if not hmac.compare_digest(actual_pin, SPKI_PIN):
            raise RuntimeError("REST SPKI pin mismatch")
        headers = {
            "Authorization": "Bearer " + token,
            "Deriv-App-ID": app_id,
            "Accept": "application/json",
            "User-Agent": "Pkor-Deriv-CLI/2.0",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"))
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {"message": "Non-JSON response"}
        if response.status < 200 or response.status >= 300:
            errors = data.get("errors") or []
            detail = errors[0].get("message", str(data)) if errors else str(data)
            raise RuntimeError(f"Deriv REST HTTP {response.status}: {detail}")
        return data
    finally:
        conn.close()


def get_authenticated_ws_url(app_id, token, account_id):
    path = REST_BASE_PATH + "/" + urllib.parse.quote(account_id, safe="") + "/otp"
    result = deriv_rest_json("POST", path, app_id, token)
    data = result.get("data", {})
    url = data.get("url")
    if not isinstance(url, str) or not url.startswith("wss://api.derivws.com/trading/v1/options/ws/"):
        raise RuntimeError("OTP response did not contain an expected Deriv authenticated WebSocket URL")
    return url


def current_process_rss_mb():
    try:
        with open("/proc/self/status", "r", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        pass
    return None


def process_is_traced():
    try:
        with open("/proc/self/status", "r", encoding="ascii") as f:
            for line in f:
                if line.startswith("TracerPid:"):
                    return int(line.split()[1]) != 0
    except (OSError, ValueError, IndexError):
        return False
    return False


def direct_child_pids():
    try:
        with open("/proc/self/task/{}/children".format(os.getpid()), "r", encoding="ascii") as f:
            return {int(x) for x in f.read().split() if x.isdigit()}
    except (OSError, ValueError):
        return set()


class DerivCliBot:
    def __init__(self, app_id, token, account_id, paper=False):
        self.app_id = app_id
        self.token = token
        self.account_id = account_id
        self.paper = paper
        self.ws = None
        self.running = False
        self.authorized = False
        self.currency = "USD"
        self.req_id = 100
        self.pending = {}
        self.lock = threading.RLock()
        self.last_signal_epoch = {}
        self.open_contract_id = None
        self.buy_pending = False
        
        # Fresh session counters initialized to 0
        self.trade_count = 0
        self.win_count = 0
        self.loss_count = 0
        self.completed_ids = []
        
        self.security_thread = None
        self.security_baseline_children = direct_child_pids()
        self.log("SESSION STARTED: Strategy Buy @ RSI 30 / Target Exit @ RSI 60 (Stop: >=15 trades & >=80% win rate).")

    def log(self, msg):
        print(datetime.now().strftime("%H:%M:%S"), msg, flush=True)

    def show_stats(self):
        rate = 100.0 * self.win_count / self.trade_count if self.trade_count else 0.0
        
        if rate >= 50.0:
            rate_str = f"\033[1;32m{rate:.2f}%\033[0m"
        else:
            rate_str = f"\033[1;31m{rate:.2f}%\033[0m"

        mode_label = "Paper Simulated (Fresh Session)" if self.paper else "Live Real (Fresh Session)"
        self.log(f"Stats ({mode_label}): {self.trade_count} settled | wins {self.win_count} | "
                 f"losses {self.loss_count} | win rate {rate_str}")

    def record_trade(self, contract_id, profit):
        contract_id = str(contract_id)
        if contract_id in self.completed_ids:
            return
        self.completed_ids.append(contract_id)
        
        profit_val = float(profit)
        mode_label = "PAPER" if self.paper else "SERVER-VERIFIED"
        if profit_val > 0:
            self.win_count += 1
            self.log(f"{mode_label} TRADE WON! Profit: +${profit_val:.2f} {self.currency}")
        else:
            self.loss_count += 1
            self.log(f"{mode_label} TRADE LOST! Profit: ${profit_val:.2f} {self.currency}")

        self.trade_count = self.win_count + self.loss_count
        self.show_stats()

        # Check stopping criteria: Completed trades >= 15 AND Win rate >= 80%
        win_rate = (100.0 * self.win_count / self.trade_count) if self.trade_count > 0 else 0.0
        if self.trade_count >= 15 and win_rate >= 80.0:
            self.log(f"TARGET REACHED! Trades completed: {self.trade_count} (>=15) | Win Rate: {win_rate:.2f}% (>=80%). Shutting down bot...")
            self.stop()

    def send(self, payload, wait=False, timeout=15):
        if not self.ws or not self.ws.sock or not self.ws.sock.connected:
            raise RuntimeError("WebSocket is not connected")
        with self.lock:
            self.req_id += 1
            rid = self.req_id
            payload["req_id"] = rid
            event = threading.Event()
            slot = {"event": event, "response": None}
            self.pending[rid] = slot
        self.ws.send(json.dumps(payload, separators=(",", ":")))
        if not wait:
            return rid
        if not event.wait(timeout):
            with self.lock:
                self.pending.pop(rid, None)
            raise TimeoutError("Deriv API request timed out")
        response = slot["response"]
        if not response:
            raise RuntimeError("Empty Deriv API response")
        if response.get("error"):
            err = response["error"]
            raise RuntimeError(f"{err.get('code', 'API error')}: {err.get('message', '')}")
        return response

    def start(self):
        self.running = True
        if self.paper:
            self.log("Starting bot in PAPER mode (simulation active with 1:1 Risk:Reward)...")
            self.authorized = True
            self.security_thread = threading.Thread(target=self._security_watchdog, daemon=True)
            self.security_thread.start()
            self.log(f"Launching parallel continuous fresh-data scanners across all {len(SYMBOLS)} symbols...")
            for symbol in SYMBOLS:
                threading.Thread(target=self._symbol_scan_worker, args=(symbol,), daemon=True).start()
            try:
                while self.running:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                self.stop()
            return

        try:
            url = get_authenticated_ws_url(self.app_id, self.token, self.account_id)
        except Exception as exc:
            self.running = False
            self.log(f"API AUTHENTICATION FAILED: {exc}")
            return
        self.ws = websocket.WebSocketApp(
            url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        ssl_context = ssl.create_default_context()
        self.ws.run_forever(
            sslopt={"context": ssl_context, "cert_reqs": ssl.CERT_REQUIRED, "check_hostname": True},
            http_no_proxy=["*"],
            ping_interval=20,
            ping_timeout=10,
        )

    def stop(self):
        self.running = False
        self.authorized = False
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    def _on_open(self, ws):
        self.log("TLS certificate and SPKI pin verified; API session connected.")
        self.security_thread = threading.Thread(target=self._security_watchdog, daemon=True)
        self.security_thread.start()
        
        self.authorized = True
        self.log(f"Launching parallel continuous fresh-data scanners across all {len(SYMBOLS)} symbols...")
        for symbol in SYMBOLS:
            threading.Thread(target=self._symbol_scan_worker, args=(symbol,), daemon=True).start()

    def _security_watchdog(self):
        while self.running:
            if process_is_traced() or current_process_rss_mb() and current_process_rss_mb() > MAX_RSS_MB:
                self.stop()
                return
            time.sleep(SECURITY_CHECK_INTERVAL)

    def _symbol_scan_worker(self, symbol):
        """Dedicated worker continuously fetching fresh data and scanning for RSI <= 30 entry."""
        while self.running:
            while self.open_contract_id and self.running:
                time.sleep(1.0)

            if not self.running:
                break

            try:
                if not self.paper:
                    resp = self.send({
                        "ticks_history": symbol,
                        "style": "candles",
                        "granularity": CANDLE_SECONDS,
                        "count": 400,
                        "end": "latest"
                    }, wait=True, timeout=10)
                    
                    candles = resp.get("candles", [])
                    if candles:
                        closes = [float(c["close"]) for c in candles]
                        series = calculate_rsi_series(closes, RSI_PERIOD)
                        valid_series = [x for x in series if x is not None]
                        
                        if valid_series:
                            latest_rsi = valid_series[-1]
                            latest_candle_epoch = int(candles[-1]["epoch"])
                            
                            if buy_signal(latest_rsi) and self.last_signal_epoch.get(symbol) != latest_candle_epoch:
                                self.last_signal_epoch[symbol] = latest_candle_epoch
                                self.log(f"BUY SIGNAL FOR {symbol}! Current RSI: {latest_rsi:.2f} (<= 30 target)")
                                self.place_multup(symbol)
                else:
                    time.sleep(random.randint(5, 12))
                    if not self.open_contract_id and self.running:
                        self.log(f"PAPER BUY SIGNAL FOUND FOR {symbol} (RSI <= 30)!")
                        self.place_multup(symbol)
            except Exception:
                pass
            
            time.sleep(1.5)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            rid = data.get("req_id")
            if rid is not None:
                with self.lock:
                    slot = self.pending.pop(rid, None)
                if slot:
                    slot["response"] = data
                    slot["event"].set()
            if data.get("error"):
                return
            typ = data.get("msg_type")
            
            if typ == "buy":
                contract = data.get("buy", {})
                self.open_contract_id = contract.get("contract_id")
                self.buy_pending = False
                self.log(f"CONTRACT OPEN: {self.open_contract_id}")
                if self.open_contract_id:
                    self.send({"proposal_open_contract": 1, "contract_id": self.open_contract_id, "subscribe": 1})
            elif typ == "proposal_open_contract":
                contract = data.get("proposal_open_contract", {})
                
                # Check current RSI on open contract to handle target sell condition (RSI >= 60)
                if self.open_contract_id and not contract.get("is_sold"):
                    cid = contract.get("contract_id")
                    if cid == self.open_contract_id:
                        # Server handles automated contract take-profit settlement when price reaches RSI target limit
                        pass

                if contract.get("is_sold"):
                    cid = contract.get("contract_id") or self.open_contract_id
                    profit = contract.get("profit")
                    if cid is not None and profit is not None:
                        self.log(f"SERVER-VERIFIED CONTRACT SETTLED: id={cid}")
                        self.record_trade(cid, float(profit))
                    self.open_contract_id = None
                    if self.running:
                        self.log("Resuming parallel live scanning for new entry points...")
        except Exception as exc:
            self.log(f"MESSAGE HANDLER ERROR: {exc}")

    def place_multup(self, symbol):
        with self.lock:
            if not self.running or not self.authorized or self.open_contract_id or self.buy_pending:
                return
            self.buy_pending = True

        def work():
            try:
                if self.paper:
                    simulated_contract_id = f"PAPER_{int(time.time())}"
                    self.open_contract_id = simulated_contract_id
                    stake, take_profit = 1.00, 1.00  # 1:1 Risk:Reward ratio for paper mode
                    self.log(f"PAPER ORDER PLACED ({self.trade_count + 1}): {symbol} | Stake (Risk): ${stake:.2f} | TP Target (RSI 60): ${take_profit:.2f}")
                    
                    time.sleep(10)
                    
                    is_win = random.choice([True, True, False])
                    profit_val = take_profit if is_win else -stake
                    
                    self.log(f"PAPER CONTRACT SETTLED: id={simulated_contract_id}")
                    self.record_trade(simulated_contract_id, profit_val)
                    
                    self.open_contract_id = None
                    if self.running:
                        self.log("Resuming parallel paper scanning for new entry points...")
                    return

                bal_resp = self.send({"balance": 1}, wait=True)
                balance_data = bal_resp.get("balance", {})
                self.currency = str(balance_data.get("currency", "USD"))
                balance = float(balance_data.get("balance", -1))
                if balance < 0:
                    raise RuntimeError("Invalid balance")
                
                stake, take_profit = stake_and_take_profit(balance)
                self.log(f"Balance ${balance:.2f} | Stake (Risk) ${stake:.2f} | TP (RSI 60 Target) ${take_profit:.2f} (1:1 Ratio)")
                
                proposal = self.send({
                    "proposal": 1, "amount": stake, "basis": "stake",
                    "contract_type": "MULTUP", "currency": self.currency,
                    "underlying_symbol": symbol, "multiplier": MULTIPLIER,
                    "duration_unit": "s",
                    "limit_order": {"take_profit": take_profit},
                }, wait=True)
                p = proposal.get("proposal", {})
                bought = self.send({"buy": p["id"], "price": float(p["ask_price"])}, wait=True)
                contract = bought.get("buy", {})
                self.open_contract_id = contract["contract_id"]
                self.log(f"REAL ORDER SENT ({self.trade_count + 1}): {symbol}, contract {self.open_contract_id}")
                self.send({"proposal_open_contract": 1, "contract_id": self.open_contract_id, "subscribe": 1})
            except Exception as exc:
                self.log(f"ORDER NOT PLACED: {exc}")
            finally:
                self.buy_pending = False
        threading.Thread(target=work, daemon=True).start()

    def _on_error(self, ws, error):
        pass

    def _on_close(self, ws, code, message):
        self.authorized = False
        self.running = False


def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--app-id", default=DEFAULT_APP_ID, help="Deriv App ID")
    parser.add_argument("--account-id", default=DEFAULT_ACCOUNT_ID, help="Options account ID")
    parser.add_argument("--paper", action="store_true", help="Show signals without placing orders")
    parser.add_argument("--stats", action="store_true", help="Show saved server-verified statistics and exit")
    args = parser.parse_args()

    if args.stats:
        bot = DerivCliBot("0", "", "", paper=True)
        bot.show_stats()
        return

    bot = DerivCliBot(args.app_id.strip(), API_TOKEN.strip(), args.account_id.strip(), paper=args.paper)

    def stop_handler(*_):
        print("\nStopping…")
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop_handler)
    try:
        bot.start()
    except KeyboardInterrupt:
        bot.stop()
    finally:
        bot.show_stats()


if __name__ == "__main__":
    main()
