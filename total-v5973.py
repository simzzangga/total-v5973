import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [1. 시스템 설정 및 네트워크 관리] ---
BACKUP_KRX_FILE = "backup_krx.json"

def get_network_status():
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        df.to_json(BACKUP_KRX_FILE)
        return df, "🟢 Online (Server Connected)"
    except Exception as e:
        if os.path.exists(BACKUP_KRX_FILE):
            return pd.read_json(BACKUP_KRX_FILE), "🟡 Offline (Backup Mode)"
        return None, f"🔴 Connection Failed: {str(e)}"

# --- [2. v5.9.73 정밀 분석 엔진] ---
def analyze_v5_73_core(row):
    ticker, name = row['Code'], row['Name']
    ticker_str = str(ticker).zfill(6)
    target_date = datetime.date.today()
    start_date = target_date - datetime.timedelta(days=240)
    
    try:
        df = fdr.DataReader(ticker_str, start_date, target_date)
        if df is None or len(df) < 40: return None
        
        df.columns = [c.upper() for c in df.columns]
        rename_map = {'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME','거래대금':'AMOUNT'}
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        
        # 핵심 지표 연산
        body_ratio = (df['CLOSE'] - df['OPEN']).abs() / (df['HIGH'] - df['LOW'] + 0.001)
        vol_ma20 = df['VOLUME'].iloc[-21:-1].mean()
        vol_ratio = df['VOLUME'].iloc[-1] / (vol_ma20 + 1)
        pre_20_close = df['CLOSE'].iloc[-21:-1]
        cv_val = (pre_20_close.std() / pre_20_close.mean()) * 100
        
        cv_score = max(0, 100 - (abs(cv_val - 1.8) * 20))
        vol_score = min(100, (vol_ratio / 5.0) * 100)
        similarity = (cv_score * 0.3) + (vol_score * 0.7)
        raw_exp = (vol_ratio * 2.5) + (similarity * 0.1)
        
        # 상태 및 비중 산출 (스크린샷 기반)
        phase, weight_now, exp_profit = "🟡 관망", 0, 0
        if similarity >= 85 and vol_ratio >= 5.0 and body_ratio.iloc[-1] >= 0.7:
            phase, weight_now, exp_profit = "🔥 3차: 강력매수", 50, round(raw_exp + 5.0 - 2.0, 2)
        elif similarity >= 78 and vol_ratio >= 4.0:
            phase, weight_now, exp_profit = "🚀 2차: 추가매수", 30, round(raw_exp - 2.0, 2)
        elif similarity >= 70 and vol_ratio >= 3.0:
            phase, weight_now, exp_profit = "⚔️ 1차: 신규진입", 20, round(max(8.0, raw_exp * 0.8) - 2.0, 2)
        
        # 적합도 가중치 배점
        fit_score = 0
        if 82.5 <= similarity <= 88.0: fit_score += 30
        if 2.8 <= vol_ratio <= 4.2: fit_score += 30
        if 1.5 <= cv_val <= 2.2: fit_score += 25
        if 0.65 <= body_ratio.iloc[-1] <= 0.85: fit_score += 15
        
        today_prefix = target_date.strftime("[%Y-%m-%d]")
        
        if fit_score >= 50:
            return {
                "종목명": f"{today_prefix} {name}",
                "종목코드": ticker_str,
                "적합도": int(fit_score),
                "상태": phase,
                "비중": f"{weight_now}%",
                "현재가": int(df['CLOSE'].iloc[-1]),
                "목표가": int(df['CLOSE'].iloc[-1] * 1.10), # 73버전 기준 10%
                "손절가": int(df['CLOSE'].iloc[-1] * 0.95), # 73버전 기준 5%
                "예상수익": f"{exp_profit}%",
                "유사도": round(similarity, 1),
                "거래량비": round(vol_ratio, 1),
                "CV": round(cv_val, 2),
                "몸통비율": round(body_ratio.iloc[-1], 2)
            }
    except: pass
    return None

# --- [3. UI 레이아웃] ---
st.set_page_config(page_title="Phoenix v5.9.79 Perfect Replica", layout="wide")
st.markdown("<style>div.stApp {background: white !important;} * {color: black !important;}</style>", unsafe_allow_html=True)

if 'krx_data' not in st.session_state:
    df, status = get_network_status()
    st.session_state['krx_data'] = df
    st.session_state['net_status'] = status

col_h1, col_h2 = st.columns([7, 3])
with col_h1: st.title("⚡ Phoenix v5.9.73 [Full-Scan]")
with col_h2: 
    st.metric("Network Status", st.session_state['net_status'])
    if st.button("🔄 네트워크 재연결"):
        st.cache_data.clear()
        df, status = get_network_status()
        st.session_state['krx_data'] = df
        st.session_state['net_status'] = status
        st.rerun()

st.divider()

if st.button("🚀 전 종목 병렬 스캔 및 리포트 생성", width='stretch'):
    krx_list = st.session_state['krx_data']
    if krx_list is not None:
        results = []
        prog_bar = st.progress(0)
        status_text = st.empty()
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(analyze_v5_73_core, row): row for _, row in krx_list.iterrows()}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                res = future.result()
                if res: results.append(res)
                if completed % 50 == 0:
                    prog_bar.progress(completed / len(krx_list))
                    status_text.text(f"분석 중: {completed}/{len(krx_list)}")

        prog_bar.empty()
        status_text.empty()
        
        if results:
            df_final = pd.DataFrame(results).sort_values(by='적합도', ascending=False)
            st.subheader(f"📊 스캔 결과 리포트 ({len(results)}개)")
            
            # 지휘관님 스크린샷과 동일한 컬럼 순서
            cols = ["종목명", "종목코드", "적합도", "상태", "비중", "현재가", "목표가", "손절가", "예상수익", "유사도", "거래량비", "CV", "몸통비율"]
            
            # 표 출력
            st.dataframe(df_final[cols], use_container_width=True, hide_index=True)
            
            # CSV 저장
            csv_data = df_final[cols].to_csv(index=False).encode('utf-8-sig')
            st.download_button("📥 CSV 결과 저장", csv_data, f"{datetime.date.today()}_Phoenix_v73.csv", "text/csv")
        else:
            st.warning("⚠️ 포착된 종목이 없습니다.")
