import streamlit as st
import FinanceDataReader as fdr
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
ANALYSIS_LOG_FILE, BACKUP_KRX_FILE = "analysis_log_v5.json", "backup_krx.json"

st.set_page_config(page_title="Phoenix v5.9.92", layout="wide")

st.markdown("""
    <style>
    .stMetric { background-color: rgba(240, 242, 246, 0.1); padding: 10px; border-radius: 5px; }
    .stDataFrame { border: 1px solid #e6e9ef; }
    .stButton>button { width: 100%; border-radius: 5px; }
    </style>
    """, unsafe_allow_html=True)

if "scan_storage" not in st.session_state: st.session_state.scan_storage = []
if "auto_code" not in st.session_state: st.session_state.auto_code = ""
if "fixed_log" not in st.session_state: st.session_state.fixed_log = []

def save_log(name, code):
    if not any(log['code'] == code for log in st.session_state.fixed_log):
        st.session_state.fixed_log.append({"name": name, "code": code})

@st.cache_data(ttl=3600)
def get_krx_list():
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        return df
    except: return pd.DataFrame([{"Code": "005930", "Name": "삼성전자"}])

# --- [2. v5.9.92 고성능 ATR 엔진] ---
def analyze_overload_v92(ticker, target_date):
    ticker_str = str(ticker).zfill(6)
    try:
        df = fdr.DataReader(ticker_str, target_date - datetime.timedelta(days=150), target_date)
        if df is None or len(df) < 40: return None, None
        df.columns = [c.upper() for c in df.columns]
        df = df.rename(columns={'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME'}).reset_index()
        
        # 패턴 B 탐지 (6일 연속 거래량 감소)
        vol_cliff = (df['VOLUME'] < df['VOLUME'].shift(1)).iloc[-6:].all()
        
        # [수정] ATR 기반 하이퍼 동적 매도 로직
        high_low = df['HIGH'] - df['LOW']
        high_close = (df['HIGH'] - df['CLOSE'].shift(1)).abs()
        low_close = (df['LOW'] - df['CLOSE'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        
        # 현재가 대비 ATR 비율 (변동성 강도)
        volatility_rate = (atr / df['CLOSE'].iloc[-1]) * 100
        
        # [수정] 7% ~ 30% 사이 최적 목표가 계산 (단기 폭발력 가중치 적용)
        # 변동성의 약 4.5배를 목표치로 설정하되 범위를 제한
        target_profit = round(max(7.0, min(30.0, volatility_rate * 4.5)), 1)
        stop_loss = -3.0 # 지휘관의 손절 원칙 유지
        
        # 적합도 계산 (CV 1.9 영점)
        pre_20 = df['CLOSE'].iloc[-21:-1]
        cv_val = (pre_20.std() / pre_20.mean()) * 100
        vol_ratio = df['VOLUME'].iloc[-1] / (df['VOLUME'].iloc[-21:-1].mean() + 1)
        
        fit_score = 0
        if 1.5 <= cv_val <= 2.2: fit_score += 40
        if 2.8 <= vol_ratio <= 4.2: fit_score += 40
        if vol_cliff: fit_score += 20
        
        is_noise = (target_date.weekday() == 2) or (target_date.month in [2, 3])
        action, buy_p = "🛑 관망", "0%"
        if fit_score >= 80:
            action, buy_p = ("⚠️ 고위험매수", "15%") if is_noise else ("🔥 즉시매수", "100%")
        elif fit_score >= 60: action, buy_p = "⚔️ 분할진입", "50%"
        elif vol_cliff: action, buy_p = "⚡ 절벽포착", "30%"

        return {
            "날짜": target_date.strftime("%y-%m-%d"), "종목코드": ticker_str,
            "적합도": f"{int(fit_score)}%", "상태": action, "금일비중": buy_p,
            "익절목표": f"{target_profit}%", "손절가": f"{stop_loss}%", "is_valid": fit_score >= 50 or vol_cliff
        }, df
    except: return None, None

# --- [3. UI 레이아웃] ---
krx_df = get_krx_list()
krx_df['Display'] = krx_df['Code'] + " | " + krx_df['Name']

st.sidebar.title("📁 분석 히스토리")
for idx, log in enumerate(st.session_state.fixed_log):
    if st.sidebar.button(f"{log['name']} ({log['code']})", key=f"hist_{log['code']}_{idx}"):
        st.session_state.auto_code = log['code']; st.rerun()

st.title("Phoenix v5.9.92 Classic")
st.caption("1억 달성 ATR 하이퍼 다이내믹 엔진 (7%-30% Variable Target)")

with st.container():
    c1, c2 = st.columns([7, 3])
    with c1:
        def_idx = 0
        if st.session_state.auto_code:
            matches = [i for i, x in enumerate(krx_df['Code']) if x == st.session_state.auto_code]
            if matches: def_idx = matches[0]
        search_input = st.selectbox("종목 선택 및 검색", krx_df['Display'].tolist(), index=def_idx)
    with c2:
        btn_click = st.button("🔍 정밀 분석 실행", type="primary", use_container_width=True)

if btn_click or st.session_state.auto_code:
    t_code = search_input.split(" | ")[0] if not st.session_state.auto_code else st.session_state.auto_code
    res, df = analyze_overload_v92(t_code, datetime.date.today())
    if res:
        name = krx_df[krx_df['Code'] == t_code]['Name'].values[0]
        save_log(name, t_code)
        
        st.subheader(f"[{name}] 분석 리포트")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("상태", res['상태'])
        m2.metric("적합도", res['적합도'])
        m3.metric("매수 비중", res['금일비중'])
        m4.metric("익절/손절", f"{res['익절목표']} / {res['손절가']}")
        
        fig = go.Figure(data=[go.Candlestick(open=df['OPEN'], high=df['HIGH'], low=df['LOW'], close=df['CLOSE'])])
        fig.update_layout(height=400, template="plotly_white", xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True)
        st.session_state.auto_code = ""

st.divider()

if st.button("🚀 전 종목 시장 스캔 (Course Record)", use_container_width=True):
    results = []
    prog = st.progress(0)
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(analyze_overload_v92, row['Code'], datetime.date.today()): row for _, row in krx_df.iterrows()}
        for i, future in enumerate(as_completed(futures)):
            r, _ = future.result()
            if r and r['is_valid']:
                r['종목명'] = krx_df[krx_df['Code'] == r['종목코드']]['Name'].values[0]
                results.append(r)
            if i % 100 == 0: prog.progress((i+1)/len(krx_df))
    st.session_state.scan_storage = results; st.rerun()

if st.session_state.scan_storage:
    st.subheader("📋 스캔 결과 리스트")
    s_df = pd.DataFrame(st.session_state.scan_storage).sort_values(by='적합도', ascending=False)
    st.dataframe(s_df[['날짜', '종목명', '종목코드', '적합도', '상태', '금일비중', '익절목표', '손절가']], use_container_width=True, hide_index=True)
    
    selected_target = st.selectbox("🎯 타겟 락온 (상단 이동)", ["선택하세요"] + (s_df['종목코드'] + " | " + s_df['종목명']).tolist())
    if selected_target != "선택하세요":
        st.session_state.auto_code = selected_target.split(" | ")[0]
        st.rerun()
