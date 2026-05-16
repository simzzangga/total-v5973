import streamlit as st
import FinanceDataReader as fdr
import yfinance as yf
import pandas as pd
import numpy as np
import datetime
import json
import os
import plotly.graph_objects as go
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [1. 시스템 설정 및 영속성] ---
SCAN_RESULT_FILE = "last_scan_results.json"
BACKUP_KRX_FILE = "backup_krx.json"

if "scan_storage" not in st.session_state:
    if os.path.exists(SCAN_RESULT_FILE):
        try:
            with open(SCAN_RESULT_FILE, "r", encoding="utf-8") as f:
                st.session_state.scan_storage = json.load(f)
        except: st.session_state.scan_storage = []
    else: st.session_state.scan_storage = []

if "auto_code" not in st.session_state: st.session_state.auto_code = ""
if "last_viewed" not in st.session_state: st.session_state.last_viewed = None
if "fixed_log" not in st.session_state: st.session_state.fixed_log = []
if "server_status" not in st.session_state: st.session_state.server_status = "🛰️ 엔진 점화 중..."

def save_to_fixed_log(name, code):
    if not any(log['code'] == code for log in st.session_state.fixed_log):
        st.session_state.fixed_log.append({"name": name, "code": code})

@st.cache_data(ttl=3600, show_spinner=False)
def get_krx_list_ultimate():
    if os.path.exists(BACKUP_KRX_FILE):
        try:
            df_l = pd.read_json(BACKUP_KRX_FILE)
            if not df_l.empty: 
                st.session_state.server_status = "🔥 출격 준비 완료 (LOCAL FAST)"
                return df_l
        except: pass
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        df.to_json(BACKUP_KRX_FILE)
        st.session_state.server_status = "🔥 출격 준비 완료 (SERVER LIVE)"
        return df
    except:
        st.session_state.server_status = "⚠️ 서버 점검 중"
        return pd.DataFrame([{"Code": "005930", "Name": "삼성전자"}])

# --- [2. 분석 엔진 (v5.10.0 거래대금 필터 추가)] ---
def analyze_v10(ticker, target_date):
    ticker_str = str(ticker).zfill(6)
    start_date = target_date - datetime.timedelta(days=180)
    try:
        df = fdr.DataReader(ticker_str, start_date, target_date)
        if df is None or df.empty:
            yf_ticker = f"{ticker_str}.KS" if ticker_str.startswith(('0', '1')) else f"{ticker_str}.KQ"
            df = yf.download(yf_ticker, start=start_date, end=target_date + datetime.timedelta(days=1), progress=False)
        
        if df is None or len(df) < 20: return None, None

        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df.columns = [c.upper() for c in df.columns]
        df = df.rename(columns={'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME','ADJ CLOSE':'CLOSE'})
        df = df[['OPEN', 'HIGH', 'LOW', 'CLOSE', 'VOLUME']].dropna()
        
        # [거래대금 필터 추가] - 종가 * 거래량 / 1억
        curr_price = int(df['CLOSE'].iloc[-1])
        curr_volume = df['VOLUME'].iloc[-1]
        amount_억 = round((curr_price * curr_volume) / 100_000_000, 1)
        
        # 최근 20일 평균 거래량 대비 비율
        vol_ratio = round(curr_volume / (df['VOLUME'].iloc[-21:-1].mean() + 1), 2)
        
        # 적합도 연산 (거래량비 기반 + 거래대금 가중치)
        # 거래대금이 50억~200억 사이일 때 가산점 부여
        val_bonus = 30 if 50 <= amount_억 <= 200 else 10 if amount_억 > 200 else 0
        fit_score = int(min(100, (vol_ratio * 15) + val_bonus))
        
        phase = "🔥 출격준비" if fit_score > 70 else "🟡 엔진예열"

        return {
            "종목코드": ticker_str, "현재가": curr_price, "적합도": fit_score,
            "상태": phase, "비중": "100%" if fit_score > 80 else "50%",
            "익절목표": "15.0%", "손절가": "-3.0%", "거래대금(억)": amount_억,
            "목표타격가": int(curr_price * 1.15), "최종손절선": int(curr_price * 0.97),
            "거래량비": vol_ratio, "is_valid": True if amount_억 >= 10 else False, # 10억 미만 필터링
            "스캔날짜": target_date.strftime('%Y-%m-%d')
        }, df
    except: return None, None

