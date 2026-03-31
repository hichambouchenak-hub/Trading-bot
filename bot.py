# ========================================================
# البوت V12-Turbo - نسخة Railway
# يعمل 24/7 مع خادم ويب صغير لمنع التوقف
# استراتيجية Fibonacci + مطاردة من 2%
# ========================================================

import ccxt
import pandas as pd
import pandas_ta as ta
import requests
import time
import threading
import json
import os
import random
from datetime import datetime
import warnings
from flask import Flask
warnings.filterwarnings('ignore')

# =========================
# إعدادات Flask (لخادم الويب)
# =========================
app = Flask(__name__)

@app.route('/')
def home():
    return "✅ بوت التداول V12 يعمل 24/7", 200

@app.route('/health')
def health():
    return "OK", 200

def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# =========================
# إعدادات الربط (ضع مفاتيحك هنا)
# =========================
API_KEY = "bg_bd03b4ab2190f5aaaf0965217f96b23b"
SECRET = "3de47da68bb3828436fd4ab7e79918247a1677b5e92b2a3086a2a81ad68f34fc"
PASSWORD = "Hich1978"

TELEGRAM_TOKEN = "8681138761:AAEUVPSzkPzrwLMGcwotBbISQ3D1b-QZds8" # ضع التوكن هنا
CHAT_ID = "5809146953"

# =========================
# إعدادات V12 النهائية
# =========================
START_BALANCE = 55.00              # رأس المال الفعلي
TRADE_SIZE = 2.5                   # 2.5$ لكل صفقة
MAX_OPEN_TRADES = 20               # 20 صفقة مفتوحة

# مستويات المطاردة V12 (تبدأ من 2%)
PROFIT_LEVELS = [
    (2.0, 1.0),   # عند 2% ربح → ستوب 1%
    (3.5, 2.0),   # عند 3.5% ربح → ستوب 2%
    (7.0, 5.0),   # عند 7% ربح → ستوب 5%
    (15.0, 12.0), # عند 15% ربح → ستوب 12%
    (30.0, 25.0), # عند 30% ربح → ستوب 25%
]

# Stop Loss ثابت
STOP_LOSS_PCT = 0.025              # 2.5%

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
MEMORY_FILE = "bot_v12_state.json"

def load_state():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, 'r') as f:
                data = json.load(f)
                for key in ["active_trades", "wins", "losses"]:
                    if key not in data:
                        data[key] = {} if key == "active_trades" else 0
                return data
        except:
            pass
    return {"active_trades": {}, "wins": 0, "losses": 0}

