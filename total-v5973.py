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

# --- [1. 시스템 설정 & 배경화면] ---
SCAN_RESULT_FILE = "last_scan_results.json"
ANALYSIS_LOG_FILE, BACKUP_KRX_FILE = "analysis_log_v5.json", "backup_krx.json"
BG_IMG_URL = "https://raw.githubusercontent.com/simzzangga/phoenix-v80/main/300km.png"

st.set_page_config(page_title="Phoenix v5.9.88", layout="centered")

# 하야부사 300km/h 배경화면 및 커스텀 스타일
st.markdown(f"""
    <style>
    .stApp {{
        background-image: url("{BG_IMG_URL}");
        background-size: cover;
        background-position: center;
        background-attachment: fixed;
    }}
    .stApp > header {{ background: transparent; }}
    section[data-testid="stSidebar"] {{ background: rgba(0,0,0,0.85) !important; }}
    .stMarkdown, .stText, p, h1, h2, h3, span {{ color: white !important; font-family: 'Courier New', monospace; }}
    .stButton>button {{ background-color: #D32F2F !important; color: white !important; border: none; font-weight: bold; }}
    .stDataFrame {{ background: rgba(0,0,0,0.7); }}
    </style>
    """, unsafe_allow_html=True)

if "scan_storage" not in st.session_state: st.session_state.scan_storage = []
if "auto_code" not in st.session_state: st.session_state.auto_code = ""

def load_data(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    return default

def save_data(path, data):
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)

@st.cache_data(ttl=3600)
def get_krx_list():
    if os.path.exists(BACKUP_KRX_FILE):
        try: return pd.read_json(BACKUP_KRX_FILE)
        except: pass
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        df.to_json(BACKUP_KRX_FILE)
        return df
    except: return pd.DataFrame([{"Code": "005930", "Name": "삼성전자"}])

# --- [2. v5.9.88 하이퍼 오버로드 엔진] ---
def analyze_overload_v88(ticker, target_date):
    ticker_str = str(ticker).zfill(6)
    try:
        df = fdr.DataReader(ticker_str, target_date - datetime.timedelta(days=120), target_date)
        if df is None or len(df) < 35: return None, None
        df.columns = [c.upper() for c in df.columns]
        df = df.rename(columns={'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME'}).reset_index()
        
        # 패턴 B 탐지 (6일 연속 거래량 감소)
        vol_cliff = (df['VOLUME'] < df['VOLUME'].shift(1)).iloc[-6:].all()
        
        # v88 영점 파라미터 (CV 1.9 / SIM 84.5-90)
        pre_20 = df['CLOSE'].iloc[-21:-1]
        cv_val = (pre_20.std() / pre_20.mean()) * 100
        vol_ratio = df['VOLUME'].iloc[-1] / (df['VOLUME'].iloc[-21:-1].mean() + 1)
        body_ratio = (df['CLOSE'].iloc[-1] - df['OPEN'].iloc[-1]) / (df['HIGH'].iloc[-1] - df['LOW'].iloc[-1] + 0.001)
        
        cv_score = max(0, 100 - (abs(cv_val - 1.9) * 20))
        vol_score = min(100, (vol_ratio / 5.0) * 100)
        similarity = (cv_score * 0.3) + (vol_score * 0.7)
        
        fit_score = 0
        if 84.5 <= similarity <= 90.0: fit_score += 30
        if 2.8 <= vol_ratio <= 4.2: fit_score += 30
        if 1.5 <= cv_val <= 2.2: fit_score += 25
        if 0.65 <= abs(body_ratio) <= 0.85: fit_score += 15
        if vol_cliff: fit_score += 20
        
        is_noise = (target_date.weekday() == 2) or (target_date.month in [2, 3])
        action, buy_p, color = "🛑 관망", "0%", "white"
        if fit_score >= 80:
            action, buy_p, color = ("⚠️ 고위험매수", "15%", "orange") if is_noise else ("🔥 즉시매수", "100%", "#D32F2F")
        elif fit_score >= 60:
            action, buy_p, color = "⚔️ 분할진입", "50%", "#2E7D32"
        elif vol_cliff:
            action, buy_p, color = "⚡ 절벽포착", "30%", "#1565C0"

        return {
            "날짜": target_date.strftime("%y-%m-%d"), "종목코드": ticker_str,
            "적합도": f"{int(fit_score)}%", "상태": action, "금일비중": buy_p,
            "익절": "10%(D)/8%(C)", "손절": "-3.0%", "is_valid": fit_score >= 50 or vol_cliff
        }, df
    except: return None, None

