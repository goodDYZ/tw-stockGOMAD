import os
import re
import datetime
import time
import requests
import json
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import csv
import io
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from curl_cffi import requests as crequests

# ==========================================
# ⚙️ 全局設定區
# ==========================================
FINMIND_TOKEN = ""  # 免費 FinMind Token (若無設定，自動使用 TWSE/TPEx 官方 API 備援)
CHART_DIR = "./charts"

if not os.path.exists(CHART_DIR):
    os.makedirs(CHART_DIR)

# ==========================================
# 🏛 1. 籌碼資料擷取模組 (動態標頭對齊終極版)
# ==========================================
def fetch_chips_twse_official():
    print("🏛 [籌碼模組] 從證交所與櫃買中心擷取近 5 日三大法人籌碼...")
    chip_dict = {}
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    valid_days_count = 0  # 紀錄成功取得的「有效交易日」天數
    
    # 往回搜尋最多 15 天 (自動跨過週末、國定連假與颱風假)
    for i in range(15):
        target_date = datetime.now() - timedelta(days=i)
        
        # 排除週末
        if target_date.weekday() in (5, 6):
            continue

        date_twse = target_date.strftime("%Y%m%d")                    # 西元年：20261002
        roc_year = target_date.year - 1911
        date_tpex = f"{roc_year}/{target_date.month:02d}/{target_date.day:02d}"  # 民國年：115/10/02
        date_display = target_date.strftime("%Y-%m-%d")

        # ----------------------------------------------------
        # 1. 證交所 TWSE (上市 API - 動態定位欄位)
        # ----------------------------------------------------
        url_twse = f"https://www.twse.com.tw/rwd/zh/fund/T86?date={date_twse}&selectType=ALL&response=json"
        is_valid_trading_day = False  # 標記今天是否為有效交易日
        
        try:
            res = requests.get(url_twse, headers=headers, timeout=8)
            if res.status_code == 200:
                data = res.json()
                if data.get("stat") == "OK" and "data" in data and "fields" in data:
                    is_valid_trading_day = True
                    fields = data["fields"]
                    
                    # 🔍 動態尋找「外資」與「投信」買賣超欄位的索引 index
                    foreign_idx = -1
                    trust_idx = -1
                    
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
                    print(f"  └─ ✅ 上市 (TWSE) {date_display} 解析完成 (外資Idx:{foreign_idx}, 投信Idx:{trust_idx})")
                else:
                    print(f"  └─ ℹ️ 上市 (TWSE) {date_display} 非交易日/未開盤 (自動跳過)")
        except Exception as e:
            print(f"  └─ ❌ 上市 (TWSE) {date_display} 失敗: {e}")

        if not is_valid_trading_day:
            continue

        # ----------------------------------------------------
        # 2. 櫃買中心 TPEx (上櫃現行 CSV API)
        # ----------------------------------------------------
        url_tpex_csv = f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=AL&t=D&d={date_tpex}&p=1&st=1&s=0,asc,0&o=csv"
        try:
            res = requests.get(url_tpex_csv, headers=headers, timeout=10)
            if res.status_code == 200:
                content = res.content.decode('big5', errors='ignore')
                lines = content.splitlines()
                
                otc_count = 0
                for line in lines:
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
                                otc_count += 1
                            except (ValueError, IndexError):
                                continue
                                
                if otc_count > 0:
                    print(f"  └─ ✅ 上櫃 (TPEx) {date_display} CSV 解析完成 (成功讀取 {otc_count} 支)")
                else:
                    print(f"  └─ ⚠ 上櫃 (TPEx) {date_display} 無資料 (非交易日或未開盤)")
        except Exception as e:
            print(f"  └─ ❌ 上櫃 (TPEx) {date_display} 失敗: {e}")

        time.sleep(0.5)
        valid_days_count += 1
        
        if valid_days_count == 5:
            break

    print(f"\n🎉 最終全市場 (上市+上櫃) 成功取得總股數: {len(chip_dict)} 支！")
    return chip_dict

