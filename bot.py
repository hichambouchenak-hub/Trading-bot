# ========================================================
# البوت V18 - V12 محسن (مخفف + مطاردة مبكرة)
# مع ترقيم الصفقات
# ========================================================

import ccxt
import pandas as pd
import requests
import time
import threading
import json
import os
import random
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# =========================
# إعدادات الربط
# =========================
API_KEY = "bg_bd03b4ab2190f5aaaf0965217f96b23b"
SECRET = "3de47da68bb3828436fd4ab7e79918247a1677b5e92b2a3086a2a81ad68f34fc"
PASSWORD = "Hich1978"

TELEGRAM_TOKEN = "8681138761:AAEUVPSzkPzrwLMGcwotBbISQ3D1b-QZds8"
CHAT_ID = "5809146953"

# =========================
# إعدادات البوت V18
# =========================
START_BALANCE = 50.00
TRADE_SIZE = 1.5
MAX_OPEN_TRADES = 30
STOP_LOSS_PCT = 0.025

# مستويات المطاردة المحسنة V18 (حماية مبكرة)
PROFIT_LEVELS = [
    (1.5, 0.5),   # عند 1.5% ربح → ستوب 0.5%
    (3.0, 1.5),   # عند 3% ربح → ستوب 1.5%
    (5.0, 3.0),   # عند 5% ربح → ستوب 3%
    (10.0, 7.0),  # عند 10% ربح → ستوب 7%
    (20.0, 15.0), # عند 20% ربح → ستوب 15%
]

# العملات المستبعدة
EXCLUDED_COINS = ["USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USD1", "USDT", "BGB", "BITGET"]

# =========================
# الاتصال بالمنصة
# =========================
exchange = ccxt.bitget({
    'apiKey': API_KEY,
    'secret': SECRET,
    'password': PASSWORD,
    'enableRateLimit': True,
    'options': {'createMarketBuy_RequiresPrice': False}
})

# =========================
# إدارة الحالة
# =========================
MEMORY_FILE = "bot_v18_state.json"

def load_state():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f:
                data = json.load(f)
                for key in ["active_trades", "wins", "losses", "trade_count"]:
                    if key not in data:
                        data[key] = {} if key == "active_trades" else 0
                return data
        except:
            pass
    return {"active_trades": {}, "wins": 0, "losses": 0, "trade_count": 0}

