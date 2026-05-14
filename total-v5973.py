import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [1. 시스템 설정 및 네트워크 체크] ---
BACKUP_KRX_FILE = "backup_krx.json"

@st.cache_data(ttl=3600)
def check_network_and_get_list():
    try:
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        df.to_json(BACKUP_KRX_FILE)
        return df, "🟢 Online (Server Connected)"
    except:
        if os.path.exists(BACKUP_KRX_FILE):
            return pd.read_json(BACKUP_KRX_FILE), "🟡 Offline (Backup Mode)"
        return None, "🔴 Connection Failed"

# --- [2. v5.9.73 정밀 분석 엔진 핵심부] ---
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
        
        body_ratio = (df['CLOSE'] - df['OPEN']).abs() / (df['HIGH'] - df['LOW'] + 0.001)
        vol_ma20 = df['VOLUME'].iloc[-21:-1].mean()
        vol_ratio = df['VOLUME'].iloc[-1] / (vol_ma20 + 1)
        pre_20_close = df['CLOSE'].iloc[-21:-1]
        cv_val = (pre_20_close.std() / pre_20_close.mean()) * 100
        
        cv_score = max(0, 100 - (abs(cv_val - 1.8) * 20))
        vol_score = min(100, (vol_ratio / 5.0) * 100)
        similarity = (cv_score * 0.3) + (vol_score * 0.7)
        
        fit_score = 0
        if 82.5 <= similarity <= 88.0: fit_score += 30
        if 2.8 <= vol_ratio <= 4.2: fit_score += 30
        if 1.5 <= cv_val <= 2.2: fit_score += 25
        if 0.65 <= body_ratio.iloc[-1] <= 0.85: fit_score += 15
        
        if fit_score >= 50:
            return {
                "종목명": name, "종목코드": ticker_str, "적합도": int(fit_score),
                "현재가": int(df['CLOSE'].iloc[-1]), "유사도": round(similarity, 1),
                "거래량비": round(vol_ratio, 2), "CV": round(cv_val, 2),
                "몸통비율": round(body_ratio.iloc[-1], 2),
                "거래대금(억)": round(df['AMOUNT'].iloc[-1] / 1e8, 1) if 'AMOUNT' in df.columns else 0
            }
    except: pass
    return None

# --- [3. UI 레이아웃 및 제어부] ---
st.set_page_config(page_title="Phoenix v5.9.73 Strategic Radar", layout="wide")
st.markdown("<style>div.stApp {background: white !important;} * {color: black !important;}</style>", unsafe_allow_html=True)

krx_list, net_status = check_network_and_get_list()
col_h1, col_h2 = st.columns([8, 2])
with col_h1: st.title("⚡ Phoenix v5.9.73 [Full-Scan Radar]")
with col_h2: st.metric("Network", net_status)

if st.button("🚀 전 종목 병렬 스캔 및 데이터 수집 시작", width='stretch'):
    if krx_list is not None:
        results = []
        prog_bar = st.progress(0)
        status_text = st.empty()
        time_text = st.empty()
        
        start_time = time.time()
        total_count = len(krx_list)
        
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = {executor.submit(analyze_v5_73_core, row): row for _, row in krx_list.iterrows()}
            completed = 0
            for future in as_completed(futures):
                completed += 1
                res = future.result()
                if res: results.append(res)
                
                if completed % 50 == 0 or completed == total_count:
                    elapsed = time.time() - start_time
                    avg = elapsed / completed
                    rem = avg * (total_count - completed)
                    prog_bar.progress(completed / total_count)
                    status_text.markdown(f"**📡 스캔 중:** `{completed}`/`{total_count}` 완료")
                    time_text.markdown(f"**⏱️ 예상 남은 시간:** `{int(rem // 60)}분 {int(rem % 60)}초` ")

        prog_bar.empty()
        status_text.empty()
        time_text.empty()
        
        if results:
            df_final = pd.DataFrame(results).sort_values(by='적합도', ascending=False)
            st.subheader(f"📊 스캔 리포트 ({len(results)}개 포착)")
            
            # --- [수정 구간: applymap -> map 적용] ---
            def highlight_fit(val):
                if val >= 90: return 'background-color: #d4edda; font-weight: bold; color: #155724'
                if val >= 70: return 'background-color: #fff3cd; color: #856404'
                return ''
            
            display_cols = ["종목명", "종목코드", "적합도", "현재가", "유사도", "거래량비", "CV", "몸통비율", "거래대금(억)"]
            
            # 최신 Pandas 규격에 맞게 .style.map() 사용
            try:
                st.dataframe(df_final[display_cols].style.map(highlight_fit, subset=['적합도']), use_container_width=True, hide_index=True)
            except:
                # 혹시 모를 구버전 대응을 위한 예외 처리
                st.dataframe(df_final[display_cols].style.applymap(highlight_fit, subset=['적합도']), use_container_width=True, hide_index=True)
            
            # CSV 다운로드 버튼
            csv_data = df_final[display_cols].to_csv(index=False).encode('utf-8-sig')
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            st.download_button(
                label="📥 결과 CSV 파일로 저장",
                data=csv_data,
                file_name=f"{today_str}_Phoenix_v73_Scan.csv",
                mime="text/csv",
                key="download_btn"
            )
        else:
            st.warning("⚠️ 포착된 종목이 없습니다.")
    else:
        st.error("데이터 서버 접속 실패 및 백업 파일 부재.")
