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

# --- [1. 시스템 설정] ---
SCAN_RESULT_FILE = "last_scan_results.json"
BACKUP_KRX_FILE = "backup_krx.json"

if "scan_storage" not in st.session_state:
    st.session_state.scan_storage = []
if "fixed_log" not in st.session_state: st.session_state.fixed_log = []
if "auto_code" not in st.session_state: st.session_state.auto_code = ""

@st.cache_data(ttl=3600)
def get_krx_list():
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        return df
    except: return pd.DataFrame([{"Code": "005930", "Name": "삼성전자"}])

# --- [2. 분석 엔진: 거래대금 가중치 적용] ---
def analyze_v99(ticker, target_date):
    ticker_str = str(ticker).zfill(6)
    start_date = target_date - datetime.timedelta(days=180)
    try:
        df = fdr.DataReader(ticker_str, start_date, target_date)
        if df is None or len(df) < 40: return None, None
        
        df.columns = [c.upper() for c in df.columns]
        df = df.rename(columns={'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME'})
        
        # [거래대금 연산]
        df['AMOUNT'] = (df['CLOSE'] * df['VOLUME']) / 100_000_000
        avg_amount = df['AMOUNT'].rolling(5).mean().iloc[-1] # 5일 평균 거래대금(억)
        
        # ATR 및 변동성 연산
        high_low = df['HIGH'] - df['LOW']
        tr = pd.concat([high_low], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        curr_price = int(df['CLOSE'].iloc[-1])
        volatility_rate = (atr / curr_price) * 100
        
        # 거래대금 가점 로직 (50억~500억 주도주 섹터)
        value_score = 0
        if 50 <= avg_amount <= 500: value_score = 40
        elif 10 <= avg_amount < 50: value_score = 20
        elif avg_amount > 500: value_score = 10 # 너무 무거움
        
        # 기술적 지표 합산 (거래량비 + 거래대금 가점)
        vol_ratio = df['VOLUME'].iloc[-1] / (df['VOLUME'].iloc[-21:-1].mean() + 1)
        fit_score = int(min(100, (vol_ratio * 15) + value_score))
        
        phase = "🔥 출격준비" if fit_score > 75 else "🟡 엔진예열" if fit_score > 50 else "💤 대기"
        
        return {
            "종목코드": ticker_str, "현재가": curr_price, "적합도": fit_score,
            "상태": phase, "거래대금(억)": round(avg_amount, 1),
            "익절목표": f"{round(volatility_rate * 4.5, 1)}%",
            "목표가": int(curr_price * (1 + (volatility_rate * 4.5)/100)),
            "최종손절선": int(curr_price * 0.97), "is_valid": True if avg_amount >= 10 else False
        }, df
    except: return None, None

# --- [3. UI 레이아웃] ---
st.set_page_config(page_title="Phoenix Pulse v5.9.99", layout="wide")
krx_df = get_krx_list()
krx_df['Display'] = krx_df['Code'] + " | " + krx_df['Name']

st.markdown(f"### 🔥 Phoenix Pulse v5.9.99 | `거래대금 최적화 엔진`")

# 사이드바
st.sidebar.title("📁 분석 히스토리")
for idx, log in enumerate(st.session_state.fixed_log):
    if st.sidebar.button(f"{log['name']} ({log['code']})", key=f"side_{idx}", use_container_width=True):
        st.session_state.auto_code = log['code']; st.rerun()

with st.form("input_form"):
    c1, c2, c3 = st.columns([4, 1.5, 2])
    def_idx = 0
    if st.session_state.auto_code:
        matches = [i for i, x in enumerate(krx_df['Code']) if x == st.session_state.auto_code]
        if matches: def_idx = matches[0]
    
    selected = c1.selectbox("종목 선택", krx_df['Display'].tolist(), index=def_idx)
    btn = c2.form_submit_button("🔍 정밀 분석", type="primary", use_container_width=True)
    d_in = c3.date_input("날짜", value=datetime.date.today())

if btn or st.session_state.auto_code:
    t_code = selected.split(" | ")[0] if not st.session_state.auto_code else st.session_state.auto_code
    res, df_c = analyze_v99(t_code, d_in)
    if res:
        st.session_state.auto_code = ""
        st.markdown(f"#### 🎯 [{krx_df[krx_df['Code']==res['종목코드']]['Name'].values[0]}] 리포트")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("상태", res['상태'])
        m2.metric("적합도", f"{res['적합도']}%", delta=f"{res['거래대금(억)']}억")
        m3.metric("목표가", f"{res['목표가']:,}원", delta=res['익절목표'])
        m4.metric("손절가", f"{res['최종손절선']:,}원", delta="-3.0%")
        
        fig = go.Figure(data=[go.Candlestick(x=df_c.index, open=df_c['OPEN'], high=df_c['HIGH'], low=df_c['LOW'], close=df_c['CLOSE'], line=dict(width=1, color='white'))])
        fig.update_layout(height=400, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=5,r=5,t=5,b=5))
        st.plotly_chart(fig, use_container_width=True)

st.divider()

if st.button("🚀 거래대금 필터 적용 광역 스캔", use_container_width=True):
    temp = []
    pb = st.progress(0)
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(analyze_v99, r['Code'], d_in): r for _, r in krx_df.iterrows()}
        for i, f in enumerate(as_completed(futures)):
            r, _ = f.result()
            if r and r['is_valid'] and r['적합도'] > 50:
                r['종목명'] = futures[f]['Name']
                temp.append(r)
            if i % 100 == 0: pb.progress((i+1)/len(krx_df))
    st.session_state.scan_storage = temp
    st.rerun()

if st.session_state.scan_storage:
    sdf = pd.DataFrame(st.session_state.scan_storage).sort_values(by='적합도', ascending=False)
    st.markdown(f"### 📋 스캔 결과 (거래대금 10억 이상 유효주)")
    st.dataframe(sdf[['종목명', '적합도', '상태', '거래대금(억)', '현재가', '목표가', '익절목표']], use_container_width=True, hide_index=True)
