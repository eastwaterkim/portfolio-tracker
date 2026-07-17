"""migrate_to_cloud.py — 로컬 SQLite(portfolio.db) 데이터를 클라우드 DB로 1회 이전.

사용법 (Supabase 접속 URL을 환경변수로 준 뒤 실행):
    Git Bash 예:
        DATABASE_URL="postgresql://postgres:암호@호스트:5432/postgres" \
            ./venv/Scripts/python.exe migrate_to_cloud.py

클라우드에 테이블을 만들고, 로컬의 모든 행을 통째로 넣는다.
id 컬럼은 클라우드에서 새로 자동 부여(내부용이라 값 자체는 의미 없음).
"""

import os
import sqlite3
import sys

import pandas as pd

LOCAL = os.path.join(os.path.dirname(__file__), "portfolio.db")
TABLES = ["assets", "transactions", "cashflows", "prices",
          "corporate_actions", "cash_balances", "manual_metrics"]
AUTOID = {"transactions", "cashflows", "cash_balances"}  # id 자동생성 테이블


def main():
    # 1) 로컬에서 읽기 (db.py를 거치지 않고 직접 — db.py는 이미 클라우드를 볼 수 있음)
    src = sqlite3.connect(LOCAL)
    data = {t: pd.read_sql(f"SELECT * FROM {t}", src) for t in TABLES}
    src.close()

    # 2) 클라우드에 테이블 생성 (db.py가 secrets/환경변수의 URL을 보고 Postgres로 연결)
    import db
    if db.get_engine().dialect.name == "sqlite":
        sys.exit("클라우드 DATABASE_URL이 설정되지 않았습니다 "
                 "(.streamlit/secrets.toml 또는 환경변수 확인).")
    print("이전 대상:", db.get_engine().url.render_as_string(hide_password=True))
    db.init_db()

    # 3) 각 테이블 통째로 삽입
    eng = db.get_engine()
    for t in TABLES:
        df = data[t]
        if t in AUTOID and "id" in df.columns:
            df = df.drop(columns=["id"])   # 클라우드가 새 id 부여
        if not df.empty:
            df.to_sql(t, eng, if_exists="append", index=False)
        print(f"  {t}: {len(df)} 행 이전")

    print("완료. 클라우드 DB로 데이터 이전이 끝났습니다.")


if __name__ == "__main__":
    main()