# --- [3. UI 레이아웃] ---
krx_df = get_krx_list()
krx_df['Display'] = krx_df['Code'] + " | " + krx_df['Name']

col_h1, col_h2 = st.columns([9, 1])
with col_h1: st.markdown("### 🟢 PHOENIX v5.9.88")
with col_h2:
    if st.button("🔄"):
        st.cache_data.clear()
        if os.path.exists(BACKUP_KRX_FILE): os.remove(BACKUP_KRX_FILE)
        st.rerun()

st.sidebar.title("📁 History")
analysis_log = load_data(ANALYSIS_LOG_FILE, [])
for idx, log in enumerate(analysis_log[:15]):
    if st.sidebar.button(f"{log['name']}", key=f"hist_{log['code']}_{idx}", use_container_width=True):
        st.session_state.auto_code = log['code']; st.rerun()

with st.form("ignite_form"):
    search_input = st.selectbox("🎯 타겟 선택", krx_df['Display'].tolist())
    btn_click = st.form_submit_button("　　　　　🚀 IGNITION　　　　　", type="primary", use_container_width=True)

if btn_click or st.session_state.auto_code:
    t_code = search_input.split(" | ")[0] if not st.session_state.auto_code else st.session_state.auto_code
    res, df = analyze_overload_v88(t_code, datetime.date.today())
    if res:
        name = krx_df[krx_df['Code'] == t_code]['Name'].values[0]
        temp_log = [l for l in load_data(ANALYSIS_LOG_FILE, []) if str(l['code']).zfill(6) != t_code]
        temp_log.insert(0, {"name": name, "code": t_code}); save_data(ANALYSIS_LOG_FILE, temp_log[:40])
        st.markdown(f"#### {res['상태']} : {name}")
        st.write(f"**적합도:** {res['적합도']} | **비중:** {res['금일비중']} | **익절:** {res['익절']} | **손절:** {res['손절']}")
        fig = go.Figure(data=[go.Candlestick(open=df['OPEN'], high=df['HIGH'], low=df['LOW'], close=df['CLOSE'])])
        fig.add_hline(y=df['CLOSE'].iloc[-1]*1.1, line_dash="dot", line_color="orange")
        fig.add_hline(y=df['CLOSE'].iloc[-1]*0.97, line_dash="solid", line_color="red")
        fig.update_layout(height=280, margin=dict(l=0,r=0,t=0,b=0), template="plotly_dark", xaxis_rangeslider_visible=False, xaxis_showticklabels=False, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        st.session_state.auto_code = ""

st.divider()

if st.button("🏁 오늘의 코스 레코드 시작 (전 종목 스캔)", use_container_width=True):
    results = []
    prog, st_text, tm_text = st.progress(0), st.empty(), st.empty()
    start_tm, total = time.time(), len(krx_df)
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
                prog.progress((i+1)/total); st_text.markdown(f"🏎️ **RECORD:** `{i+1}/{total}`"); tm_text.markdown(f"⏱️ **LAP:** `{int(elapsed//60):02}:{int(elapsed%60):02}` | **EST:** `{int(est//60):02}:{int(est%60):02}`")
    st.session_state.scan_storage = results; st.rerun()

if st.session_state.scan_storage:
    s_df = pd.DataFrame(st.session_state.scan_storage).sort_values(by='적합도', ascending=False)
    st.dataframe(s_df[['날짜', '종목명', '종목코드', '적합도', '상태', '금일비중', '익절', '손절']], use_container_width=True, hide_index=True)
