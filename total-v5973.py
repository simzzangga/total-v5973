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

# 페이지 설정 (사이드바 기본 확장)
st.set_page_config(page_title="Phoenix v5.9.90 Enterprise", layout="centered")

# [디자인 수정] 화이트/실무형 스타일 적용
st.markdown("""
    <style>
    /* 배경 및 기본 폰트 */
    .stApp { background-color: #ffffff; }
    .stMarkdown, .stText, p, h1, h2, h3, span { color: #333333 !important; font-family: 'Malgun Gothic', sans-serif; }
    
    /* 연한 파랑 포인트 컬러 (버튼 및 구분선) */
    .stButton>button { 
        background-color: #e3f2fd !important; 
        color: #1976d2 !important; 
        border: 1px solid #bbdefb !important;
        font-weight: bold;
        width: 100%;
    }
    .stButton>button:hover { background-color: #bbdefb !important; }
    
    /* 폼 및 입력창 스타일 */
    div[data-testid="stForm"] { border: 1px solid #e0e0e0; background-color: #fcfcfc; border-radius: 5px; }
    hr { border-top: 2px solid #e3f2fd; }
    
    /* 데이터프레임 엑셀 느낌 강화 */
    .stDataFrame { border: 1px solid #e0e0e0; border-radius: 0px; }
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

# --- [2. 엔진 로직 (보존)] ---
def analyze_overload_v90(ticker, target_date):
    ticker_str = str(ticker).zfill(6)
    try:
        df = fdr.DataReader(ticker_str, target_date - datetime.timedelta(days=120), target_date)
        if df is None or len(df) < 35: return None, None
        df.columns = [c.upper() for c in df.columns]
        df = df.rename(columns={'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME'}).reset_index()
        
        vol_cliff = (df['VOLUME'] < df['VOLUME'].shift(1)).iloc[-6:].all()
        pre_20 = df['CLOSE'].iloc[-21:-1]
        cv_val = (pre_20.std() / pre_20.mean()) * 100
        vol_ratio = df['VOLUME'].iloc[-1] / (df['VOLUME'].iloc[-21:-1].mean() + 1)
        
        cv_score = max(0, 100 - (abs(cv_val - 1.9) * 20))
        vol_score = min(100, (vol_ratio / 5.0) * 100)
        similarity = (cv_score * 0.3) + (vol_score * 0.7)
        
        fit_score = 0
        if 84.5 <= similarity <= 90.0: fit_score += 30
        if 2.8 <= vol_ratio <= 4.2: fit_score += 30
        if 1.5 <= cv_val <= 2.2: fit_score += 25
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
            "익절": "10%(D)/8%(C)", "손절": "-3.0%", "is_valid": fit_score >= 50 or vol_cliff
        }, df
    except: return None, None

# --- [3. UI 레이아웃] ---
krx_df = get_krx_list()
krx_df['Display'] = krx_df['Code'] + " | " + krx_df['Name']

col_h1, col_h2 = st.columns([8, 2])
with col_h1: st.subheader("📊 PHOENIX v5.9.90 [Enterprise]")
with col_h2:
    if st.button("새로고침"):
        st.cache_data.clear()
        if os.path.exists(BACKUP_KRX_FILE): os.remove(BACKUP_KRX_FILE)
        st.rerun()

st.sidebar.title("📁 분석 히스토리")
analysis_log = load_data(ANALYSIS_LOG_FILE, [])
for idx, log in enumerate(analysis_log[:15]):
    if st.sidebar.button(f"{log['name']}", key=f"hist_{log['code']}_{idx}"):
        st.session_state.auto_code = log['code']; st.rerun()

# --- [메인 분석 폼] ---
with st.form("ignite_form"):
    def_idx = 0
    if st.session_state.auto_code:
        matches = [i for i, x in enumerate(krx_df['Code']) if x == st.session_state.auto_code]
        if matches: def_idx = matches[0]
    
    search_input = st.selectbox("🎯 분석 대상 종목 선택", krx_df['Display'].tolist(), index=def_idx)
    btn_click = st.form_submit_button("정밀 분석 실행")

if btn_click or st.session_state.auto_code:
    t_code = search_input.split(" | ")[0] if not st.session_state.auto_code else st.session_state.auto_code
    res, df = analyze_overload_v90(t_code, datetime.date.today())
    if res:
        name = krx_df[krx_df['Code'] == t_code]['Name'].values[0]
        temp_log = [l for l in load_data(ANALYSIS_LOG_FILE, []) if str(l['code']).zfill(6) != t_code]
        temp_log.insert(0, {"name": name, "code": t_code}); save_data(ANALYSIS_LOG_FILE, temp_log[:40])
        
        st.info(f"📌 {name} ({t_code}) 분석 결과")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("상태", res['상태'])
        c2.metric("적합도", res['적합도'])
        c3.metric("매수비중", res['금일비중'])
        c4.metric("익절/손절", f"{res['익절']} / {res['손절']}")
        
        # [디자인] 차트 배경 화이트 테두리 및 가독성 개선
        fig = go.Figure(data=[go.Candlestick(
            open=df['OPEN'], high=df['HIGH'], low=df['LOW'], close=df['CLOSE'],
            increasing_line_color='#d32f2f', decreasing_line_color='#1976d2',
            line=dict(width=1)
        )])
        last_c = df['CLOSE'].iloc[-1]
        fig.add_hline(y=last_c*1.1, line_dash="dot", line_color="orange", annotation_text="[TARGET]")
        fig.add_hline(y=last_c*0.97, line_dash="solid", line_color="red", annotation_text="[STOP]")
        fig.update_layout(height=250, margin=dict(l=0,r=0,t=0,b=0), template="plotly_white", 
                          xaxis_rangeslider_visible=False)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        st.session_state.auto_code = ""

st.divider()

# --- [전 종목 스캔 섹션] ---
if st.button("🏁 시장 전 종목 스캔 시작 (Record Scan)", use_container_width=True):
    results = []
    prog, st_text = st.progress(0), st.empty()
    start_tm, total = time.time(), len(krx_df)
    with ThreadPoolExecutor(max_workers=30) as ex:
        futures = {ex.submit(analyze_overload_v90, row['Code'], datetime.date.today()): row for _, row in krx_df.iterrows()}
        for i, future in enumerate(as_completed(futures)):
            r, _ = future.result()
            if r and r['is_valid']:
                r['종목명'] = krx_df[krx_df['Code'] == r['종목코드']]['Name'].values[0]
                results.append(r)
            if i % 100 == 0:
                prog.progress((i+1)/total)
                st_text.write(f"🔍 스캔 중... ({i+1}/{total})")
    st.session_state.scan_storage = results; st.rerun()

if st.session_state.scan_storage:
    st.subheader("📋 스캔 결과 리스트")
    s_df = pd.DataFrame(st.session_state.scan_storage).sort_values(by='적합도', ascending=False)
    
    # [디자인] 결과 리스트에만 조건부 색상 적용
    def color_status(val):
        if '즉시매수' in val: color = '#ffcdd2'
        elif '분할진입' in val: color = '#c8e6c9'
        elif '절벽포착' in val: color = '#bbdefb'
        else: color = 'transparent'
        return f'background-color: {color}'
    
    display_df = s_df[['날짜', '종목명', '종목코드', '적합도', '상태', '금일비중', '익절', '손절']]
    st.dataframe(display_df.style.applymap(color_status, subset=['상태']), use_container_width=True, hide_index=True)
    
    # [자동 연동] 결과 리스트에서 타겟 선택 시 상단으로 이동
    selected_target = st.selectbox("🎯 리스트에서 종목 선택 (위에서 정밀 분석)", ["선택하세요"] + (s_df['종목코드'] + " | " + s_df['종목명']).tolist())
    if selected_target != "선택하세요":
        st.session_state.auto_code = selected_target.split(" | ")[0]
        st.rerun()
