# ========================================================
# البوت V19 - النسخة النهائية (مع كل التعديلات)
# - ستوب يتحرك عند 1.5%
# - أوامر التليجرام الكاملة
# - مزامنة الصفقات من المنصة
# - مستويات مطاردة حتى 200%
# - فلتر BTC قابل للتشغيل/الإيقاف
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
# إعدادات البوت V19
# =========================
START_BALANCE = 51.40
TRADE_SIZE = 1.5
MAX_OPEN_TRADES = 30
STOP_LOSS_PCT = 0.025

# مستويات المطاردة (حتى 200%)
PROFIT_LEVELS = [
    (1.5, 0.5), (3.0, 1.5), (5.0, 3.0), (10.0, 7.0),
    (20.0, 15.0), (50.0, 45.0), (100.0, 95.0), (150.0, 145.0), (200.0, 195.0),
]

EXCLUDED_COINS = ["USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USD1", "USDT", "BGB", "BITGET"]

# فلتر BTC (مفعل افتراضياً)
BTC_FILTER_ENABLED = True

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
MEMORY_FILE = "bot_v19_state.json"

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
# مزامنة الصفقات من المنصة (لحفظ الصفقات عند إعادة التشغيل)
# =========================
def sync_trades_from_exchange():
    """جلب الصفقات المفتوحة من المنصة وإضافتها إلى الحالة مع سعر الدخول من التاريخ"""
    print("🔄 جاري مزامنة الصفقات من المنصة...")
    try:
        # جلب تاريخ الصفقات لمعرفة أسعار الدخول
        my_trades = exchange.fetch_my_trades(symbol=None, limit=200)
        latest_buy = {}
        for trade in my_trades:
            if trade['side'] == 'buy':
                symbol = trade['symbol']
                if symbol not in latest_buy or trade['timestamp'] > latest_buy[symbol]['timestamp']:
                    latest_buy[symbol] = trade
        
        # جلب الرصيد الحالي
        balance = exchange.fetch_balance()
        synced = 0
        
        for asset, amount in balance['total'].items():
            if asset == 'USDT' or amount <= 0:
                continue
            if asset in EXCLUDED_COINS:
                continue
            
            symbol = f"{asset}/USDT"
            
            # البحث عن سعر الدخول من التاريخ
            entry_price = None
            if symbol in latest_buy:
                entry_price = latest_buy[symbol]['price']
                print(f"   ✅ {symbol}: سعر الدخول من التاريخ = {entry_price}")
            else:
                ticker = exchange.fetch_ticker(symbol)
                entry_price = ticker['last']
                print(f"   ⚠️ {symbol}: لم نجد سعر الدخول، نستخدم السعر الحالي {entry_price}")
            
            if symbol not in state['active_trades']:
                state['trade_count'] += 1
                trade_id = state['trade_count']
                state['active_trades'][symbol] = {
                    "entry": entry_price,
                    "max_p": entry_price,
                    "trade_id": trade_id,
                    "synced": True
                }
                synced += 1
        
        if synced > 0:
            save_state()
            send_telegram(f"🔄 تم استيراد {synced} صفقة من المنصة")
            print(f"✅ تمت المزامنة: {synced} صفقة")
        else:
            print("✅ لا توجد صفقات مفتوحة للمزامنة")
            
    except Exception as e:
        print(f"❌ خطأ في المزامنة: {e}")

# =========================
# مؤشرات يدوية
# =========================
def calculate_ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def is_btc_uptrend(df_btc):
    global BTC_FILTER_ENABLED
    if not BTC_FILTER_ENABLED:
        return True
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
    df['h_max'] = df['high'].rolling(50).max()
    df['l_min'] = df['low'].rolling(50).min()
    df['fib_deep'] = df['h_max'] - (0.786 * (df['h_max'] - df['l_min']))
    df['gate_35'] = df['l_min'] + (0.35 * (df['h_max'] - df['l_min']))
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
    """حساب الستوب - يتحرك عند 1.49%"""
    fixed_stop = entry_price * (1 - STOP_LOSS_PCT)
    
    if current_pnl < 1.49:
        return fixed_stop
    
    final_stop = fixed_stop
    
    if current_pnl >= 1.49:
        final_stop = max(final_stop, max_price * 0.995)
    if current_pnl >= 2.99:
        final_stop = max(final_stop, max_price * 0.985)
    if current_pnl >= 4.99:
        final_stop = max(final_stop, max_price * 0.97)
    if current_pnl >= 9.99:
        final_stop = max(final_stop, max_price * 0.93)
    if current_pnl >= 19.99:
        final_stop = max(final_stop, max_price * 0.85)
    
    return final_stop