# ==========================================
# 🌐 2. 股票清單擷取模組 (高穩定備援機制)
# ==========================================
def get_stock_mapping():
    print("🔍 正在抓取全台股上市與上櫃股票代碼清單...")
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
            print(f"✅ 成功從證交所 OpenAPI 取得全台股 {len(stock_map)} 支股票！")
            return stock_map
    except Exception as e:
        print(f"⚠️ OpenAPI 清單獲取跳過: {e}")

    try:
        url_github = "https://raw.githubusercontent.com/datasets/invest-taiwan/main/stocks.json"
        res = requests.get(url_github, headers=headers, timeout=10)
        data = res.json()
        for item in data:
            sid = str(item.get('code', '')).strip()
            market = item.get('market', '')
            if len(sid) == 4 and sid.isdigit():
                suffix = ".TW" if market == "TWSE" else ".TWO"
                stock_map[sid] = f"{sid}{suffix}"
        if len(stock_map) > 1000:
            print(f"✅ 成功從備援資料庫取得 {len(stock_map)} 支台股！")
            return stock_map
    except Exception as e:
        print(f"⚠️ 備援資料庫獲取失敗: {e}")

    print("⚠️ 使用預設熱門股票清單...")
    default_tw = ['2330', '2317', '2454', '2308', '2382', '2356', '2881']
    default_two = ['3231', '8299', '6274']
    for s in default_tw:
        stock_map[s] = f"{s}.TW"
    for s in default_two:
        stock_map[s] = f"{s}.TWO"
    return stock_map

# ==========================================
# 💬 3. 聲量爬蟲與互動式 K 線圖繪製 (Plotly 版本)
# ==========================================
def fetch_social_sentiment(target_stocks):
    print("\n💬 [聲量模組] 開始掃描 PTT 網路討論聲量...")
    
    stock_info_map = {}
    if isinstance(target_stocks, dict):
        for sid, info in target_stocks.items():
            s_id = str(sid).strip()
            s_name = info.get('name', '') if isinstance(info, dict) else ''
            stock_info_map[s_id] = s_name
    elif isinstance(target_stocks, (list, set, tuple)):
        for sid in target_stocks:
            stock_info_map[str(sid).strip()] = ''

    sentiment_counts = {
        sid: {'ptt': 0, 'dcard': 0, 'score': 0.0, 'name': name} 
        for sid, name in stock_info_map.items()
    }
    
    if not stock_info_map:
        print(" ⚠️ 無輸入目標股票，跳過聲量掃描。")
        return sentiment_counts

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7"
    })

    stock_patterns = {}
    for sid, name in stock_info_map.items():
        patterns = [re.compile(r'(?<!\d)' + re.escape(sid) + r'(?!\d)')]
        if name and len(name) >= 2:
            patterns.append(re.compile(re.escape(name)))
        stock_patterns[sid] = patterns

    ptt_scanned_articles = 0
    try:
        session.cookies.set('over18', '1')
        ptt_url = "https://www.ptt.cc/bbs/Stock/index.html"
        
        for page in range(10):
            res = session.get(ptt_url, timeout=5)
            if res.status_code != 200:
                break

            soup = BeautifulSoup(res.text, 'html.parser')
            articles = soup.find_all('div', class_='r-ent')

            for article in articles:
                title_div = article.find('div', class_='title')
                if not title_div or not title_div.find('a'):
                    continue

                title_text = title_div.text.strip()
                article_href = title_div.find('a')['href']
                article_url = f"https://www.ptt.cc{article_href}"

                article_content = ""
                try:
                    art_res = session.get(article_url, timeout=3)
                    if art_res.status_code == 200:
                        art_soup = BeautifulSoup(art_res.text, 'html.parser')
                        main_content = art_soup.find('div', id='main-content')
                        if main_content:
                            article_content = main_content.text
                except Exception:
                    pass

                full_text = f"{title_text} {article_content}"
                ptt_scanned_articles += 1

                for sid, patterns in stock_patterns.items():
                    if any(pat.search(full_text) for pat in patterns):
                        sentiment_counts[sid]['ptt'] += 1

            paging_btn = soup.find('a', string=re.compile(r'‹\s*上頁'))
            if paging_btn and 'href' in paging_btn.attrs:
                ptt_url = f"https://www.ptt.cc{paging_btn['href']}"
            else:
                break
            time.sleep(0.1)

        print(f"  └─ ✅ PTT 股票版掃描完成 (共掃描 {ptt_scanned_articles} 篇文章)")
    except Exception as e:
        print(f"  └─ ⚠️ PTT 聲量掃描中斷: {e}")

    for sid, data in sentiment_counts.items():
        score = data['ptt'] * 2.5
        data['score'] = round(score, 1)

    return sentiment_counts


