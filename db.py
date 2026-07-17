"""db.py — 저장소 계층 (SQLAlchemy).

모든 데이터 읽기/쓰기는 이 파일을 통해서만 한다.
다른 파일(app.py, engine.py, analytics.py)은 SQL을 몰라도 되도록 함수로 감싼다.

접속 대상은 자동으로 정해진다:
  - DATABASE_URL 이 설정돼 있으면 → 그 클라우드 DB(Postgres, 예: Supabase)
  - 없으면 → 이 폴더의 로컬 SQLite 파일(portfolio.db)  ← 지금까지의 기본
DATABASE_URL 은 Streamlit secrets 또는 환경변수에서 읽는다(코드에 안 적는다).
덕분에 로컬에선 파일로, 배포 땐 클라우드 DB로 같은 코드가 동작한다.
"""

import os
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

# 로컬 기본 저장 파일 (DATABASE_URL 없을 때)
DB_PATH = Path(__file__).parent / "portfolio.db"

_engine = None


def _database_url():
    """접속 URL 결정: secrets/환경변수의 DATABASE_URL → 없으면 로컬 SQLite."""
    url = None
    try:
        import streamlit as st
        if "DATABASE_URL" in st.secrets:
            url = st.secrets["DATABASE_URL"]
    except Exception:
        url = None
    if not url:
        url = os.environ.get("DATABASE_URL")

    if url:
        # SQLAlchemy는 postgresql+psycopg2 드라이버 명시가 필요.
        # Supabase가 주는 postgresql://... 를 자동 보정한다.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg2://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url
    return f"sqlite:///{DB_PATH}"


def get_engine():
    """SQLAlchemy 엔진(연결 풀). 한 번 만들어 재사용한다."""
    global _engine
    if _engine is None:
        _engine = create_engine(_database_url(), future=True, pool_pre_ping=True)
    return _engine


def _is_postgres():
    return get_engine().dialect.name == "postgresql"


def get_connection():
    """하위호환용 raw DBAPI 연결 (로컬 스크립트/테스트에서 가끔 사용)."""
    return get_engine().raw_connection()


# ---------- 내부 헬퍼 (named 파라미터 :이름 사용) ----------

def _read(sql, params=None):
    """SELECT → DataFrame."""
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})


def _exec(sql, params=None):
    """INSERT/UPDATE/DELETE. params가 리스트면 executemany."""
    with get_engine().begin() as conn:
        conn.execute(text(sql), params if params is not None else {})


def _scalar(sql, params=None):
    """단일 값 조회."""
    with get_engine().connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def init_db():
    """필요한 테이블이 없으면 만든다 (SQLite/Postgres 양쪽 방언 대응).
    앱 시작 때마다 호출해도 안전(IF NOT EXISTS)."""
    pg = _is_postgres()
    pk = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    real = "DOUBLE PRECISION" if pg else "REAL"

    stmts = [
        # 종목 메타데이터
        """CREATE TABLE IF NOT EXISTS assets (
            ticker    TEXT PRIMARY KEY,
            yf_symbol TEXT NOT NULL,
            name      TEXT NOT NULL,
            currency  TEXT NOT NULL,
            themes    TEXT DEFAULT ''
        )""",
        # 매매·배당 이벤트
        f"""CREATE TABLE IF NOT EXISTS transactions (
            id       {pk},
            date     TEXT NOT NULL,
            ticker   TEXT NOT NULL,
            type     TEXT NOT NULL,
            quantity {real},
            price    {real},
            amount   {real} NOT NULL,
            fee      {real} DEFAULT 0,
            memo     TEXT DEFAULT ''
        )""",
        # 계좌 입출금
        f"""CREATE TABLE IF NOT EXISTS cashflows (
            id       {pk},
            date     TEXT NOT NULL,
            type     TEXT NOT NULL,
            amount   {real} NOT NULL,
            currency TEXT NOT NULL
        )""",
        # 가격 캐시 (환율 KRW=X도 여기 저장)
        f"""CREATE TABLE IF NOT EXISTS prices (
            ticker TEXT NOT NULL,
            date   TEXT NOT NULL,
            close  {real} NOT NULL,
            PRIMARY KEY (ticker, date)
        )""",
        # 주식분할
        f"""CREATE TABLE IF NOT EXISTS corporate_actions (
            ticker TEXT NOT NULL,
            date   TEXT NOT NULL,
            ratio  {real} NOT NULL,
            PRIMARY KEY (ticker, date)
        )""",
        # 예수금 스냅샷 (사용자가 직접 기록)
        f"""CREATE TABLE IF NOT EXISTS cash_balances (
            id       {pk},
            date     TEXT NOT NULL,
            currency TEXT NOT NULL,
            amount   {real} NOT NULL
        )""",
        # 수동 입력 지표 (Phase 3 알림용)
        f"""CREATE TABLE IF NOT EXISTS manual_metrics (
            date        TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            value       {real} NOT NULL,
            PRIMARY KEY (date, ticker, metric_name)
        )""",
    ]
    with get_engine().begin() as conn:
        for s in stmts:
            conn.execute(text(s))


# ---------- assets (종목) ----------

def add_asset(ticker, yf_symbol, name, currency, themes=""):
    _exec(
        "INSERT INTO assets (ticker, yf_symbol, name, currency, themes) "
        "VALUES (:ticker, :yf, :name, :cur, :themes)",
        {"ticker": ticker.strip().upper(), "yf": yf_symbol.strip(),
         "name": name.strip(), "cur": currency, "themes": themes.strip()},
    )


