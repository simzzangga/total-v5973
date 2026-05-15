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
ANALYSIS_LOG_FILE, BACKUP_KRX_FILE = "analysis_log_v5.json", "backup_krx.json"

if "scan_storage" not in st.session_state:
    if os.path.exists(SCAN_RESULT_FILE):
        try:
            with open(SCAN_RESULT_FILE, "r", encoding="utf-8") as f:
                st.session_state.scan_storage = json.load(f)
        except: st.session_state.scan_storage = []
    else: st.session_state.scan_storage = []

if "auto_code" not in st.session_state: st.session_state.auto_code = ""
if "last_viewed" not in st.session_state: st.session_state.last_viewed = None
# [로그 고정 Fix] 세션 내에서만 유지되는 순서 고정 리스트
if "fixed_log" not in st.session_state: st.session_state.fixed_log = []

def save_to_fixed_log(name, code):
    # 새로운 종목이 들어오면 기존에 없는 경우에만 리스트 맨 뒤에 추가 (순서 고정)
    if not any(log['code'] == code for log in st.session_state.fixed_log):
        st.session_state.fixed_log.append({"name": name, "code": code})

@st.cache_data(ttl=3600, show_spinner=False)
def get_krx_list_ultimate():
    if os.path.exists(BACKUP_KRX_FILE):
        try:
            df_l = pd.read_json(BACKUP_KRX_FILE)
            if not df_l.empty: return df_l
        except: pass
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        df.to_json(BACKUP_KRX_FILE)
        return df
    except: return pd.DataFrame([{"Code": "005930", "Name": "삼성전자"}])

# --- [2. 고도화 엔진: ATR 하이퍼 다이내믹 + 패턴 B] ---
def analyze_overload_v95(ticker, target_date):
    ticker_str = str(ticker).zfill(6)
    start_date = target_date - datetime.timedelta(days=150)
    try:
        df = fdr.DataReader(ticker_str, start_date, target_date)
        if df is None or len(df) < 40: return None, None
        df.columns = [c.upper() for c in df.columns]
        df = df.rename(columns={'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME'}).reset_index()
        
        # 패턴 B 탐지 (6일 연속 거래량 감소)
        vol_cliff = (df['VOLUME'] < df['VOLUME'].shift(1)).iloc[-6:].all()
        
        # ATR 기반 하이퍼 동적 매도 로직 (7% ~ 30%)
        high_low = df['HIGH'] - df['LOW']
        high_close = (df['HIGH'] - df['CLOSE'].shift(1)).abs()
        low_close = (df['LOW'] - df['CLOSE'].shift(1)).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        volatility_rate = (atr / df['CLOSE'].iloc[-1]) * 100
        
        # 정직한 기대 수익률 계산 (변동성 4.5배수, 단기 1억 목표 가중치)
        target_profit = round(max(7.0, min(30.0, volatility_rate * 4.5)), 1)
        stop_loss = -3.0 # 손절 3% 고정 원칙
        
        # 적합도 영점 조절 (CV 1.9 / SIM 85)
        pre_20 = df['CLOSE'].iloc[-21:-1]
        cv_val = (pre_20.std() / pre_20.mean()) * 100
        vol_ratio = df['VOLUME'].iloc[-1] / (df['VOLUME'].iloc[-21:-1].mean() + 1)
        body_ratio = (df['CLOSE'].iloc[-1] - df['OPEN'].iloc[-1]).abs() / (df['HIGH'].iloc[-1] - df['LOW'].iloc[-1] + 0.001)
        
        cv_score = max(0, 100 - (abs(cv_val - 1.9) * 20))
        vol_score = min(100, (vol_ratio / 5.0) * 100)
        similarity = (cv_score * 0.3) + (vol_score * 0.7)
        
        fit_score = 0
        if 84.5 <= similarity <= 90.0: fit_score += 30
        if 2.8 <= vol_ratio <= 4.2: fit_score += 30
        if 1.5 <= cv_val <= 2.2: fit_score += 25
        if 0.65 <= body_ratio <= 0.85: fit_score += 15
        if vol_cliff: fit_score += 20
        
        is_noise = (target_date.weekday() == 2) or (target_date.month in [2, 3])
        phase, weight_now = "🟡 관망", "0%"
        if fit_score >= 80:
            phase, weight_now = ("⚠️ 고위험매수", "15%") if is_noise else ("🔥 즉시매수", "100%")
        elif fit_score >= 60: phase, weight_now = "⚔️ 분할진입", "50%"
        elif vol_cliff: phase, weight_now = "⚡ 절벽포착", "30%"

        return {
            "종목코드": ticker_str, "현재가": int(df['CLOSE'].iloc[-1]), "적합도": fit_score,
            "상태": phase, "비중": weight_now, "익절목표": f"{target_profit}%", "손절가": f"{stop_loss}%",
            "목표타격가": int(df['CLOSE'].iloc[-1] * (1 + target_profit/100)),
            "최종손절선": int(df['CLOSE'].iloc[-1] * (1 + stop_loss/100)),
            "거래량비": round(vol_ratio, 1), "CV": round(cv_val, 2), "몸통비율": round(body_ratio, 2),
            "유사도": round(similarity, 1), "is_valid": True if fit_score >= 50 or vol_cliff else False,
            "스캔날짜": target_date.strftime('%Y-%m-%d')
        }, df
    except: return None, None

# --- [3. UI 레이아웃] ---
st.set_page_config(page_title="Phoenix Pulse v5.9.95", layout="wide")