# =========================
# تحليل وشراء عملة
# =========================
def analyze_coin(symbol):
    try:
        if not symbol.endswith('/USDT'):
            symbol = f"{symbol.upper()}/USDT"
        
        df = fetch_ohlcv(symbol, limit=200)
        if df is None or len(df) < 100:
            return False, "بيانات غير كافية"
        
        df = calculate_fibonacci(df)
        
        if check_entry(df, len(df)-1):
            gate_35 = df['gate_35'].iloc[-1]
            current_price = df['close'].iloc[-1]
            if current_price > gate_35:
                return True, f"✅ صالحة للشراء"
            else:
                return False, f"❌ السعر تحت gate"
        else:
            return False, f"❌ لا تحقق شروط Fibonacci"
    except Exception as e:
        return False, f"❌ خطأ: {e}"

def buy_coin(symbol):
    try:
        if not symbol.endswith('/USDT'):
            symbol = f"{symbol.upper()}/USDT"
        
        if symbol in state['active_trades']:
            return False, "العملة مفتوحة بالفعل"
        
        if len(state['active_trades']) >= MAX_OPEN_TRADES:
            return False, "الحد الأقصى ممتلئ"
        
        is_valid, msg = analyze_coin(symbol)
        if not is_valid:
            return False, msg
        
        exchange.create_market_buy_order(symbol, None, params={'cost': TRADE_SIZE})
        price = exchange.fetch_ticker(symbol)['last']
        
        state['trade_count'] += 1
        trade_id = state['trade_count']
        
        state['active_trades'][symbol] = {"entry": price, "max_p": price, "trade_id": trade_id}
        save_state()
        
        return True, f"🏹 تم شراء {symbol} بسعر {price:.8f}$ (صفقة #{trade_id})"
    except Exception as e:
        return False, f"❌ فشل الشراء: {e}"