def get_assets():
    """모든 종목을 DataFrame으로 반환."""
    return _read("SELECT * FROM assets ORDER BY ticker")


def update_asset(ticker, yf_symbol, name, currency, themes):
    _exec(
        "UPDATE assets SET yf_symbol=:yf, name=:name, currency=:cur, themes=:themes "
        "WHERE ticker=:ticker",
        {"yf": yf_symbol.strip(), "name": name.strip(), "cur": currency,
         "themes": themes.strip(), "ticker": ticker},
    )


def delete_asset(ticker):
    _exec("DELETE FROM assets WHERE ticker=:ticker", {"ticker": ticker})


# ---------- transactions (매매·배당) ----------

def add_transaction(date, ticker, type_, quantity, price, amount, fee=0, memo=""):
    _exec(
        "INSERT INTO transactions (date, ticker, type, quantity, price, amount, fee, memo) "
        "VALUES (:date, :ticker, :type, :qty, :price, :amount, :fee, :memo)",
        {"date": date, "ticker": ticker, "type": type_, "qty": quantity,
         "price": price, "amount": amount, "fee": fee, "memo": memo},
    )


def get_transactions():
    """모든 거래를 날짜순 DataFrame으로 반환."""
    return _read("SELECT * FROM transactions ORDER BY date, id")


def update_transaction(id_, date, ticker, type_, quantity, price, amount, fee, memo):
    _exec(
        "UPDATE transactions SET date=:date, ticker=:ticker, type=:type, quantity=:qty, "
        "price=:price, amount=:amount, fee=:fee, memo=:memo WHERE id=:id",
        {"date": date, "ticker": ticker, "type": type_, "qty": quantity, "price": price,
         "amount": amount, "fee": fee, "memo": memo, "id": id_},
    )


def delete_transaction(id_):
    _exec("DELETE FROM transactions WHERE id=:id", {"id": id_})


# ---------- cashflows (입출금) ----------

def add_cashflow(date, type_, amount, currency):
    _exec(
        "INSERT INTO cashflows (date, type, amount, currency) "
        "VALUES (:date, :type, :amount, :cur)",
        {"date": date, "type": type_, "amount": amount, "cur": currency},
    )


def get_cashflows():
    return _read("SELECT * FROM cashflows ORDER BY date, id")


def delete_cashflow(id_):
    _exec("DELETE FROM cashflows WHERE id=:id", {"id": id_})


# ---------- cash_balances (예수금 스냅샷) ----------

def add_cash_balance(date, currency, amount):
    _exec(
        "INSERT INTO cash_balances (date, currency, amount) "
        "VALUES (:date, :cur, :amount)",
        {"date": date, "cur": currency, "amount": amount},
    )


def get_cash_balances():
    return _read("SELECT * FROM cash_balances ORDER BY date, id")


def delete_cash_balance(id_):
    _exec("DELETE FROM cash_balances WHERE id=:id", {"id": id_})


# ---------- prices (가격 캐시) ----------

def upsert_prices(df):
    """가격 DataFrame(ticker, date, close)을 저장. 이미 있는 (ticker,date)는 덮어쓴다."""
    if df.empty:
        return
    rows = [{"ticker": str(t), "date": str(d), "close": float(c)}
            for t, d, c in df[["ticker", "date", "close"]].itertuples(index=False, name=None)]
    if _is_postgres():
        sql = ("INSERT INTO prices (ticker, date, close) VALUES (:ticker, :date, :close) "
               "ON CONFLICT (ticker, date) DO UPDATE SET close = EXCLUDED.close")
    else:
        sql = "INSERT OR REPLACE INTO prices (ticker, date, close) VALUES (:ticker, :date, :close)"
    _exec(sql, rows)


def get_prices(ticker=None):
    """캐시된 가격 조회. ticker를 주면 그 종목만, 없으면 전부."""
    if ticker:
        return _read("SELECT * FROM prices WHERE ticker=:t ORDER BY date", {"t": ticker})
    return _read("SELECT * FROM prices ORDER BY ticker, date")


def get_last_price_date(ticker):
    """이 종목의 캐시에서 가장 최근 날짜. 없으면 None."""
    return _scalar("SELECT MAX(date) FROM prices WHERE ticker=:t", {"t": ticker})


# ---------- corporate_actions (주식분할) ----------

def upsert_corporate_actions(df):
    """분할 이벤트 DataFrame(ticker, date, ratio)을 저장."""
    if df.empty:
        return
    rows = [{"ticker": str(t), "date": str(d), "ratio": float(r)}
            for t, d, r in df[["ticker", "date", "ratio"]].itertuples(index=False, name=None)]
    if _is_postgres():
        sql = ("INSERT INTO corporate_actions (ticker, date, ratio) "
               "VALUES (:ticker, :date, :ratio) "
               "ON CONFLICT (ticker, date) DO UPDATE SET ratio = EXCLUDED.ratio")
    else:
        sql = ("INSERT OR REPLACE INTO corporate_actions (ticker, date, ratio) "
               "VALUES (:ticker, :date, :ratio)")
    _exec(sql, rows)


def get_corporate_actions(ticker=None):
    if ticker:
        return _read("SELECT * FROM corporate_actions WHERE ticker=:t ORDER BY date",
                     {"t": ticker})
    return _read("SELECT * FROM corporate_actions ORDER BY ticker, date")
