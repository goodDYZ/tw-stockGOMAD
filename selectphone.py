import os
import re
import datetime
import time
import requests
import json
import pandas as pd
import numpy as np
import yfinance as yf
import mplfinance as mpf
import streamlit as st
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# ==========================================
# ⚙️ 1. Streamlit 頁面與全域設定 (手機最佳化)
# ==========================================
st.set_page_config(
    page_title="台股雷達與聲量篩選系統",
    page_icon="📈",
    layout="centered"
)

CHART_DIR = "./charts"
if not os.path.exists(CHART_DIR):
    os.makedirs(CHART_DIR)

st.title("📈 台股精選與 PTT 聲量雷達")
st.caption("結合三大法人籌碼 + 6大技術面指標 + PTT 股票版即時聲量")

# ==========================================
# 🏛 2. 籌碼資料擷取模組 (帶 Streamlit 快取機制)
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)  # 快取 1 小時，避免重複爬證交所
def fetch_chips_twse_official():
    chip_dict = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    valid_days_count = 0
    
    for i in range(15):
        target_date = datetime.now() - timedelta(days=i)
        if target_date.weekday() in (5, 6):
            continue

        date_twse = target_date.strftime("%Y%m%d")
        roc_year = target_date.year - 1911
        date_tpex = f"{roc_year}/{target_date.month:02d}/{target_date.day:02d}"

        # 1. 證交所 TWSE
        url_twse = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_twse}&selectType=ALL&response=json"
        is_valid_trading_day = False
        
        try:
            res = requests.get(url_twse, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if data.get("stat") == "OK" and "data" in data and "fields" in data:
                    is_valid_trading_day = True
                    fields = data["fields"]
                    
                    foreign_idx = next((idx for idx, f in enumerate(fields) if "外陸資買賣超" in f or "外資買賣超" in f), 7)
                    trust_idx = next((idx for idx, f in enumerate(fields) if "投信買賣超" in f), 10)

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

        # 2. 櫃買中心 TPEx
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

        time.sleep(0.2)
        valid_days_count += 1
        if valid_days_count == 5:
            break

    return chip_dict

# ==========================================
# 🌐 3. 股票代碼對應表
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def get_stock_mapping():
    headers = {"User-Agent": "Mozilla/5.0"}
    stock_map = {}
    try:
        url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        for item in requests.get(url_twse, headers=headers, timeout=10).json():
            sid = item.get('Code', '').strip()
            if len(sid) == 4 and sid.isdigit():
                stock_map[sid] = f"{sid}.TW"
                
        url_tpex = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
        for item in requests.get(url_tpex, headers=headers, timeout=10).json():
            sid = item.get('SecuritiesCompanyCode', '').strip()
            if len(sid) == 4 and sid.isdigit():
                stock_map[sid] = f"{sid}.TWO"
                
        if len(stock_map) > 1000:
            return stock_map
    except Exception:
        pass

    default_stocks = {'2330': '2330.TW', '2317': '2317.TW', '2454': '2454.TW', '2382': '2382.TW', '3231': '3231.TWO'}
    return default_stocks

# ==========================================
# 💬 4. PTT 聲量模組
# ==========================================
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_social_sentiment(target_stocks):
    stock_info_map = {str(sid).strip(): "" for sid in target_stocks}
    sentiment_counts = {sid: {'ptt': 0, 'score': 0.0} for sid in stock_info_map.keys()}
    
    if not stock_info_map:
        return sentiment_counts

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36"})
    stock_patterns = {sid: [re.compile(r'(?<!\d)' + re.escape(sid) + r'(?!\d)')] for sid in stock_info_map.keys()}

    try:
        session.cookies.set('over18', '1')
        ptt_url = "https://www.ptt.cc/bbs/Stock/index.html"
        
        for _ in range(5):  # 網頁端抓前 5 頁維持極速回應
            res = session.get(ptt_url, timeout=5)
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
    except Exception:
        pass

    for sid, data in sentiment_counts.items():
        data['score'] = round(data['ptt'] * 2.5, 1)

    return sentiment_counts

# ==========================================
# 📊 5. 評估策略引擎與繪圖
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
            '股票代號': clean_sid,
            '收盤價': round(cur_close, 2),
            '成交量(張)': int(cur_vol),
            '5日外資買超(張)': foreign_buy_5d,
            '5日投信買超(張)': trust_buy_5d,
            '季線乖離(%)': round(dist_ma60_pct, 2)
        }
    return None