# =========================
# معالج رسائل التليجرام
# =========================
def telegram_listener():
    global BTC_FILTER_ENABLED
    last_update_id = 0
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 30}
            response = requests.get(url, params=params, timeout=35)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok') and data.get('result'):
                    for update in data['result']:
                        last_update_id = update['update_id']
                        
                        if 'message' in update and 'text' in update['message']:
                            text = update['message']['text'].strip()
                            chat_id = update['message']['chat']['id']
                            
                            # ===== الأوامر =====
                            if text.startswith('/'):
                                cmd = text.lower().split()
                                
                                # /profit
                                if cmd[0] == '/profit':
                                    balance = exchange.fetch_balance()
                                    usdt = balance['total'].get('USDT', 0)
                                    trades_value = 0
                                    for s, t in state['active_trades'].items():
                                        try:
                                            price = exchange.fetch_ticker(s)['last']
                                            amt = balance['total'].get(s.split('/')[0], 0)
                                            trades_value += amt * price
                                        except:
                                            pass
                                    total = usdt + trades_value
                                    profit = total - START_BALANCE
                                    profit_pct = (profit / START_BALANCE) * 100
                                    send_telegram(f"💰 *الربح:* {profit:+.2f}$ ({profit_pct:+.2f}%)")
                                
                                # /close all
                                elif cmd[0] == '/close' and len(cmd) > 1 and cmd[1] == 'all':
                                    for symbol in list(state['active_trades'].keys()):
                                        try:
                                            coin = symbol.split('/')[0]
                                            amt = exchange.fetch_balance()['total'].get(coin, 0)
                                            if amt > 0:
                                                exchange.create_market_sell_order(symbol, amt)
                                        except:
                                            pass
                                    state['active_trades'] = {}
                                    save_state()
                                    send_telegram("✅ تم إغلاق *جميع الصفقات*")
                                
                                # /close BTC
                                elif cmd[0] == '/close' and len(cmd) > 1:
                                    symbol = f"{cmd[1].upper()}/USDT"
                                    if symbol in state['active_trades']:
                                        try:
                                            coin = symbol.split('/')[0]
                                            amt = exchange.fetch_balance()['total'].get(coin, 0)
                                            if amt > 0:
                                                exchange.create_market_sell_order(symbol, amt)
                                            del state['active_trades'][symbol]
                                            save_state()
                                            send_telegram(f"✅ تم إغلاق {symbol}")
                                        except:
                                            send_telegram(f"❌ فشل إغلاق {symbol}")
                                    else:
                                        send_telegram(f"❌ {symbol} غير موجود")
                                
                                # /stop 0.5
                                elif cmd[0] == '/stop' and len(cmd) > 1:
                                    try:
                                        new_stop_pct = float(cmd[1]) / 100
                                        count = 0
                                        for symbol, t in state['active_trades'].items():
                                            t['custom_stop'] = t['entry'] * (1 - new_stop_pct)
                                            count += 1
                                        save_state()
                                        send_telegram(f"✅ تم تعديل ستوب {count} صفقة إلى {cmd[1]}%")
                                    except:
                                        send_telegram("❌ صيغة خاطئة. استخدم: /stop 0.5")
                                
                                # /stop MET 0.5
                                elif cmd[0] == '/stop' and len(cmd) > 2:
                                    try:
                                        symbol = f"{cmd[1].upper()}/USDT"
                                        new_stop_pct = float(cmd[2]) / 100
                                        if symbol in state['active_trades']:
                                            state['active_trades'][symbol]['custom_stop'] = state['active_trades'][symbol]['entry'] * (1 - new_stop_pct)
                                            save_state()
                                            send_telegram(f"✅ تم تعديل ستوب {symbol} إلى {cmd[2]}%")
                                        else:
                                            send_telegram(f"❌ {symbol} غير موجود")
                                    except:
                                        send_telegram("❌ صيغة خاطئة. استخدم: /stop MET 0.5")
                                
                                # /status
                                elif cmd[0] == '/status':
                                    msg = f"📊 *ملخص*\n"
                                    msg += f"• صفقات مفتوحة: {len(state['active_trades'])}/{MAX_OPEN_TRADES}\n"
                                    msg += f"• ✅ ربح: {state['wins']} | ❌ خسارة: {state['losses']}\n"
                                    total = state['wins'] + state['losses']
                                    if total > 0:
                                        msg += f"• 📈 نسبة الربح: {(state['wins']/total)*100:.1f}%\n"
                                    send_telegram(msg)
                                
                                # /trades
                                elif cmd[0] == '/trades':
                                    if not state['active_trades']:
                                        send_telegram("📭 لا توجد صفقات مفتوحة")
                                    else:
                                        balance = exchange.fetch_balance()
                                        msg = f"📊 *الصفقات المفتوحة ({len(state['active_trades'])}/{MAX_OPEN_TRADES}):*\n\n"
                                        for s, t in list(state['active_trades'].items()):
                                            try:
                                                price = exchange.fetch_ticker(s)['last']
                                                entry = t['entry']
                                                pnl = (price - entry) / entry * 100
                                                msg += f"• #{t['trade_id']} `{s}`: دخل `{entry:.8f}`، الآن `{price:.8f}` ({pnl:+.2f}%)\n"
                                            except:
                                                msg += f"• #{t['trade_id']} `{s}`: خطأ\n"
                                        send_telegram(msg)
                                
                                # /balance
                                elif cmd[0] == '/balance':
                                    balance = exchange.fetch_balance()
                                    usdt = balance['total'].get('USDT', 0)
                                    trades_value = 0
                                    for s in state['active_trades']:
                                        try:
                                            coin = s.split('/')[0]
                                            price = exchange.fetch_ticker(s)['last']
                                            trades_value += balance['total'].get(coin, 0) * price
                                        except:
                                            pass
                                    total = usdt + trades_value
                                    profit = total - START_BALANCE
                                    profit_pct = (profit / START_BALANCE) * 100
                                    msg = f"💰 *الرصيد*\n"
                                    msg += f"💵 كاش: `{usdt:.2f}$`\n"
                                    msg += f"📦 عملات: `{trades_value:.2f}$`\n"
                                    msg += f"📊 إجمالي: `{total:.2f}$`\n"
                                    msg += f"📈 الربح: `{profit:+.2f}$` ({profit_pct:+.2f}%)"
                                    send_telegram(msg)
                                
                                # /btc on/off
                                elif cmd[0] == '/btc' and len(cmd) > 1:
                                    if cmd[1] == 'on':
                                        BTC_FILTER_ENABLED = True
                                        send_telegram("✅ تم *تفعيل* فلتر BTC")
                                    elif cmd[1] == 'off':
                                        BTC_FILTER_ENABLED = False
                                        send_telegram("⚠️ تم *تعطيل* فلتر BTC")
                                    else:
                                        send_telegram("❌ استخدم: /btc on أو /btc off")
                                
                                else:
                                    send_telegram("❌ أمر غير معروف. الأوامر:\n/profit\n/close all\n/close BTC\n/stop 0.5\n/stop MET 0.5\n/status\n/trades\n/balance\n/btc on/off")
                            
                            # ===== عملة عادية =====
                            else:
                                coin = text.upper().replace('/USDT', '').strip()
                                if len(coin) < 20 and not coin.startswith('/'):
                                    success, msg = buy_coin(coin)
                                    send_telegram(msg)
                            
            time.sleep(1)
        except Exception as e:
            print(f"خطأ: {e}")
            time.sleep(5)

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
                
                if 'custom_stop' in t and t['custom_stop'] > 0:
                    stop_price = t['custom_stop']
                else:
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
                print(f"خطأ: {e}")
        time.sleep(20)

