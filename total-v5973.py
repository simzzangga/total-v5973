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

# --- [1. 시스템 설정 & 영속성] ---
SCAN_RESULT_FILE = "last_scan_results.json"
ANALYSIS_LOG_FILE, BACKUP_KRX_FILE = "analysis_log_v5.json", "backup_krx.json"

st.set_page_config(page_title="Phoenix v5.9.88", layout="centered")
st.markdown("<style>div.stApp {background: #0E1117 !important;} * {color: white !important;}</style>", unsafe_allow_html=True)

if "scan_storage" not in st.session_state: st.session_state.scan_storage = []
if "auto_code" not in st.session_state: st.session_state.auto_code = ""

def load_data(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    return default

@st.cache_data(ttl=3600)
def get_krx_list():
    try: return fdr.StockListing('KRX')[['Code', 'Name']]
    except: return pd.DataFrame([{"Code": "005930", "Name": "삼성전자"}])

# --- [2. v5.9.88 오버로드 엔진] ---
def analyze_overload_v88(ticker, target_date):
    ticker_str = str(ticker).zfill(6)
    try:
        df = fdr.DataReader(ticker_str, target_date - datetime.timedelta(days=100), target_date)
        if df is None or len(df) < 30: return None, None
        df.columns = [c.upper() for c in df.columns]
        df = df.rename(columns={'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME'})
        
        # 주말/공휴일 제거 (연속 데이터화)
        df = df.reset_index(drop=True)
        
        # 영점 조절 로직 (v88)
        vol_cliff = (df['VOLUME'] < df['VOLUME'].shift(1)).iloc[-6:].all()
        cv_val = (df['CLOSE'].iloc[-21:-1].std() / df['CLOSE'].iloc[-21:-1].mean()) * 100
        vol_ratio = df['VOLUME'].iloc[-1] / (df['VOLUME'].iloc[-21:-1].mean() + 1)
        
        # 적합도 (지휘관 영점 고정)
        fit_score = 0
        if 1.5 <= cv_val <= 2.2: fit_score += 40
        if 2.8 <= vol_ratio <= 4.2: fit_score += 40
        if vol_cliff: fit_score += 20
        
        # 상태 및 비중 추천
        action, buy_ratio, color = "🛑 관망", "0%", "grey"
        if fit_score >= 80:
            action, buy_ratio, color = "🔥 즉시매수", "100%", "#D32F2F"
        elif fit_score >= 60:
            action, buy_ratio, color = "⚔️ 분할진입", "50%", "#2E7D32"
        elif vol_cliff:
            action, buy_ratio, color = "⚡ 절벽포착", "30%", "#1565C0"

        return {
            "날짜": target_date.strftime("%y-%m-%d"),
            "종목명": ticker_str, # 임시 (외부에서 매핑)
            "종목코드": ticker_str,
            "적합도": f"{int(fit_score)}%",
            "상태": action,
            "금일 매수비중(자산33%중)": buy_ratio,
            "자동매도 익절": "10.0% (D) / 8.0% (C)",
            "자동매도 손절": "-3.0%",
            "color": color,
            "is_valid": True if fit_score >= 50 or vol_cliff else False
        }, df
    except: return None, None

# --- [3. UI 레이아웃] ---
krx_df = get_krx_list()
krx_df['Code'] = krx_df['Code'].astype(str).str.zfill(6)
krx_df['Display'] = krx_df['Code'] + " | " + krx_df['Name']

# 헤더 (LED & 미니 싱크)
col_h1, col_h2 = st.columns([8, 1])
with col_h1: st.markdown(f"### 🟢 PHOENIX v5.9.88 [Mobile Overload]")
with col_h2: st.button("🔄")

# 사이드바 (고정 로그)
st.sidebar.title("📁 History")
analysis_log = load_data(ANALYSIS_LOG_FILE, [])
for idx, log in enumerate(analysis_log[:15]):
    if st.sidebar.button(f"{log['name']}", key=f"log_{log['code']}_{idx}", use_container_width=True):
        st.session_state.auto_code = log['code']; st.rerun()

# 분석 폼
with st.form("ignite_form"):
    search_input = st.selectbox("🎯 타겟 선택", krx_df['Display'].tolist())
    btn_click = st.form_submit_button("　　　　　🚀　　　　　", type="primary", use_container_width=True)

if btn_click or st.session_state.auto_code:
    t_code = search_input.split(" | ")[0] if not st.session_state.auto_code else st.session_state.auto_code
    res, df = analyze_overload_v88(t_code, datetime.date.today())
    if res:
        name = krx_df[krx_df['Code'] == t_code]['Name'].values[0]
        st.markdown(f"#### {res['상태']} : {name}")
        st.info(f"**금일 매수 비중:** {res['금일 매수비중(자산33%중)']} | **익절:** {res['자동매도 익절']} | **손절:** {res['자동매도 손절']}")
        
        # 정적 미니 차트
        fig = go.Figure(data=[go.Candlestick(open=df['OPEN'], high=df['HIGH'], low=df['LOW'], close=df['CLOSE'])])
        fig.add_hline(y=df['CLOSE'].iloc[-1]*1.1, line_dash="dot", line_color="orange")
        fig.add_hline(y=df['CLOSE'].iloc[-1]*0.97, line_dash="solid", line_color="red")
        fig.update_layout(height=250, margin=dict(l=0,r=0,t=0,b=0), template="plotly_dark", 
                          xaxis_rangeslider_visible=False, xaxis_showticklabels=False)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        st.session_state.auto_code = ""

st.divider()

# 코스 레코드 (스캔)
if st.button("🏁 오늘의 코스 레코드 시작", use_container_width=True):
    results = []
    prog, st_text, tm_text = st.progress(0), st.empty(), st.empty()
    start_tm = time.time()
    total = len(krx_df)
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(analyze_overload_v88, row['Code'], datetime.date.today()): row for _, row in krx_df.iterrows()}
        for i, future in enumerate(as_completed(futures)):
            r, _ = future.result()
            if r and r['is_valid']:
                r['종목명'] = krx_df[krx_df['Code'] == r['종목코드']]['Name'].values[0]
                results.append(r)
            if i % 50 == 0:
                elapsed = time.time() - start_tm
                est = (elapsed / (i+1)) * (total - (i+1))
                prog.progress((i+1)/total)
                st_text.markdown(f"🏎️ **RECORD:** `{i+1}/{total}`")
                tm_text.markdown(f"⏱️ **LAP:** `{int(elapsed//60):02}:{int(elapsed%60):02}` | **EST:** `{int(est//60):02}:{int(est%60):02}`")
    st.session_state.scan_storage = results
    st.rerun()

if st.session_state.scan_storage:
    s_df = pd.DataFrame(st.session_state.scan_storage).sort_values(by='적합도', ascending=False)
    cols = ['날짜', '종목명', '종목코드', '적합도', '상태', '금일 매수비중(자산33%중)', '자동매도 익절', '자동매도 손절']
    st.dataframe(s_df[cols], use_container_width=True, hide_index=True)
