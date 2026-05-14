import streamlit as st
import FinanceDataReader as fdr
import pandas as pd
import numpy as np
import datetime
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- [1. 시스템 설정] ---
BACKUP_KRX_FILE = "backup_krx.json"

@st.cache_data(ttl=3600)
def get_full_krx_list():
    try:
        # 상장 종목 전체 로드
        df = fdr.StockListing('KRX')[['Code', 'Name']]
        df['Code'] = df['Code'].astype(str).str.zfill(6)
        df.to_json(BACKUP_KRX_FILE)
        return df
    except Exception as e:
        if os.path.exists(BACKUP_KRX_FILE):
            return pd.read_json(BACKUP_KRX_FILE)
        return pd.DataFrame([{"Code": "005930", "Name": "삼성전자"}])

# --- [2. v5.9.73 백테스트 정밀 엔진] ---
def analyze_v5_73_core(row):
    ticker, name = row['Code'], row['Name']
    ticker_str = str(ticker).zfill(6)
    target_date = datetime.date.today()
    # 충분한 데이터 확보를 위해 240일 로드
    start_date = target_date - datetime.timedelta(days=240)
    
    try:
        df = fdr.DataReader(ticker_str, start_date, target_date)
        if df is None or len(df) < 40: 
            return None
            
        # 컬럼명 표준화 (대문자)
        df.columns = [c.upper() for c in df.columns]
        # 한글 컬럼명 대응
        rename_map = {'시가':'OPEN','고가':'HIGH','저가':'LOW','종가':'CLOSE','거래량':'VOLUME','거래대금':'AMOUNT'}
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        
        # [핵심 지표 계산]
        # 1. 몸통 비율 (Body Ratio)
        body_ratio = (df['CLOSE'] - df['OPEN']).abs() / (df['HIGH'] - df['LOW'] + 0.001)
        
        # 2. 거래량비 (Vol Ratio)
        vol_ma20 = df['VOLUME'].iloc[-21:-1].mean()
        vol_ratio = df['VOLUME'].iloc[-1] / (vol_ma20 + 1)
        
        # 3. 변동계수 (CV)
        pre_20_close = df['CLOSE'].iloc[-21:-1]
        cv_val = (pre_20_close.std() / pre_20_close.mean()) * 100
        
        # 4. 유사도 (Similarity)
        # CV가 1.8에 가까울수록, 거래량비가 5.0에 가까울수록 점수 상승
        cv_score = max(0, 100 - (abs(cv_val - 1.8) * 20))
        vol_score = min(100, (vol_ratio / 5.0) * 100)
        similarity = (cv_score * 0.3) + (vol_score * 0.7)
        
        # [적합도 배점 - v5.9.73 오리지널 가중치]
        fit_score = 0
        if 82.5 <= similarity <= 88.0: fit_score += 30
        if 2.8 <= vol_ratio <= 4.2: fit_score += 30
        if 1.5 <= cv_val <= 2.2: fit_score += 25
        if 0.65 <= body_ratio.iloc[-1] <= 0.85: fit_score += 15
        
        # 50점 이상이면 데이터 수집
        if fit_score >= 50:
            return {
                "종목명": name,
                "종목코드": ticker_str,
                "적합도": int(fit_score),
                "현재가": int(df['CLOSE'].iloc[-1]),
                "유사도": round(similarity, 1),
                "거래량비": round(vol_ratio, 2),
                "CV": round(cv_val, 2),
                "몸통비율": round(body_ratio.iloc[-1], 2),
                "거래대금(억)": round(df['AMOUNT'].iloc[-1] / 1e8, 1) if 'AMOUNT' in df.columns else 0
            }
    except:
        pass
    return None

# --- [3. UI 레이아웃 및 제어] ---
st.set_page_config(page_title="Phoenix v5.9.75 Final Check", layout="wide")
st.markdown("<style>div.stApp {background: white !important;} * {color: black !important;}</style>", unsafe_allow_html=True)

st.title("⚡ Phoenix v5.9.75 [Backtest Final]")
st.caption("v5.9.73 엔진의 모든 지표를 포함한 전 종목 병렬 스캔 모드입니다.")

if st.button("🚀 전 종목 정밀 검수 스캔 시작", width='stretch'):
    krx_list = get_full_krx_list()
    results = []
    
    prog_bar = st.progress(0)
    status_text = st.empty()
    time_text = st.empty()
    
    start_time = time.time()
    total_count = len(krx_list)
    
    # 병렬 처리 (서버 사양 고려 20개 스레드)
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(analyze_v5_73_core, row): row for _, row in krx_list.iterrows()}
        completed = 0
        for future in as_completed(futures):
            completed += 1
            res = future.result()
            if res:
                results.append(res)
            
            # 50개 단위 UI 업데이트 (성능 최적화)
            if completed % 50 == 0 or completed == total_count:
                elapsed = time.time() - start_time
                avg = elapsed / completed
                rem = avg * (total_count - completed)
                prog_bar.progress(completed / total_count)
                status_text.markdown(f"**📡 스캔 현황:** `{completed}` / `{total_count}` 완료")
                time_text.markdown(f"**⏱️ 예상 남은 시간:** `{int(rem // 60)}분 {int(rem % 60)}초` ")

    prog_bar.empty()
    status_text.empty()
    time_text.empty()
    
    st.divider()
    
    if results:
        st.subheader(f"📊 백테스트 분석 데이터 ({len(results)}개 포착)")
        scan_df = pd.DataFrame(results)
        
        # 적합도 내림차순 정렬
        sorted_df = scan_df.sort_values(by='적합도', ascending=False)
        
        # 시각적 필터링 규칙 (90점 이상 강조)
        def highlight_fit(val):
            if val >= 90: return 'background-color: #d4edda; font-weight: bold; color: #155724'
            if val >= 70: return 'background-color: #fff3cd; color: #856404'
            return ''
            
        # 컬럼 순서 고정 및 출력
        display_cols = ["종목명", "종목코드", "적합도", "현재가", "유사도", "거래량비", "CV", "몸통비율", "거래대금(억)"]
        st.dataframe(
            sorted_df[display_cols].style.applymap(highlight_fit, subset=['적합도']),
            use_container_width=True, 
            hide_index=True
        )
        st.success(f"✅ 작전 완료 (소요 시간: {int((time.time() - start_time)//60)}분 {int((time.time() - start_time)%60)}초)")
    else:
        st.warning("⚠️ 적합도 50점 기준을 통과한 종목이 없습니다.")

st.info("💡 모든 지표가 검수되었습니다. 90점 이상(초록)은 정예 기체, 70점 이상(노랑)은 후보 기체입니다.")
