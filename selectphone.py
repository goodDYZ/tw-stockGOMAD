import os
import re
import datetime
import time
import requests
import json
import io
import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib
matplotlib.use('Agg') # 避免雲端伺服器 GUI 報錯
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import streamlit as st
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# 設定字型支援中文（若雲端環境無標楷體，使用預設字型並忽略警告）
plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial Unicode MS', 'Microsoft JhengHei']
plt.rcParams['axes.unicode_minus'] = False

# ==========================================
# ⚙️ 網頁版面設定
# ==========================================
st.set_page_config(
    page_title="台股智慧選股與靜態圖表系統",
    page_icon="📈",
    layout="wide"
)

CHART_DIR = "./charts"
if not os.path.exists(CHART_DIR):
    os.makedirs(CHART_DIR)

# ==========================================
# 🏛 1. 籌碼資料擷取模組
# ==========================================
def fetch_chips_twse_official():
    chip_dict = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    valid_days_count = 0
    
    for i in range(15):
        target_date = datetime.now() - timedelta(days=i)
        if target_date.weekday() in (5, 6):
            continue

        date_twse = target_date.strftime("%Y%m%d")
        roc_year = target_date.year - 1911
        date_tpex = f"{roc_year}/{target_date.month:02d}/{target_date.day:02d}"
        
        url_twse = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_twse}&selectType=ALL&response=json"
        is_valid_trading_day = False
        
        try:
            res = requests.get(url_twse, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if data.get("stat") == "OK" and "data" in data and "fields" in data:
                    is_valid_trading_day = True
                    fields = data["fields"]
                    foreign_idx, trust_idx = -1, -1
                    
                    for idx, field in enumerate(fields):
                        if "外陸資買賣超" in field or "外資買賣超" in field:
                            foreign_idx = idx
                        elif "投信買賣超" in field:
                            trust_idx = idx
                    
                    if foreign_idx == -1: foreign_idx = 7
                    if trust_idx == -1: trust_idx = 10

                    for row in data["data"]:
                        stock_id = row[0].strip()
                        if len(stock_id) == 4 and stock_id.isdigit():
                            try:
                                foreign_buy = int(row[foreign_idx].replace(',', '')) // 1000
                                trust_buy = int(row[trust_idx].replace(',', '')) // 1000
                                if stock_id not in chip_dict:
                                    chip_dict[stock_id] = {'foreign': 0, 'trust': 0}
                                chip_dict[stock_id]['foreign'] += foreign_buy
                                chip_dict[stock_id]['trust'] += trust_buy
                            except (ValueError, IndexError):
                                continue
        except Exception:
            pass

        if not is_valid_trading_day:
            continue

        url_tpex_csv = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=AL&t=D&d={date_tpex}&p=1&st=1&s=0,asc,0&o=csv"
        try:
            res = requests.get(url_tpex_csv, headers=headers, timeout=10)
            if res.status_code == 200:
                content = res.content.decode('big5', errors='ignore')
                for line in content.splitlines():
                    row = [col.strip().replace('"', '') for col in line.split(',')]
                    if len(row) >= 11:
                        stock_id = row[0]
                        if len(stock_id) == 4 and stock_id.isdigit():
                            try:
                                foreign_buy = int(row[7].replace(',', '')) // 1000
                                trust_buy = int(row[10].replace(',', '')) // 1000
                                if stock_id not in chip_dict:
                                    chip_dict[stock_id] = {'foreign': 0, 'trust': 0}
                                chip_dict[stock_id]['foreign'] += foreign_buy
                                chip_dict[stock_id]['trust'] += trust_buy
                            except (ValueError, IndexError):
                                continue
        except Exception:
            pass

        time.sleep(0.3)
        valid_days_count += 1
        if valid_days_count == 5:
            break

    return chip_dict

# ==========================================
# 🌐 2. 股票清單擷取模組
# ==========================================
def get_stock_mapping():
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    stock_map = {}

    try:
        url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        res = requests.get(url_twse, headers=headers, timeout=10)
        for item in res.json():
            sid = item.get('Code', '').strip()
            if len(sid) == 4 and sid.isdigit():
                stock_map[sid] = f"{sid}.TW"
                
        url_tpex = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
        res_tpex = requests.get(url_tpex, headers=headers, timeout=10)
        for item in res_tpex.json():
            sid = item.get('SecuritiesCompanyCode', '').strip()
            if len(sid) == 4 and sid.isdigit():
                stock_map[sid] = f"{sid}.TWO"
                
        if len(stock_map) > 1000:
            return stock_map
    except Exception:
        pass

    try:
        url_github = "https://raw.githubusercontent.com/datasets/invest-taiwan/main/stocks.json"
        res = requests.get(url_github, headers=headers, timeout=10)
        for item in res.json():
            sid = str(item.get('code', '')).strip()
            market = item.get('market', '')
            if len(sid) == 4 and sid.isdigit():
                suffix = ".TW" if market == "TWSE" else ".TWO"
                stock_map[sid] = f"{sid}{suffix}"
        if len(stock_map) > 1000:
            return stock_map
    except Exception:
        pass

    for s in ['2330', '2317', '2454', '2308', '2382', '2356', '2881']:
        stock_map[s] = f"{s}.TW"
    for s in ['3231', '8299', '6274']:
        stock_map[s] = f"{s}.TWO"
    return stock_map

# ==========================================
# 💬 3. 聲量爬蟲與靜態圖表繪製引擎
# ==========================================
def fetch_social_sentiment(target_stocks):
    stock_info_map = {str(sid).strip(): '' for sid in target_stocks}
    sentiment_counts = {sid: {'ptt': 0, 'dcard': 0, 'score': 0.0} for sid in stock_info_map}
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9"
    })

    stock_patterns = {sid: [re.compile(r'(?<!\d)' + re.escape(sid) + r'(?!\d)')] for sid in stock_info_map}

    try:
        session.cookies.set('over18', '1')
        ptt_url = "https://www.ptt.cc/bbs/Stock/index.html"
        
        for _ in range(5):
            res = session.get(ptt_url, timeout=4)
            if res.status_code != 200:
                break
            soup = BeautifulSoup(res.text, 'html.parser')
            for article in soup.find_all('div', class_='r-ent'):
                title_div = article.find('div', class_='title')
                if not title_div or not title_div.find('a'):
                    continue
                title_text = title_div.text.strip()
                for sid, patterns in stock_patterns.items():
                    if any(pat.search(title_text) for pat in patterns):
                        sentiment_counts[sid]['ptt'] += 1

            paging_btn = soup.find('a', string=re.compile(r'‹\s*上頁'))
            if paging_btn and 'href' in paging_btn.attrs:
                ptt_url = f"https://www.ptt.cc{paging_btn['href']}"
            else:
                break
            time.sleep(0.1)
    except Exception:
        pass

    for sid, data in sentiment_counts.items():
        data['score'] = round(data['ptt'] * 2.5, 1)

    return sentiment_counts