def plot_kline_chart(stock_id, df):
    """使用 Plotly 繪製並輸出個股 60 日互動式 K 線圖與均線指標"""
    try:
        if df is None or len(df) < 20:
            print(f"⚠️ {stock_id} K 線資料不足，跳過繪圖")
            return

        df_plot = df.tail(60).copy()
        
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                            vertical_spacing=0.03, row_heights=[0.7, 0.3])

        fig.add_trace(go.Candlestick(
            x=df_plot.index,
            open=df_plot['Open'],
            high=df_plot['High'],
            low=df_plot['Low'],
            close=df_plot['Close'],
            name='K線',
            increasing_line_color='red',
            decreasing_line_color='green'
        ), row=1, col=1)

        for ma, color in zip([5, 20, 60], ['orange', 'blue', 'purple']):
            ma_series = df_plot['Close'].rolling(window=ma).mean()
            fig.add_trace(go.Scatter(
                x=df_plot.index, y=ma_series,
                mode='lines', name=f'MA{ma}',
                line=dict(color=color, width=1)
            ), row=1, col=1)

        if len(df_plot) >= 4:
            prev_3day_max_high = df_plot['High'].iloc[-4:-1].max()
            fig.add_trace(go.Scatter(
                x=df_plot.index,
                y=[prev_3day_max_high] * len(df_plot),
                mode='lines',
                name='前3日最高價',
                line=dict(color='red', dash='dash', width=1.2)
            ), row=1, col=1)

        colors = ['red' if c >= o else 'green' for c, o in zip(df_plot['Close'], df_plot['Open'])]
        fig.add_trace(go.Bar(
            x=df_plot.index, y=df_plot['Volume'],
            name='成交量', marker_color=colors
        ), row=2, col=1)

        fig.update_layout(
            title=f"Stock {stock_id} 互動式 K 線圖",
            xaxis_rangeslider_visible=False,
            height=600,
            margin=dict(l=30, r=30, t=40, b=30)
        )

        filepath = os.path.join(CHART_DIR, f"{stock_id}_kline.html")
        fig.write_html(filepath)
        print(f"📈 [圖表輸出] {stock_id} 互動 K 線圖已成功儲存至: {filepath}")
    except Exception as e:
        print(f"❌ {stock_id} K 線圖繪製失敗: {e}")

# ==========================================
# 📊 4. 篩選策略計算引擎 (修正防呆版)
# ==========================================
def evaluate_stock(stock_id, df, chip_data):
    if df is None or len(df) < 60:
        return None, f"{stock_id} K線資料不足"

    clean_sid = str(stock_id).strip().upper().replace('.TW', '').replace('.TWO', '')

    close = df['Close'].astype(float)
    high = df['High'].astype(float)
    
    raw_vol = df['Volume'].astype(float)
    if raw_vol.iloc[-5:].mean() < 10000:
        volume = raw_vol
    else:
        volume = raw_vol / 1000.0

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

    is_pass = cond1 and cond2 and cond3 and cond4 and cond5 and cond6

    diag_log = (
        f"==================== 🔍 {clean_sid} 條件過濾診斷 ====================\n"
        f"1. 成交量 >= 200張 ({cur_vol:.0f}張)             : {cond1}\n"
        f"2. 5日籌碼 (外資>=1000張 or 投信>=500張) [5日外:{foreign_buy_5d}張, 5日投:{trust_buy_5d}張] : {cond2}\n"
        f"3. 季線安全區 (0% <= 幅度 {dist_ma60_pct:.2f}% <= 15.0%)   : {cond3}\n"
        f"4. 均線多頭 (股價 {cur_close:.1f} > MA20 {ma20:.1f} > MA60 {ma60:.1f}) : {cond4}\n"
        f"5. 成交量量增 (當前 {cur_vol:.0f}張 >= 1.1倍昨量 {prev_vol*1.1:.0f}張 或 5日均量 {vol_ma5:.0f}張) : {cond5}\n"
        f"6. 突破前3日最高價 (當前收盤 {cur_close:.1f} > 前3日最高 {prev_3day_high:.1f}) : {cond6}\n"
        f"判定總結: {'✅ 符合篩選' if is_pass else '❌ 未通過'}\n"
        f"============================================================"
    )

    if is_pass:
        return {
            'stock_id': clean_sid,
            'close': cur_close,
            'volume': int(cur_vol),
            'foreign_buy_5d': foreign_buy_5d,
            'trust_buy_5d': trust_buy_5d,
            'ma60_dist_%': round(dist_ma60_pct, 2)
        }, diag_log
    
    return None, diag_log