def plot_kline_chart(stock_id, df):
    try:
        df_plot = df.tail(60).copy()
        mc = mpf.make_marketcolors(up='r', down='g', edge='inherit', wick='inherit', volume='inherit')
        s = mpf.make_mpf_style(marketcolors=mc, gridstyle="--")
        
        addplots = []
        if len(df_plot) >= 4:
            prev_3day_max_high = df_plot['High'].iloc[-4:-1].max()
            high_3d_line = pd.Series(prev_3day_max_high, index=df_plot.index)
            addplots.append(mpf.make_addplot(high_3d_line, color='red', linestyle='--', width=1.2))

        fig, _ = mpf.plot(
            df_plot, type='candle', style=s,
            title=f"Stock {stock_id} K-Line", ylabel='Price',
            volume=True, mav=(5, 20, 60), addplot=addplots if addplots else None,
            returnfig=True
        )
        return fig
    except Exception:
        return None

# ==========================================
# 📱 6. 手機 UI 控制主流程
# ==========================================
mode = st.radio("🎯 請選擇執行模式：", ["全市場自動篩選", "單股獨立診斷"], horizontal=True)

if mode == "全市場自動篩選":
    if st.button("🚀 開始全市場掃描", use_container_width=True):
        with st.spinner("🏛 正在讀取三大法人籌碼數據中..."):
            chip_data = fetch_chips_twse_official()
            
        with st.spinner("🌐 下載全台股 K 線數據中..."):
            stock_map = get_stock_mapping()
            tickers = list(stock_map.values())
            data = yf.download(tickers, period="6mo", group_by='ticker', progress=False, threads=True)
            
        st.info("🎉 開始進行全台股策略篩選與條件比對...")
        passed_stocks = []

        progress_bar = st.progress(0)
        total_stocks = len(stock_map)

        for idx, (sid, ticker) in enumerate(stock_map.items()):
            progress_bar.progress((idx + 1) / total_stocks)
            df_stock = None
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if ticker in data.columns.levels[0]:
                        df_stock = data[ticker].dropna()
                else:
                    df_stock = data.dropna()
            except Exception:
                continue

            if df_stock is not None and not df_stock.empty:
                result = evaluate_stock(sid, df_stock, chip_data)
                if result:
                    passed_stocks.append((result, df_stock))

        progress_bar.empty()

        if passed_stocks:
            st.success(f"⚡ 掃描完成！共有 {len(passed_stocks)} 支股票符合所有嚴格條件！")
            
            passed_ids = [item[0]['股票代號'] for item in passed_stocks]
            with st.spinner("💬 正在掃描 PTT 股票版聲量中..."):
                sentiment_data = fetch_social_sentiment(passed_ids)

            final_results = []
            for res, df_stock in passed_stocks:
                sid = res['股票代號']
                s_info = sentiment_data.get(sid, {'ptt': 0, 'score': 0.0})
                res['PTT討論篇數'] = s_info['ptt']
                res['聲量總得分'] = s_info['score']
                final_results.append(res)

            res_df = pd.DataFrame(final_results)
            res_df = res_df.sort_values(by=['聲量總得分', '5日外資買超(張)'], ascending=False)

            # 顯示結果表格
            st.subheader("📋 篩選結果清單")
            st.dataframe(res_df, use_container_width=True, hide_index=True)

            # 繪製 K 線圖
            st.subheader("📈 符合標的 K 線圖預覽")
            for res, df_stock in passed_stocks:
                sid = res['股票代號']
                st.write(f"### 🔥 {sid}")
                fig = plot_kline_chart(sid, df_stock)
                if fig:
                    st.pyplot(fig)
        else:
            st.warning("ℹ 今日全市場無符合 6 大嚴格條件之標的。")

else: # 單股獨立診斷模式
    target_stock = st.text_input("👉 請輸入要診斷的股票代碼 (例如: 2330 或 2408)", value="2330")
    if st.button("🔍 執行單股診斷", use_container_width=True):
        clean_sid = str(target_stock).strip().upper().replace('.TW', '').replace('.TWO', '')
        
        with st.spinner(f"正在分析 {clean_sid}..."):
            chip_data = fetch_chips_twse_official()
            symbol = f"{clean_sid}.TW"
            df = yf.download(symbol, period="6mo", progress=False)
            if df.empty or len(df) < 60:
                df = yf.download(f"{clean_sid}.TWO", period="6mo", progress=False)

            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            if not df.empty and len(df) >= 60:
                res = evaluate_stock(clean_sid, df, chip_data)
                chip_info = chip_data.get(clean_sid, {'foreign': 0, 'trust': 0})
                
                col1, col2 = st.columns(2)
                col1.metric("近5日外資買超", f"{chip_info['foreign']} 張")
                col2.metric("近5日投信買超", f"{chip_info['trust']} 張")

                if res:
                    st.success(f"✅ {clean_sid} 符合所有 6 大嚴格篩選條件！")
                else:
                    st.warning(f"❌ {clean_sid} 未通過今日篩選條件。")

                fig = plot_kline_chart(clean_sid, df)
                if fig:
                    st.pyplot(fig)
            else:
                st.error("❌ 無法抓取該股票 K 線數據或資料天數不足。")