krx_df = get_krx_list_ultimate()
krx_df['Display'] = krx_df['Code'] + " | " + krx_df['Name']

c_head1, c_head2 = st.columns([6, 2])
with c_head1: st.markdown(f"### 🔥 Phoenix Pulse v5.9.95 | `ENTERPRISE OVERLOAD`")
with c_head2:
    if st.button("🔄 리스트 동기화", use_container_width=True):
        if os.path.exists(BACKUP_KRX_FILE): os.remove(BACKUP_KRX_FILE)
        st.cache_data.clear(); st.rerun()

# [사이드바 Fix] 추가 순서대로 고정되는 로그
st.sidebar.title("📁 분석 히스토리 (고정)")
for idx, log in enumerate(st.session_state.fixed_log): 
    if st.sidebar.button(f"{log['name']} ({log['code']})", key=f"side_{log['code']}_{idx}", width='stretch'):
        st.session_state.auto_code = log['code']; st.rerun()

with st.form("main_analysis_form"):
    c1, c2, c3 = st.columns([4, 1.5, 2])
    def_idx = 0
    target_val = st.session_state.auto_code if st.session_state.auto_code else st.session_state.last_viewed
    if target_val:
        matches = [i for i, x in enumerate(krx_df['Code']) if x == str(target_val).zfill(6)]
        if matches: def_idx = matches[0]
    
    search_input = c1.selectbox("종목 선택", krx_df['Display'].tolist(), index=def_idx)
    c2.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
    btn_click = c2.form_submit_button("🔍 정밀 분석 실행", type="primary", use_container_width=True)
    d_input = c3.date_input("날짜 지정", value=datetime.date.today())

if btn_click or (st.session_state.auto_code != ""):
    target_code = search_input.split(" | ")[0] if search_input else st.session_state.auto_code
    res, df = analyze_overload_v95(target_code, d_input)
    if res:
        st.session_state.last_viewed = res['종목코드']
        disp_name = krx_df[krx_df['Code'] == res['종목코드']]['Name'].values[0]
        save_to_fixed_log(disp_name, res['종목코드']) # 로그 고정 추가
        st.session_state.auto_code = ""
        
        st.markdown(f"## 🎯 [{disp_name}] 전략 리포트")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("전투 상태", res['상태'])
        m2.metric("익절 목표", res['익절목표'], delta=f"적합도 {res['적합도']}%")
        m3.metric("목표 타격가", f"{res['목표타격가']:,}원")
        m4.metric("최종 손절선", f"{res['최종손절선']:,}원", delta=res['손절가'], delta_color="inverse")
        
        # 차트 가시성 강화 (흰색 테두리)
        fig = go.Figure(data=[go.Candlestick(x=df.index, open=df['OPEN'], high=df['HIGH'], low=df['LOW'], close=df['CLOSE'],
                                             increasing_line_color='red', decreasing_line_color='blue',
                                             line=dict(width=1, color='white'))])
        fig.update_layout(height=450, xaxis_rangeslider_visible=False, template="plotly_dark", margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)

st.divider()

# --- [4. 광역 병렬 스캐너] ---
if st.button("🚀 전 종목 광역 정밀 병렬 스캔 (Parallel Overload)", width='stretch'):
    st.session_state.scan_storage = []
    prog_bar, status_text, time_text = st.progress(0), st.empty(), st.empty()
    start_time, total_stocks = time.time(), len(krx_df)
    
    results_found = []
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_to_stock = {executor.submit(analyze_overload_v95, row['Code'], datetime.date.today()): row for _, row in krx_df.iterrows()}
        completed = 0
        for future in as_completed(future_to_stock):
            completed += 1
            row = future_to_stock[future]
            try:
                r, _ = future.result()
                if r and r['is_valid']:
                    r['종목명'] = f"[{r['스캔날짜']}] {row['Name']}"
                    results_found.append(r)
            except: pass
            
            if completed % 20 == 0 or completed == total_stocks:
                elapsed = time.time() - start_time
                rem = (elapsed / completed) * (total_stocks - completed)
                prog_bar.progress(completed / total_stocks)
                status_text.markdown(f"**📡 정찰 중:** `{completed}/{total_stocks}` (포착: {len(results_found)})")
                time_text.markdown(f"⏱️ **예상 남은 시간:** `{int(rem//60)}분 {int(rem%60)}초` ")

    st.session_state.scan_storage = results_found
    save_data(SCAN_RESULT_FILE, st.session_state.scan_storage)
    st.rerun()

if st.session_state.scan_storage:
    scan_df = pd.DataFrame(st.session_state.scan_storage)
    scan_df = scan_df.sort_values(by='적합도', ascending=False)
    
    st.markdown(f"### 📋 스캔 결과 ({len(scan_df)}개 포착)")
    # 지휘관 요청 컬럼 셋팅
    cols = ['종목명', '종목코드', '적합도', '상태', '비중', '익절목표', '손절가', '현재가', '목표타격가', '최종손절선', '유사도', '거래량비', 'CV']
    st.dataframe(scan_df[cols], use_container_width=True, hide_index=True)
    
    # [자동 연동] 리스트에서 선택 시 상단 분석기로 즉시 전송
    selected_target = st.selectbox("🎯 결과 리스트에서 타겟 락온 (상단 이동)", ["선택하세요"] + (scan_df['종목코드'] + " | " + scan_df['종목명']).tolist())
    if selected_target != "선택하세요":
        st.session_state.auto_code = selected_target.split(" | ")[0]
        st.rerun()