def save_state():
    try:
        with open(MEMORY_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print(f"خطأ في حفظ الحالة: {e}")

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
# الاستراتيجية والإدارة
# =========================

def get_market_data(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, '1h', limit=100)
        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
        df['h_max'] = df['high'].rolling(50).max()
        df['l_min'] = df['low'].rolling(50).min()
        df['fib_deep'] = df['h_max'] - (0.764 * (df['h_max'] - df['l_min']))
        df['gate_382'] = df['l_min'] + (0.382 * (df['h_max'] - df['l_min']))
        return df
    except:
        return None

def calculate_stop(entry_price, max_price, current_pnl):
    fixed_stop = entry_price * (1 - STOP_LOSS_PCT)
    
    if current_pnl < 2.0:
        return fixed_stop
    
    final_stop = fixed_stop
    for trigger, trail_dist in PROFIT_LEVELS:
        if current_pnl >= trigger:
            final_stop = max(final_stop, max_price * (1 - (trail_dist / 100)))
        else:
            break
    
    return final_stop

def is_btc_uptrend(df_btc):
    try:
        if df_btc is None or len(df_btc) < 50:
            return True
        ema50 = ta.ema(df_btc['close'], 50)
        if ema50 is None:
            return True
        return df_btc['close'].iloc[-1] > ema50.iloc[-1]
    except:
        return True

def check_entry(df, current_index):
    if current_index < 50:
        return False
    
    fib_deep = df['fib_deep'].iloc[current_index]
    gate_382 = df['gate_382'].iloc[current_index]
    low_20 = df['low'].iloc[current_index-20:current_index].min()
    current_close = df['close'].iloc[current_index]
    
    return low_20 <= fib_deep and current_close > gate_382

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
• العملة: `{symbol}`
• الربح المحقق: `{pnl:+.2f}%`
• أعلى ربح وصل: `{max_gain:.1f}%`
""")
                        del state['active_trades'][symbol]
                        save_state()
                        
            except Exception as e:
                print(f"خطأ في trade_manager: {e}")
        time.sleep(20)

# =========================
# فاحص الصفقات الجديدة
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
                        
                        df = get_market_data(s)
                        if df is not None and len(df) >= 20:
                            if check_entry(df, len(df)-1):
                                exchange.create_market_buy_order(s, None, params={'cost': TRADE_SIZE})
                                price = exchange.fetch_ticker(s)['last']
                                state['active_trades'][s] = {"entry": price, "max_p": price}
                                save_state()
                                
                                send_telegram(f"""
🏹 *دخول V12* 🏹
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
# التقرير الدوري (كل 15 دقيقة)
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
                    price = exchange.fetch_ticker(s)['last']
                    pnl = (price - t['entry']) / t['entry'] * 100
                    max_gain = (t['max_p'] - t['entry']) / t['entry'] * 100
                    details.append(f"• `{s}`: {pnl:+.2f}% (Max: {max_gain:.1f}%)")
                    trades_value += (balance['total'].get(s.split('/')[0], 0) * price)
                except:
                    pass

            total = usdt_now + trades_value
            growth = ((total - START_BALANCE) / START_BALANCE) * 100
            
            total_trades = state['wins'] + state['losses']
            win_rate = (state['wins'] / total_trades * 100) if total_trades > 0 else 0
            
            msg = f"""
📊 *تقرير V12 الدوري* 📊
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

🔹 *الصفقات المفتوحة:*
{chr(10).join(details) if details else 'لا توجد صفقات مفتوحة'}

⚙️ *Stop Loss:* {STOP_LOSS_PCT*100}% | *المطاردة:* تبدأ من 2%
"""
            send_telegram(msg)
            
        except Exception as e:
            print(f"خطأ في report: {e}")
        time.sleep(900)

# =========================
# رسالة بدء التشغيل
# =========================
def send_startup_message():
    send_telegram(f"""
🚀 *V12-Turbo: البوت يعمل على Railway* 🚀

📊 *الإعدادات:*
• رأس المال: `{START_BALANCE:.2f}$`
• حجم الصفقة: `{TRADE_SIZE}$`
• الحد الأقصى: `{MAX_OPEN_TRADES}` صفقة
• Stop Loss: `{STOP_LOSS_PCT*100}%` ثابت
• المطاردة: تبدأ من 2% ربح

💾 *حفظ الحالة:* ملف محلي ✅
🔁 تقرير كل 15 دقيقة
⚡ *يعمل 24/7 دون انقطاع*
""")

# =========================
# التشغيل الرئيسي
# =========================
if __name__ == "__main__":
    # تشغيل خادم الويب في ثريد منفصل
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    
    # إرسال رسالة بدء التشغيل
    send_startup_message()
    
    # عرض عدد الصفقات المستعادة
    if state['active_trades']:
        send_telegram(f"🔄 *تم استعادة {len(state['active_trades'])} صفقة*")
    
    # تشغيل الثريدات
    threading.Thread(target=trade_manager, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=report_loop, daemon=True).start()
    
    print("✅ البوت V12-Turbo يعمل على Railway...")
    print(f"📊 صفقات مستعادة: {len(state['active_trades'])}")
    
    while True:
        time.sleep(1)
