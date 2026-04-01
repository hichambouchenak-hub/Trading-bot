# ========================================================
# البوت V13 - التحليل البصري (بدون pandas_ta)
# ========================================================

import ccxt
import pandas as pd
import requests
import time
import threading
import json
import os
import random
import io
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# =========================
# مؤشرات يدوية (بدون pandas_ta)
# =========================

def calculate_ema(series, length):
    """EMA يدوي"""
    return series.ewm(span=length, adjust=False).mean()

def calculate_rsi(series, length=14):
    """RSI يدوي"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=length).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=length).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calculate_atr(df, length=14):
    """ATR يدوي"""
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=length).mean()
    return atr

# =========================
# إعدادات الربط
# =========================
API_KEY = "bg_bd03b4ab2190f5aaaf0965217f96b23b"
SECRET = "3de47da68bb3828436fd4ab7e79918247a1677b5e92b2a3086a2a81ad68f34fc"
PASSWORD = "Hich1978"

TELEGRAM_TOKEN = "8681138761:AAEUVPSzkPzrwLMGcwotBbISQ3D1b-QZds8"
CHAT_ID = "5809146953"

# =========================
# إعدادات البوت V13
# =========================
START_BALANCE = 55.00
TRADE_SIZE = 2.5
MAX_OPEN_TRADES = 20
SWAP_HOURS = 6
STOP_LOSS_PCT = 0.025

# مستويات المطاردة (تبدأ من 2%)
PROFIT_LEVELS = [
    (2.0, 1.0),   # عند 2% ربح → ستوب 1%
    (3.5, 2.0),   # عند 3.5% ربح → ستوب 2%
    (7.0, 5.0),   # عند 7% ربح → ستوب 5%
    (15.0, 12.0), # عند 15% ربح → ستوب 12%
    (30.0, 25.0), # عند 30% ربح → ستوب 25%
]

# العملات المستبعدة
EXCLUDED_COINS = ["USDC", "DAI", "FDUSD", "TUSD", "USDE", "PYUSD", "USD1", "USDT", "BGB", "BITGET"]

# =========================
# إعدادات GitHub للصور
# =========================
GITHUB_USERNAME = "hichambouchenak-hub"
GITHUB_REPO = "candle-patterns"

# متغيرات للصور
up_patterns = []
down_patterns = []
patterns_loaded = False

def load_patterns_from_github():
    """تحميل كل صور PNG من GitHub"""
    global up_patterns, down_patterns, patterns_loaded
    
    print("📥 تحميل صور الأنماط من GitHub...")
    
    def get_images(folder):
        images = []
        url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{folder}"
        try:
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                files = response.json()
                for file in files:
                    if file['name'].lower().endswith('.png'):
                        img_url = file['download_url']
                        img_response = requests.get(img_url, timeout=10)
                        if img_response.status_code == 200:
                            img = Image.open(io.BytesIO(img_response.content))
                            img = img.resize((200, 150)).convert('L')
                            images.append(np.array(img))
                print(f"   ✅ {len(images)} صورة من {folder}")
        except Exception as e:
            print(f"   ❌ فشل تحميل {folder}: {e}")
        return images
    
    up_patterns = get_images("up")
    down_patterns = get_images("down")
    patterns_loaded = True
    print(f"✅ تم تحميل {len(up_patterns)} صورة UP و {len(down_patterns)} صورة DOWN")

def analyze_chart_visually(chart_buf):
    """مقارنة صورة الشارت مع الصور المخزنة"""
    try:
        chart = Image.open(chart_buf)
        chart = chart.resize((200, 150)).convert('L')
        chart_arr = np.array(chart)
        
        up_scores = [ssim(chart_arr, p, data_range=255) for p in up_patterns]
        down_scores = [ssim(chart_arr, p, data_range=255) for p in down_patterns]
        
        avg_up = np.mean(up_scores) if up_scores else 0
        avg_down = np.mean(down_scores) if down_scores else 0
        
        return avg_up, avg_down
    except Exception as e:
        print(f"خطأ في التحليل البصري: {e}")
        return 0, 0

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
MEMORY_FILE = "bot_v13_state.json"

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

def create_chart_image(df, symbol):
    """رسم الشارت كصورة"""
    import matplotlib.pyplot as plt
    import mplfinance as mpf
    
    window = df.tail(50).copy()
    window.columns = ['Open', 'High', 'Low', 'Close', 'Volume']
    
    fig, ax = plt.subplots(figsize=(12, 6))
    mpf.plot(window, type='candle', style='charles', ax=ax, volume=False)
    ax.set_title(f"{symbol} - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=80)
    buf.seek(0)
    plt.close(fig)
    return buf

def calculate_fibonacci(df):
    df['h_max'] = df['high'].rolling(50).max()
    df['l_min'] = df['low'].rolling(50).min()
    df['fib_deep'] = df['h_max'] - (0.764 * (df['h_max'] - df['l_min']))
    df['gate_382'] = df['l_min'] + (0.382 * (df['h_max'] - df['l_min']))
    return df

def check_entry_v12(df, current_index):
    if current_index < 50: return False
    fib_deep = df['fib_deep'].iloc[current_index]
    gate_382 = df['gate_382'].iloc[current_index]
    low_20 = df['low'].iloc[current_index-20:current_index].min()
    current_close = df['close'].iloc[current_index]
    return low_20 <= fib_deep and current_close > gate_382

def calculate_stop(entry_price, max_price, current_pnl):
    """حساب الستوب مع المطاردة"""
    fixed_stop = entry_price * (1 - STOP_LOSS_PCT)
    
    # إذا لم نصل إلى 2% ربح، نستخدم الستوب الثابت
    if current_pnl < 2.0:
        return fixed_stop
    
    # نبدأ المطاردة
    final_stop = fixed_stop
    for trigger, trail_dist in PROFIT_LEVELS:
        if current_pnl >= trigger:
            final_stop = max(final_stop, max_price * (1 - (trail_dist / 100)))
        else:
            break
    
    return final_stop

def is_btc_uptrend(df_btc):
    try:
        if df_btc is None or len(df_btc) < 50: return True
        ema50 = calculate_ema(df_btc['close'], 50)
        if ema50 is None: return True
        return df_btc['close'].iloc[-1] > ema50.iloc[-1]
    except: return True

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
                
                # حساب الستوب مع تمرير pnl
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
• الربح المحقق: `{pnl:+.2f}%` (+{pnl_usd:.2f}$)
• أعلى ربح وصل: `{max_gain:.1f}%`
""")
                        del state['active_trades'][symbol]
                        save_state()
                        
            except Exception as e:
                print(f"خطأ في trade_manager: {e}")
        time.sleep(20)