# --- [3. UI 레이아웃] ---
st.set_page_config(page_title="Phoenix Pulse v5.10.0", layout="wide")
krx_df = get_krx_list_ultimate()
krx_df['Display'] = krx_df['Code'] + " | " + krx_df['Name']

st.markdown(f"### 🔥 Phoenix Pulse v5.10.0 | `{st.session_state.server_status}`")

# 사이드바
st.sidebar.title("📁 분석 히스토리")
for idx, log in enumerate(st.session_state.fixed_log):
    if st.sidebar.button(f"{log['name']} ({log['code']})", key=f"side_{idx}", use_container_width=True):
        st.session_state.auto_code = log['code']; st.rerun()
if st.sidebar.button("🗑️ 히스토리 초기화", use_container_width=True):
    st.session_state.fixed_log = []; st.rerun()

# 입력 폼
with st.form("analysis_input_form"):
    c1, c2, c3 = st.columns([4, 1.5, 2])
    def_idx = 0
    target_val = st.session_state.auto_code if st.session_state.auto_code else st.session_state.last_viewed
    if target_val:
        matches = [i for i, x in enumerate(krx_df['Code']) if x == str(target_val).zfill(6)]
        if matches: def_idx = matches[0]
    
    selected_disp = c1.selectbox("종목 선택", krx_df['Display'].tolist(), index=def_idx)
    d_input = c3.date_input("날짜 지정", value=datetime.date.today())
    btn_click = c2.form_submit_button("🔍 분석 실행", type="primary", use_container_width=True)

if btn_click or (st.session_state.auto_code != ""):
    t_code = selected_disp.split(" | ")[0] if not st.session_state.auto_code else st.session_state.auto_code
    res, df_chart = analyze_v10(t_code, d_input)
    if res:
        st.session_state.last_viewed = res['종목코드']
        d_name = krx_df[krx_df['Code'] == res['종목코드']]['Name'].values[0]
        save_to_fixed_log(d_name, res['종목코드'])
        st.session_state.auto_code = ""
        
        st.markdown(f"#### 🎯 [{d_name}] 전략 리포트")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("상태", res['상태'])
        m2.metric("적합도", f"{res['적합도']}%", delta=f"{res['거래대금(억)']}억")
        m3.metric("목표가", f"{res['목표타격가']:,}원")
        m4.metric("손절가", f"{res['최종손절선']:,}원")
        
        fig = go.Figure(data=[go.Candlestick(x=df_chart.index, open=df_chart['OPEN'], high=df_chart['HIGH'], low=df_chart['LOW'], close=df_chart['CLOSE'], line=dict(width=1, color='white'))])
        fig.update_layout(height=400, xaxis_rangeslider_visible=False, template="plotly_dark", margin=dict(l=5, r=5, t=5, b=5))
        st.plotly_chart(fig, use_container_width=True)

st.divider()

if st.button("🚀 거래대금 필터 적용 광역 스캔", use_container_width=True):
    temp_results = []
    p_bar = st.progress(0)
    st_msg = st.empty()
    with ThreadPoolExecutor(max_workers=30) as executor:
        futures = {executor.submit(analyze_v10, row['Code'], d_input): row for _, row in krx_df.iterrows()}
        for i, future in enumerate(as_completed(futures)):
            r, _ = future.result()
            if r and r['is_valid']: # 거래대금 10억 이상인 것만 저장
                r['종목명'] = futures[future]['Name']
                temp_results.append(r)
            if i % 100 == 0:
                p_bar.progress((i+1)/len(krx_df))
                st_msg.write(f"📡 정찰 중... ({i+1}/{len(krx_df)})")
    
    st.session_state.scan_storage = temp_results
    with open(SCAN_RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(temp_results, f, ensure_ascii=False)
    st.rerun()

if st.session_state.scan_storage:
    st.markdown(f"### 📋 스캔 결과 (거래대금 10억 이상 포착: {len(st.session_state.scan_storage)}개)")
    scan_df = pd.DataFrame(st.session_state.scan_storage).sort_values(by='적합도', ascending=False)
    cols = ['종목명', '종목코드', '적합도', '상태', '거래대금(억)', '현재가', '목표타격가', '최종손절선', '거래량비']
    st.dataframe(scan_df[cols], use_container_width=True, hide_index=True)
    
    lock_on = st.selectbox("🎯 타겟 락온", ["선택하세요"] + (scan_df['종목코드'] + " | " + scan_df['종목명']).tolist())
    if lock_on != "선택하세요":
        st.session_state.auto_code = lock_on.split(" | ")[0]
        st.rerun()