# ==========================================
# 🚀 5. 主執行程序
# ==========================================
def main():
    print("🚀 開始執行選股策略程式...\n")
    
    chip_data = fetch_chips_twse_official()
    stock_map = get_stock_mapping()
    tickers = list(stock_map.values())
    
    print(f"\n🌐 正在從 yfinance 批次下載 {len(tickers)} 支股票的歷史 K 線資料...")
    
    try:
        data = yf.download(tickers, period="6mo", group_by='ticker', progress=True, threads=True)
    except Exception as e:
        print(f"❌ K 線下載失敗: {e}")
        return

    print("\n🎉 開始進行全台股策略篩選...\n")
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

        result, diag_log = evaluate_stock(sid, df_stock, chip_data)

        if result:
            print(f"✅ 發現符合條件標的！")
            print(diag_log)
            passed_stocks.append((result, df_stock))

    print(f"\n⚡ 掃描完成！全市場共有 {len(passed_stocks)} 支股票符合所有條件。")
    
    if passed_stocks:
        passed_ids = [item[0]['stock_id'] for item in passed_stocks]
        sentiment_data = fetch_social_sentiment(passed_ids)
        
        final_results = []
        for res, df_stock in passed_stocks:
            sid = res['stock_id']
            s_info = sentiment_data.get(sid, {'ptt': 0, 'dcard': 0, 'score': 0.0})
            res['ptt_cnt'] = s_info['ptt']
            res['dcard_cnt'] = s_info['dcard']
            res['sentiment_score'] = s_info['score']
            final_results.append(res)
            
            plot_kline_chart(sid, df_stock)

        res_df = pd.DataFrame(final_results)
        
        sort_col = 'foreign_buy_5d' if 'foreign_buy_5d' in res_df.columns else ('foreign_buy' if 'foreign_buy' in res_df.columns else None)
        
        if 'sentiment_score' in res_df.columns and sort_col:
            res_df = res_df.sort_values(by=['sentiment_score', sort_col], ascending=False)
        elif sort_col:
            res_df = res_df.sort_values(by=[sort_col], ascending=False)
        
        print("\n✅ 符合條件選股清單：")
        print(res_df.to_string(index=False))
        
        res_df.to_csv("screener_result.csv", index=False, encoding="utf-8-sig")
        print(f"\n📁 結果已存至：screener_result.csv")
    else:
        print("\nℹ 今日全市場無符合 6 大嚴格條件之標的。")


def diagnose_single_stock(target_id, chip_data=None):
    target_id = str(target_id).strip().upper().replace('.TW', '').replace('.TWO', '')
    symbol = f"{target_id}.TW"
    
    print(f"\n==================== 🔍 {target_id} 單股獨立診斷啟動 ====================")
    
    df = yf.download(symbol, period="6mo", progress=False)
    if df.empty or len(df) < 60:
        df = yf.download(f"{target_id}.TWO", period="6mo", progress=False)
        if df.empty or len(df) < 60:
            print(f"❌ 錯誤：無法抓取 {target_id} 的 K 線數據或資料不足 60 天！")
            return

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if chip_data is None:
        print("🌐 未傳入全市場籌碼，現場呼叫籌碼模組抓取近 5 日籌碼...")
        chip_data = fetch_chips_twse_official()

    res, diag_log = evaluate_stock(target_id, df, chip_data)
    print(diag_log)
    
    chip_info = chip_data.get(target_id)
    if chip_info is None:
        print(f"⚠️ 警報：chip_data 字典中完全【找不到】Key 為 '{target_id}' 的籌碼資料！")
    else:
        print(f"✅ 籌碼字典比對成功！抓到的數據為: {chip_info}")


if __name__ == "__main__":
    user_input = input("👉 請輸入要診斷的股票代碼 (直接按 Enter 跑全市場篩選): ").strip()
    
    if user_input:
        diagnose_single_stock(user_input)
    else:
        main()