# =========================
# scanner مع التحليل البصري
# =========================
def scanner():
    global patterns_loaded
    btc_df = None
    
    # تحميل الصور مرة واحدة عند بدء التشغيل
    if not patterns_loaded:
        load_patterns_from_github()
    
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
                        
                        # شرط Fibonacci
                        if check_entry_v12(df, len(df)-1):
                            # التحليل البصري
                           # chart_buf = create_chart_image(df, s)
                            #avg_up, avg_down = analyze_chart_visually(chart_buf)
                            
                            # القرار: تشابه مع UP > DOWN وتشابه > 55%
                             if avg_up > avg_down and avg_up > 0.55:
                                exchange.create_market_buy_order(s, None, params={'cost': TRADE_SIZE})
                                price = exchange.fetch_ticker(s)['last']
                                state['active_trades'][s] = {"entry": price, "max_p": price}
                                save_state()
                                
                                send_telegram(f"""
🏹 *دخول V13 (بصري)* 🏹
• العملة: `{s}`
• سعر الدخول: `{price:.8f}$`
• تحليل بصري: UP {avg_up:.1%} > DOWN {avg_down:.1%}
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
                    details.append(f"• `{s}`: {pnl:+.2f}% (Max: {max_gain:.1f}%)")
                except:
                    pass
            
            total = usdt_now + trades_value
            growth = ((total - START_BALANCE) / START_BALANCE) * 100
            total_trades = state['wins'] + state['losses']
            win_rate = (state['wins'] / total_trades * 100) if total_trades > 0 else 0
            
            msg = f"""
📊 *تقرير V13 (بصري)* 📊
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
🚀 *البوت V13 (التحليل البصري) بدأ التشغيل* 🚀

📊 *الإعدادات:*
• رأس المال: `{START_BALANCE:.2f}$`
• حجم الصفقة: `{TRADE_SIZE}$`
• الحد الأقصى: `{MAX_OPEN_TRADES}` صفقة
• Stop Loss: `{STOP_LOSS_PCT*100}%`
• المطاردة: تبدأ من 2%
• التحليل البصري: ✅ مفعل (300 صورة مرجعية)

💾 *حفظ الحالة:* ملف محلي ✅
🔁 تقرير كل 15 دقيقة
""")
    
    threading.Thread(target=trade_manager, daemon=True).start()
    threading.Thread(target=scanner, daemon=True).start()
    threading.Thread(target=report_loop, daemon=True).start()
    
    print("✅ البوت V13 يعمل...")
    print("📊 التحليل البصري: جاهز")
    
    while True:
        time.sleep(1)