def generate_static_chart(stock_id, df, info_dict):
    """使用 Matplotlib 繪製帶有重要資訊標註的高畫質靜態 K 線圖 (PNG)"""
    df_plot = df.tail(60).copy()
    
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={'height_ratios': [3, 1]})
    
    # 計算均線
    df_plot['MA5'] = df_plot['Close'].rolling(5).mean()
    df_plot['MA20'] = df_plot['Close'].rolling(20).mean()
    df_plot['MA60'] = df_plot['Close'].rolling(60).mean()
    
    # 繪製 K 線 (簡化為收盤價走勢與高低區間線)
    x = np.arange(len(df_plot))
    dates = df_plot.index
    
    # K線高低範圍
    for i in range(len(df_plot)):
        color = 'red' if df_plot['Close'].iloc[i] >= df_plot['Open'].iloc[i] else 'green'
        ax1.vlines(x[i], df_plot['Low'].iloc[i], df_plot['High'].iloc[i], color=color, linewidth=1)
        ax1.vlines(x[i], df_plot['Open'].iloc[i], df_plot['Close'].iloc[i], color=color, linewidth=3)

    # 繪製均線
    ax1.plot(x, df_plot['MA5'], label='MA5', color='orange', linewidth=1.2)
    ax1.plot(x, df_plot['MA20'], label='MA20', color='blue', linewidth=1.2)
    ax1.plot(x, df_plot['MA60'], label='MA60', color='purple', linewidth=1.2)
    
    # 標註前3日最高價
    if len(df_plot) >= 4:
        prev_3day_high = df_plot['High'].iloc[-4:-1].max()
        ax1.axhline(y=prev_3day_high, color='red', linestyle='--', linewidth=1, label=f'前3日高點: {prev_3day_high:.1f}')

    # 成交量柱狀圖
    vol_colors = ['red' if df_plot['Close'].iloc[i] >= df_plot['Open'].iloc[i] else 'green' for i in range(len(df_plot))]
    ax2.bar(x, df_plot['Volume'], color=vol_colors, alpha=0.8)
    
    # 標題與重要資訊標註
    title_text = (
        f"股票代號: {stock_id} | 收盤價: {info_dict['close']} | 成交量: {info_dict['volume']}張\n"
        f"外資5日買超: {info_dict['foreign_buy_5d']}張 | 投信5日買超: {info_dict['trust_buy_5d']}張 | PTT聲量: {info_dict.get('ptt_cnt', 0)}篇"
    )
    ax1.set_title(title_text, fontsize=11, fontweight='bold', pad=10)
    
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(True, linestyle=':', alpha=0.5)
    ax2.grid(True, linestyle=':', alpha=0.5)
    
    # X軸日期標籤
    step = max(1, len(dates) // 8)
    ax2.set_xticks(x[::step])
    ax2.set_xticklabels([d.strftime('%m/%d') for d in dates[::step]], rotation=0, fontsize=9)
    
    plt.tight_layout()
    
    # 儲存為 PNG
    filepath = os.path.join(CHART_DIR, f"{stock_id}_kline.png")
    plt.savefig(filepath, dpi=150)
    plt.close(fig)
    return filepath

# ==========================================
# 📊 4. 篩選策略引擎
# ==========================================
def evaluate_stock(stock_id, df, chip_data):
    if df is None or len(df) < 60:
        return None

    clean_sid = str(stock_id).strip().upper().replace('.TW', '').replace('.TWO', '')
    close = df['Close'].astype(float)
    high = df['High'].astype(float)
    
    raw_vol = df['Volume'].astype(float)
    volume = raw_vol if raw_vol.iloc[-5:].mean() < 10000 else raw_vol / 1000.0

    cur_close = close.iloc[-1]
    cur_vol = volume.iloc[-1]
    prev_vol = volume.iloc[-2]
    vol_ma5 = volume.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    ma60 = close.rolling(60).mean().iloc[-1]

    cond1 = cur_vol >= 200
    chip_info = chip_data.get(clean_sid, {'foreign': 0, 'trust': 0})
    foreign_buy_5d = chip_info['foreign']
    trust_buy_5d = chip_info['trust']
    cond2 = (foreign_buy_5d >= 1000) or (trust_buy_5d >= 500)

    dist_ma60_pct = ((cur_close - ma60) / ma60) * 100
    cond3 = 0.0 <= dist_ma60_pct <= 15.0
    cond4 = (cur_close > ma20) and (ma20 > ma60)
    cond5 = (cur_vol >= (prev_vol * 1.1)) or (cur_vol >= vol_ma5)
    
    prev_3day_high = high.iloc[-4:-1].max()
    cond6 = cur_close > prev_3day_high

    if cond1 and cond2 and cond3 and cond4 and cond5 and cond6:
        return {
            'stock_id': clean_sid,
            'close': cur_close,
            'volume': int(cur_vol),
            'foreign_buy_5d': foreign_buy_5d,
            'trust_buy_5d': trust_buy_5d,
            'ma60_dist_%': round(dist_ma60_pct, 2)
        }
    return None

# ==========================================
# 🌐 Streamlit 網頁主介面
# ==========================================
st.title("📈 台股智慧選股與靜態重點圖表系統")
st.markdown("點擊下方按鈕即可開始執行篩選，並在網頁上直接檢視標註重要數值的高畫質靜態 K 線圖：")

if st.button("🚀 開始執行全市場篩選", type="primary"):
    with st.spinner("正在抓取法人籌碼與歷史 K 線，並生成標註圖表，請稍候..."):
        chip_data = fetch_chips_twse_official()
        stock_map = get_stock_mapping()
        tickers = list(stock_map.values())
        
        try:
            data = yf.download(tickers, period="6mo", group_by='ticker', progress=False, threads=True)
        except Exception as e:
            st.error(f"K線下載失敗: {e}")
            st.stop()

        passed_stocks = []
        for sid, ticker in stock_map.items():
            df_stock = None
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker in data.columns.levels[0]:
                        df_stock = data[ticker].dropna()
                else:
                    df_stock = data.dropna()
            except Exception:
                continue

            if df_stock is None or df_stock.empty:
                continue

            result = evaluate_stock(sid, df_stock, chip_data)
            if result:
                passed_stocks.append((result, df_stock))

    if passed_stocks:
        st.success(f"🎉 篩選完成！共找到 {len(passed_stocks)} 支符合條件的標的：")
        
        passed_ids = [item[0]['stock_id'] for item in passed_stocks]
        sentiment_data = fetch_social_sentiment(passed_ids)
        
        final_results = []
        stock_pngs = {}
        for res, df_stock in passed_stocks:
            sid = res['stock_id']
            s_info = sentiment_data.get(sid, {'ptt': 0, 'dcard': 0, 'score': 0.0})
            res['ptt_cnt'] = s_info['ptt']
            res['sentiment_score'] = s_info['score']
            final_results.append(res)
            
            # 生成帶有標註的靜態 PNG 圖表
            png_path = generate_static_chart(sid, df_stock, res)
            stock_pngs[sid] = png_path

        res_df = pd.DataFrame(final_results)
        if 'sentiment_score' in res_df.columns and 'foreign_buy_5d' in res_df.columns:
            res_df = res_df.sort_values(by=['sentiment_score', 'foreign_buy_5d'], ascending=False)

        # 顯示表格
        st.dataframe(res_df, use_container_width=True)

        st.markdown("---")
        st.subheader("🖼️ 入選個股靜態 K 線圖檢視（含重要數值標註）")
        
        for sid, png_path in stock_pngs.items():
            st.markdown(f"### 📌 股票代號: {sid}")
            st.image(png_path, use_container_width=True)
            st.markdown("---")

    else:
        st.info("ℹ 今日全市場無符合 6 大嚴格條件之標的。")