# =========================
# فاحص الصفقات (دخول تلقائي)
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
                            
                            state['trade_count'] += 1
                            trade_id = state['trade_count']
                            
                            state['active_trades'][s] = {"entry": price, "max_p": price, "trade_id": trade_id}
                            save_state()
                            
                            send_telegram(f"""
🏹 *دخول تلقائي* 🏹
• الصفقة رقم: `{trade_id}`
• العملة: `{s}`
• سعر الدخول: `{price:.8f}$`
""")
                            break
                                
        except Exception as e:
            print(f"خطأ: {e}")
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
📊 *تقرير V19* 📊
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
• 🔢 إجمالي الصفقات: `{state['trade_count']}`

🔹 *الصفقات المفتوحة:*
{chr(10).join(details) if details else 'لا توجد صفقات مفتوحة'}

⚙️ *الأوامر:* /profit, /close all, /close BTC, /stop 0.5, /trades, /balance, /btc on/off
"""
            send_telegram(msg)
            
        except Exception as e:
            print(f"خطأ: {e}")
        time.sleep(900)

# =========================
# التشغيل
# =========================
if __name__ == "__main__":
    # مزامنة الصفقات المفتوحة من المنصة
    sync_trades_from_exchange()
    
    send_telegram(f"""
🚀 *البوت V19 (النسخة النهائية)* 🚀

📊 *الإعدادات:*
• رأس المال: `{START_BALANCE:.2f}$`
• حجم الصفقة: `{TRADE_SIZE}$`
• الحد الأقصى: `{MAX_OPEN_TRADES}` صفقة
• Stop Loss: `{STOP_LOSS_PCT*100}%`
• فلتر BTC: مفعل (`/btc off` لتعطيل)

📈 *مستويات المطاردة:* 1.5%→0.5%, 3%→1.5%, 5%→3%, 10%→7%, 20%→15%, 50%→45%, 100%→95%, 150%→145%, 200%→195%

🤖 *الأوامر المتاحة:*
• `/profit` - عرض الربح
• `/close all` - إغلاق الكل
• `/close BTC` - إغلاق صفقة
• `/stop 0.5` - تعديل ستوب الكل
• `/stop MET 0.5` - تعديل ستوب صفقة
• `/trades` - عرض الصفقات
• `/balance` - عرض الرصيد
• `/btc on/off` - تشغيل/إيقاف فلتر BTC

💡 *أو أرسل اسم العملة (مثل BTC) لشرائها*
""")
    
    threading.Thread(target=trade_manager, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=report_loop, daemon=True).start()
    threading.Thread(target=telegram_listener, daemon=True).start()
    
    print("✅ البوت V19 يعمل...")
    
    while True:
        time.sleep(1)
