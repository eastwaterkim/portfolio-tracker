"""prices.py — 가격·환율·분할 수집 계층 (yfinance 격리층).

yfinance는 이 파일 안에서만 import한다.
yfinance가 깨지면 이 파일만 고치면 되도록 격리하는 것이 목적.

핵심 규칙: auto_adjust=False로 '원본 종가(raw close)'를 받는다.
수정주가는 분할·배당을 소급 반영한 값이라 실제 체결가 장부와 섞으면 틀어진다.
분할은 corporate_actions 테이블로 명시적으로 처리한다.
"""

from datetime import date, timedelta

import pandas as pd
import yfinance as yf

import db

# 환율은 일반 종목처럼 prices 테이블에 이 티커로 저장한다 (1 USD = ? KRW)
FX_TICKER = "KRW=X"

# 벤치마크: {가격캐시에 저장할 이름: yfinance 심볼}
# 사용자 보유 종목과 안 섞이게 'BM_' 접두어로 저장한다
BENCHMARKS = {"SPY": "SPY", "QQQ": "QQQ", "BTC": "BTC-USD"}


def _download_raw_close(yf_symbol, start):
    """yfinance에서 start일부터 오늘까지 원본 종가를 받아 DataFrame(date, close)으로 반환.
    실패하거나 데이터가 없으면 빈 DataFrame을 반환한다 (앱이 죽지 않도록)."""
    # yfinance의 end는 '미만'(exclusive)이라, 오늘 종가를 받으려면 내일을 넘겨야 한다.
    end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        raw = yf.download(
            yf_symbol,
            start=start,
            end=end,
            auto_adjust=False,  # 수정주가 금지 — 원본 종가만
            progress=False,
        )
    except Exception:
        return pd.DataFrame(columns=["date", "close"])

    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "close"])

    # yfinance는 컬럼이 2층(MultiIndex)으로 올 때가 있어 1층으로 평탄화한다
    close = raw["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]

    out = close.dropna().reset_index()
    out.columns = ["date", "close"]
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    return out


def update_prices(ticker, yf_symbol, first_needed_date):
    """한 종목의 가격 캐시를 최신으로 갱신한다 (증분 + 최신일 재요청).

    캐시가 있으면 '마지막 캐시 날짜부터' 다시 요청한다. 다음 날이 아니라 그 날부터
    받는 이유: 장중에는 오늘 종가가 아직 확정 전이라 계속 움직인다. 다음 날부터만
    받으면 오늘 값이 첫 갱신에 고정되므로, 마지막 날을 다시 받아 덮어쓴다
    (upsert = INSERT OR REPLACE). 캐시가 비어 있으면 first_needed_date 10일 앞에서부터.
    """
    last = db.get_last_price_date(ticker)
    if last:
        start = last  # 다음 날이 아니라 마지막 날부터 → 오늘 값이 매 갱신마다 최신화
    else:
        # 새 종목: first_needed_date가 오늘(기초 잔고)이고 그날이 주말·휴장일이면
        # 그날 데이터가 없다. 10일 앞에서부터 받아 최근 종가를 확보하고,
        # forward-fill이 오늘 평가에 그 값을 쓰게 한다.
        start = (pd.to_datetime(first_needed_date) - timedelta(days=10)).strftime("%Y-%m-%d")

    new = _download_raw_close(yf_symbol, start)
    if new.empty:
        return 0

    new["ticker"] = ticker
    db.upsert_prices(new)
    return len(new)


def update_splits(ticker, yf_symbol):
    """주식분할 이벤트를 받아 corporate_actions에 저장한다."""
    try:
        splits = yf.Ticker(yf_symbol).splits  # 날짜별 분할 비율 Series
    except Exception:
        return 0

    if splits is None or len(splits) == 0:
        return 0

    df = splits.reset_index()
    df.columns = ["date", "ratio"]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["ticker"] = ticker
    db.upsert_corporate_actions(df)
    return len(df)


def update_all(first_needed_date):
    """등록된 모든 종목 + 환율의 가격·분할 정보를 갱신한다.
    반환값: {티커: 새로 받은 행 수} — 화면에서 갱신 결과를 보여주기 위함."""
    assets = db.get_assets()
    result = {}

    for _, row in assets.iterrows():
        n = update_prices(row["ticker"], row["yf_symbol"], first_needed_date)
        update_splits(row["ticker"], row["yf_symbol"])
        result[row["ticker"]] = n

    # 환율(원/달러)도 같은 방식으로 캐시
    result[FX_TICKER] = update_prices(FX_TICKER, FX_TICKER, first_needed_date)
    return result


def update_benchmarks(first_needed_date):
    """벤치마크(SPY·QQQ·BTC) 가격을 캐시에 갱신한다.
    'BM_SPY'처럼 접두어를 붙여 사용자 종목과 분리해 저장한다."""
    result = {}
    for label, symbol in BENCHMARKS.items():
        result[label] = update_prices(f"BM_{label}", symbol, first_needed_date)
    return result