def save_state():
    try:
        with open(MEMORY_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except:
        pass

state = load_state()

def send_telegram(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        )
    except:
        pass

# =========================
# مؤشرات يدوية
# =========================
def calculate_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def is_btc_uptrend(df_btc):
    try:
        if df_btc is None or len(df_btc) < 50:
            return True
        ema50 = calculate_ema(df_btc['close'], 50)
        if ema50 is None:
            return True
        return df_btc['close'].iloc[-1] > ema50.iloc[-1]
    except:
        return True

# =========================
# جلب البيانات والاستراتيجية
# =========================
def fetch_ohlcv(symbol, limit=500):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, '1h', limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df
    except:
        return None

def calculate_fibonacci(df):
    """حساب Fibonacci المحسن V18 (مخفف)"""
    df['h_max'] = df['high'].rolling(50).max()
    df['l_min'] = df['low'].rolling(50).min()
    df['fib_deep'] = df['h_max'] - (0.786 * (df['h_max'] - df['l_min']))  # 0.764 → 0.786
    df['gate_35'] = df['l_min'] + (0.35 * (df['h_max'] - df['l_min']))    # 0.382 → 0.35
    return df

def check_entry(df, current_index):
    if current_index < 50:
        return False
    fib_deep = df['fib_deep'].iloc[current_index]
    gate_35 = df['gate_35'].iloc[current_index]
    low_20 = df['low'].iloc[current_index-20:current_index].min()
    current_close = df['close'].iloc[current_index]
    return low_20 <= fib_deep and current_close > gate_35

def calculate_stop(entry_price, max_price, current_pnl):
    """حساب الستوب مع مستويات V18"""
    fixed_stop = entry_price * (1 - STOP_LOSS_PCT)
    if current_pnl < PROFIT_LEVELS[0][0]:
        return fixed_stop
    final_stop = fixed_stop
    for trigger, trail_dist in PROFIT_LEVELS:
        if current_pnl >= trigger:
            final_stop = max(final_stop, max_price * (1 - (trail_dist / 100)))
        else:
            break
    return final_stop

# =========================
# إدارة الصفقات
# =========================
def trade_manager():
    while True:
        for symbol, t in list(state['active_trades'].items()):
            try:
                curr_p = exchange.fetch_ticker(symbol)['last']
                entry = t['entry']
                t['max_p'] = max(t.get('max_p', curr_p), curr_p)
                pnl = (curr_p - entry) / entry * 100
                pnl_usd = (pnl / 100) * TRADE_SIZE
                
                stop_price = calculate_stop(entry, t['max_p'], pnl)
                
                if curr_p <= stop_price:
                    coin = symbol.split('/')[0]
                    balance = exchange.fetch_balance()
                    amt = balance['total'].get(coin, 0)
                    if amt > 0:
                        exchange.create_market_sell_order(symbol, amt)
                        
                        if pnl > 0:
                            state['wins'] += 1
                            res = "✅ *ربح* ✅"
                        else:
                            state['losses'] += 1
                            res = "❌ *خسارة* ❌"
                        
                        max_gain = (t['max_p'] - entry) / entry * 100
                        
                        send_telegram(f"""
{res}
• الصفقة رقم: `{t['trade_id']}`
• العملة: `{symbol}`
• الربح المحقق: `{pnl:+.2f}%` (+{pnl_usd:.2f}$)
• أعلى ربح وصل: `{max_gain:.1f}%`
""")
                        del state['active_trades'][symbol]
                        save_state()
                        
            except Exception as e:
                print(f"خطأ في trade_manager: {e}")
        time.sleep(20)

# =========================
# فاحص الصفقات
# =========================
def scanner():
    btc_df = None
    while True:
        try:
            if len(state['active_trades']) < MAX_OPEN_TRADES:
                if btc_df is None:
                    btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1h', limit=100)
                    btc_df = pd.DataFrame(btc_ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                
                if is_btc_uptrend(btc_df):
                    markets = exchange.load_markets()
                    symbols = [s for s in markets if s.endswith('/USDT') and s.split('/')[0] not in EXCLUDED_COINS]
                    random.shuffle(symbols)
                    
                    for s in symbols[:50]:
                        if s in state['active_trades']:
                            continue
                        
                        df = fetch_ohlcv(s, limit=200)
                        if df is None or len(df) < 100:
                            continue
                        
                        df = calculate_fibonacci(df)
                        
                        if check_entry(df, len(df)-1):
                            exchange.create_market_buy_order(s, None, params={'cost': TRADE_SIZE})
                            price = exchange.fetch_ticker(s)['last']
                            
                            # زيادة رقم الصفقة
                            state['trade_count'] += 1
                            trade_id = state['trade_count']
                            
                            state['active_trades'][s] = {"entry": price, "max_p": price, "trade_id": trade_id}
                            save_state()
                            
                            send_telegram(f"""
🏹 *دخول جديد* 🏹
• الصفقة رقم: `{trade_id}`
• العملة: `{s}`
• سعر الدخول: `{price:.8f}$`
• حجم الصفقة: `{TRADE_SIZE}$`
• الصفقات المفتوحة: `{len(state['active_trades'])}/{MAX_OPEN_TRADES}`
""")
                            break
                                
        except Exception as e:
            print(f"خطأ في scanner: {e}")
        time.sleep(60)

# =========================
# تقرير دوري
# =========================
def report_loop():
    while True:
        try:
            balance = exchange.fetch_balance()
            usdt_now = balance['total'].get('USDT', 0)
            trades_value = 0
            details = []
            
            for s, t in state['active_trades'].items():
                try:
                    coin = s.split('/')[0]
                    amt = balance['total'].get(coin, 0)
                    price = exchange.fetch_ticker(s)['last']
                    value = amt * price
                    trades_value += value
                    
                    pnl = (price - t['entry']) / t['entry'] * 100
                    max_gain = (t['max_p'] - t['entry']) / t['entry'] * 100
                    details.append(f"• #{t['trade_id']} `{s}`: {pnl:+.2f}% (Max: {max_gain:.1f}%)")
                except:
                    pass
            
            total = usdt_now + trades_value
            growth = ((total - START_BALANCE) / START_BALANCE) * 100
            total_trades = state['wins'] + state['losses']
            win_rate = (state['wins'] / total_trades * 100) if total_trades > 0 else 0
            
            msg = f"""
📊 *تقرير V18* 📊
⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'=' * 40}

📈 *الحساب:*
💰 الإجمالي: `{total:.2f}$` ({growth:+.2f}%)
💵 كاش: `{usdt_now:.2f}$`
📦 عملات: `{trades_value:.2f}$`

📊 *الإحصائيات:*
• صفقات مفتوحة: `{len(state['active_trades'])}/{MAX_OPEN_TRADES}`
• ✅ رابحة: `{state['wins']}`
• ❌ خاسرة: `{state['losses']}`
• 📈 نسبة الربح: `{win_rate:.1f}%`
• 🔢 إجمالي الصفقات المفتوحة: `{state['trade_count']}`

🔹 *الصفقات المفتوحة:*
{chr(10).join(details) if details else 'لا توجد صفقات مفتوحة'}
"""
            send_telegram(msg)
            
        except Exception as e:
            print(f"خطأ في report: {e}")
        time.sleep(900)

# =========================
# التشغيل
# =========================
if __name__ == "__main__":
    send_telegram(f"""
🚀 *البوت V18 (محسن) بدأ التشغيل* 🚀

📊 *الإعدادات:*
• رأس المال: `{START_BALANCE:.2f}$`
• حجم الصفقة: `{TRADE_SIZE}$`
• الحد الأقصى: `{MAX_OPEN_TRADES}` صفقة
• Stop Loss: `{STOP_LOSS_PCT*100}%`
• المطاردة: تبدأ من 1.5% → ستوب 0.5%

🔢 *ترقيم الصفقات:* ✅ مفعل
""")
    
    threading.Thread(target=trade_manager, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=report_loop, daemon=True).start()
    
    print("✅ البوت V18 يعمل...")
    
    while True:
        time.sleep(1)
