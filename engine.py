"""engine.py — 보유/평단/손익/시계열 재구성 로직.

이 앱은 보유 수량을 저장하지 않는다. 장부(transactions)를 처음부터
순서대로 재생(replay)해서 임의 시점의 상태를 그때그때 계산한다.

- 평단: 이동평균법 (한국 증권사 표시 방식과 동일 → 대조 검증 용이)
- 분할: corporate_actions를 거래와 함께 날짜순으로 재생 (수량×비율, 평단÷비율)
- 휴장일: forward-fill — "그날 이하 가장 최근 종가" 사용
- 통화: 원가는 종목 통화 그대로, 표시 시점에 환율로 환산 (기준 USD)
"""

import pandas as pd

import db
from prices import FX_TICKER


# ---------- 가격 조회 (forward-fill) ----------

def price_series_wide(start=None):
    """캐시된 모든 가격을 '날짜 × 티커' 표로 만든다.
    중간에 빈 날(휴장일)은 직전 종가로 채운다(forward-fill).
    행: 달력상 모든 날짜, 열: 티커(환율 KRW=X 포함)."""
    px = db.get_prices()
    if px.empty:
        return pd.DataFrame()

    wide = px.pivot(index="date", columns="ticker", values="close")
    wide.index = pd.to_datetime(wide.index)

    # 거래소마다 휴장일이 달라 날짜 구멍이 서로 다르다.
    # 달력상 모든 날짜로 늘린 뒤 ffill하면 어느 날짜를 물어도 답이 나온다.
    full_range = pd.date_range(wide.index.min(), wide.index.max(), freq="D")
    wide = wide.reindex(full_range).ffill()

    if start:
        wide = wide[wide.index >= pd.to_datetime(start)]
    return wide


def price_on(wide, ticker, date):
    """wide 표에서 특정 종목의 date 기준 가격(그날 이하 최근 종가).
    데이터가 아예 없으면 None."""
    if wide.empty or ticker not in wide.columns:
        return None
    s = wide.loc[wide.index <= pd.to_datetime(date), ticker].dropna()
    return None if s.empty else float(s.iloc[-1])


# ---------- 장부 재생 (핵심) ----------

def replay_holdings(as_of=None):
    """장부를 처음부터 as_of 날짜까지 재생해 종목별 상태를 계산한다.

    반환: {ticker: {"qty", "avg_cost", "invested", "realized", "dividends"}}
      qty       : 보유 수량 (분할 반영)
      avg_cost  : 이동평균 평단 (종목 통화 기준)
      invested  : 현재 보유분의 원가 (qty × avg_cost)
      realized  : 실현손익 누계 (매도 시 확정, 수수료 차감)
      dividends : 배당 수령 누계
    """
    txs = db.get_transactions()
    if as_of:
        txs = txs[txs["date"] <= str(as_of)]

    splits = db.get_corporate_actions()
    if as_of:
        splits = splits[splits["date"] <= str(as_of)]

    # 거래와 분할을 하나의 이벤트 목록으로 합쳐 날짜순 정렬.
    # 같은 날이면 분할(장 시작 전 효력)을 거래보다 먼저 처리한다.
    events = []
    for _, r in splits.iterrows():
        events.append((r["date"], 0, "split", r))
    for _, r in txs.iterrows():
        events.append((r["date"], 1, r["type"], r))
    events.sort(key=lambda e: (e[0], e[1]))

    state = {}

    def get(ticker):
        return state.setdefault(
            ticker, {"qty": 0.0, "avg_cost": 0.0, "realized": 0.0, "dividends": 0.0}
        )

    for _date, _order, kind, r in events:
        s = get(r["ticker"])

        if kind == "split":
            # 10:1 분할 → 수량 10배, 평단 1/10 (평가금액은 그대로)
            if s["qty"] > 0:
                s["qty"] *= r["ratio"]
                s["avg_cost"] /= r["ratio"]

        elif kind == "buy":
            # 이동평균법: 새 평단 = (기존 원가 + 새 매수금액) / 총수량
            cost_before = s["qty"] * s["avg_cost"]
            s["qty"] += r["quantity"]
            s["avg_cost"] = (cost_before + r["amount"] + r["fee"]) / s["qty"]

        elif kind == "sell":
            # 실현손익 = (매도단가 - 평단) × 수량 - 수수료. 평단은 변하지 않는다.
            s["realized"] += (r["price"] - s["avg_cost"]) * r["quantity"] - r["fee"]
            s["qty"] -= r["quantity"]
            if s["qty"] <= 1e-9:  # 전량 매도 시 부동소수점 찌꺼기 정리
                s["qty"] = 0.0
                s["avg_cost"] = 0.0

        elif kind == "dividend":
            s["dividends"] += r["amount"] - r["fee"]

    for s in state.values():
        s["invested"] = s["qty"] * s["avg_cost"]
    return state


# ---------- 실현손익 (매도 건별 / 종목별 롤업) ----------

def realized_log():
    """매도 건별 실현손익 로그.

    거래를 날짜순으로 재생하며 '각 매도 시점의 이동평균 평단'을 스냅샷해서
    실현손익 = (매도가 − 그 시점 평단) × 수량 − 수수료 로 계산한다.
    현재 평단으로 과거 매도를 소급 계산하지 않는 것이 핵심.

    반환: DataFrame[날짜, 티커, 이름, 통화, 수량, 매도가, 매도금액,
                    사용평단, 실현손익, 수익률(%)] — 통화는 종목 통화 기준.
    """
    txs = db.get_transactions()
    assets = db.get_assets().set_index("ticker")
    splits = db.get_corporate_actions()

    events = []
    for _, r in splits.iterrows():
        events.append((r["date"], 0, "split", r))
    for _, r in txs.iterrows():
        events.append((r["date"], 1, r["type"], r))
    events.sort(key=lambda e: (e[0], e[1]))

    state = {}  # ticker -> {qty, avg_cost}

    def get(t):
        return state.setdefault(t, {"qty": 0.0, "avg_cost": 0.0})

    rows = []
    for _date, _order, kind, r in events:
        s = get(r["ticker"])
        if kind == "split":
            if s["qty"] > 0:
                s["qty"] *= r["ratio"]
                s["avg_cost"] /= r["ratio"]
        elif kind == "buy":
            cost_before = s["qty"] * s["avg_cost"]
            s["qty"] += r["quantity"]
            s["avg_cost"] = (cost_before + r["amount"] + r["fee"]) / s["qty"]
        elif kind == "sell":
            avg = s["avg_cost"]            # ← 이 시점의 평단을 스냅샷
            qty = r["quantity"]
            realized = (r["price"] - avg) * qty - r["fee"]
            cost_basis = avg * qty
            ret = (realized / cost_basis * 100) if cost_basis > 0 else None
            name = assets.loc[r["ticker"], "name"] if r["ticker"] in assets.index else r["ticker"]
            cur = assets.loc[r["ticker"], "currency"] if r["ticker"] in assets.index else ""
            rows.append({
                "날짜": r["date"], "티커": r["ticker"], "이름": name, "통화": cur,
                "수량": qty, "매도가": r["price"], "매도금액": r["amount"],
                "사용평단": avg, "실현손익": realized, "수익률(%)": ret,
            })
            s["qty"] -= qty
            if s["qty"] <= 1e-9:      # 전량 매도 시 정리 (평단은 매도로 안 변함)
                s["qty"] = 0.0
                s["avg_cost"] = 0.0

    cols = ["날짜", "티커", "이름", "통화", "수량", "매도가", "매도금액",
            "사용평단", "실현손익", "수익률(%)"]
    return pd.DataFrame(rows, columns=cols)


def realized_rollup():
    """종목별 실현손익 롤업. 자본손익(매도 실현)과 배당을 별도 컬럼으로 분리.
    (미실현은 여기에 넣지 않는다 — 보유 표에서 따로 본다.)

    반환: DataFrame[티커, 이름, 통화, 실현자본손익, 배당, 합계] — 종목 통화 기준.
    """
    state = replay_holdings()
    assets = db.get_assets().set_index("ticker")

    rows = []
    for t, s in state.items():
        cap, div = s["realized"], s["dividends"]
        if abs(cap) < 1e-9 and abs(div) < 1e-9:
            continue
        name = assets.loc[t, "name"] if t in assets.index else t
        cur = assets.loc[t, "currency"] if t in assets.index else ""
        rows.append({"티커": t, "이름": name, "통화": cur,
                     "실현자본손익": cap, "배당": div, "합계": cap + div})

    cols = ["티커", "이름", "통화", "실현자본손익", "배당", "합계"]
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows).sort_values("합계", ascending=False).reset_index(drop=True)


# ---------- 예수금 (스냅샷 기반) ----------

def cash_snapshots(as_of=None):
    """통화별 최신 예수금. 사용자가 기록한 스냅샷 중
    as_of 이하 가장 최근 것을 쓴다. {통화: {"amount", "date"}}"""
    cb = db.get_cash_balances()
    if as_of:
        cb = cb[cb["date"] <= str(as_of)]
    out = {}
    for cur, g in cb.groupby("currency"):
        last = g.iloc[-1]  # date, id 순 정렬 상태
        out[cur] = {"amount": float(last["amount"]), "date": last["date"]}
    return out


# ---------- 현재 보유 표 ----------

def daily_return(ticker, as_of=None):
    """종목의 일간 수익률(%). 그 종목의 '연속된 두 거래일 종가'로 계산한다
    (종목 통화 기준 — 그 종목 자체의 하루 등락). 데이터 부족하면 None."""
    px = db.get_prices(ticker)
    if px.empty:
        return None
    if as_of:
        px = px[px["date"] <= str(as_of)]
    if len(px) < 2:
        return None
    last = float(px["close"].iloc[-1])
    prev = float(px["close"].iloc[-2])
    return (last / prev - 1) * 100 if prev else None


def holdings_table(as_of=None, price_as_of=None):
    """보유 표 DataFrame: 수량/평단/현재가/평가액($·₩)/비중/미실현손익/총수익률/일간.

    as_of: 보유 수량·현금을 이 날짜까지의 장부로 재구성.
    price_as_of: 평가에 쓸 가격 기준일 (기본 = as_of). 예를 들어 '매매 전 보유를
        그날 종가로 평가'하려면 as_of=전날, price_as_of=그날로 부른다.
    반환: (표, 총평가액USD, 환율, 가격기준일)"""
    assets = db.get_assets().set_index("ticker")
    state = replay_holdings(as_of)
    wide = price_series_wide()

    ref_date = price_as_of or as_of or (wide.index.max() if not wide.empty else None)
    fx = price_on(wide, FX_TICKER, ref_date) if ref_date is not None else None

    rows = []
    for ticker, s in state.items():
        if s["qty"] <= 0 or ticker not in assets.index:
            continue
        a = assets.loc[ticker]
        px = price_on(wide, ticker, ref_date)

        # 종목 통화 기준 평가액 → USD/KRW 병기 (환율 없으면 환산 불가 → None)
        native_value = s["qty"] * px if px is not None else None
        if native_value is None:
            usd_value = krw_value = unrealized = None
        elif a["currency"] == "KRW":
            usd_value = native_value / fx if fx else None
            krw_value = native_value
            unrealized = native_value - s["invested"]  # KRW 기준
            if fx:
                unrealized /= fx  # 표는 USD 기준으로 통일
        else:
            usd_value = native_value
            krw_value = native_value * fx if fx else None
            unrealized = native_value - s["invested"]

        # 총수익률·일간수익률은 종목 통화 기준(가격 vs 평단, 종목 자체 등락).
        # 환율 노이즈 없이 증권사 표시와 맞고, 환효과는 USD 평가액 쪽에 드러난다.
        total_ret = ((px - s["avg_cost"]) / s["avg_cost"] * 100
                     if (px is not None and s["avg_cost"] > 0) else None)
        day_ret = daily_return(ticker, ref_date)

        rows.append(
            {
                "티커": ticker,
                "이름": a["name"],
                "수량": s["qty"],
                "평단": s["avg_cost"],
                "통화": a["currency"],
                "현재가": px,
                "평가액($)": usd_value,
                "평가액(₩)": krw_value,
                "미실현($)": unrealized,
                "총수익률(%)": total_ret,
                "일간(%)": day_ret,
            }
        )

    # 현금도 종목처럼 한 줄로 넣는다 (가격은 항상 1, 미실현 없음)
    for cur, c in cash_snapshots(as_of).items():
        if c["amount"] <= 0:
            continue
        if cur == "KRW":
            usd_value = c["amount"] / fx if fx else None
            krw_value = c["amount"]
        else:
            usd_value = c["amount"]
            krw_value = c["amount"] * fx if fx else None
        rows.append(
            {
                "티커": f"현금-{cur}",
                "이름": f"현금 ({cur})",
                "수량": c["amount"],
                "평단": None,
                "통화": cur,
                "현재가": 1.0,
                "평가액($)": usd_value,
                "평가액(₩)": krw_value,
                "미실현($)": None,
                "총수익률(%)": None,
                "일간(%)": None,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df, 0.0, fx, ref_date

    total_usd = df["평가액($)"].sum()
    df["비중(%)"] = df["평가액($)"] / total_usd * 100
    df = df.sort_values("평가액($)", ascending=False).reset_index(drop=True)
    return df, total_usd, fx, ref_date


# ---------- 총자산 시계열 ----------

def quantity_series_wide(wide_index):
    """날짜 × 티커 '보유 수량' 표. 장부를 한 번 재생하면서
    수량이 바뀌는 시점만 기록한 뒤, 전체 날짜로 늘려 ffill한다."""
    txs = db.get_transactions()
    if txs.empty:
        return pd.DataFrame(index=wide_index)

    splits = db.get_corporate_actions()
    events = []
    for _, r in splits.iterrows():
        events.append((r["date"], 0, "split", r))
    for _, r in txs.iterrows():
        events.append((r["date"], 1, r["type"], r))
    events.sort(key=lambda e: (e[0], e[1]))

    qty = {}
    records = []  # (date, ticker, qty_after)
    for date_, _order, kind, r in events:
        t = r["ticker"]
        q = qty.get(t, 0.0)
        if kind == "split":
            q *= r["ratio"] if q > 0 else 1.0
        elif kind == "buy":
            q += r["quantity"]
        elif kind == "sell":
            q -= r["quantity"]
        else:
            continue  # dividend는 수량 불변
        qty[t] = q
        records.append((date_, t, q))

    snap = pd.DataFrame(records, columns=["date", "ticker", "qty"])
    snap["date"] = pd.to_datetime(snap["date"])
    # 같은 날 여러 거래가 있으면 마지막 상태만 남긴다
    snap = snap.groupby(["date", "ticker"]).last().reset_index()
    qwide = snap.pivot(index="date", columns="ticker", values="qty")
    qwide = qwide.reindex(wide_index).ffill().fillna(0.0)
    return qwide


def portfolio_value_series():
    """최초 거래일부터 일별 총자산(USD) Series를 만든다.
    각 종목: 수량 × 그날 종가(ffill) → KRW 종목은 그날 환율로 USD 환산 → 합산."""
    txs = db.get_transactions()
    wide = price_series_wide()
    if txs.empty or wide.empty:
        return pd.Series(dtype=float)

    start = pd.to_datetime(txs["date"].min())
    wide = wide[wide.index >= start]
    qwide = quantity_series_wide(wide.index)

    assets = db.get_assets().set_index("ticker")
    fx = wide[FX_TICKER] if FX_TICKER in wide.columns else None

    total = pd.Series(0.0, index=wide.index)
    for ticker in qwide.columns:
        if ticker not in wide.columns or ticker not in assets.index:
            continue
        value = qwide[ticker] * wide[ticker]
        if assets.loc[ticker, "currency"] == "KRW" and fx is not None:
            value = value / fx
        total = total.add(value.fillna(0.0), fill_value=0.0)

    # 예수금: 기록된 스냅샷을 계단식으로 이어붙인다
    # (기록과 기록 사이에는 마지막 잔고 유지, 첫 기록 전에는 0)
    cb = db.get_cash_balances()
    for cur, g in cb.groupby("currency"):
        s = g.groupby("date")["amount"].last()  # 같은 날 여러 번이면 마지막 값
        s.index = pd.to_datetime(s.index)
        s = s.reindex(wide.index).ffill().fillna(0.0)
        if cur == "KRW" and fx is not None:
            s = s / fx
        total = total.add(s.fillna(0.0), fill_value=0.0)

    return total


# ---------- 누적 손익 ----------

def cumulative_pnl():
    """누적 손익(단순) = 평가액 + 실현손익 + 배당 − 투입원가. 전부 USD 기준.
    반환: dict — 화면 요약 카드에 쓸 구성요소들."""
    _df, total_usd, fx, ref_date = holdings_table()
    state = replay_holdings()
    assets = db.get_assets().set_index("ticker")

    realized = dividends = invested = 0.0
    for ticker, s in state.items():
        # KRW 종목의 손익·원가는 환율로 USD 환산해 합산
        rate = fx if (ticker in assets.index and assets.loc[ticker, "currency"] == "KRW" and fx) else 1.0
        realized += s["realized"] / rate
        dividends += s["dividends"] / rate
        invested += s["invested"] / rate

    # 현금은 평가액에 포함되므로 원가에도 같은 금액을 넣는다
    # (현금 자체는 손익이 없어야 하므로 — 안 넣으면 누적손익이 현금만큼 부풀려진다)
    for cur, c in cash_snapshots().items():
        rate = fx if (cur == "KRW" and fx) else 1.0
        invested += c["amount"] / rate

    return {
        "평가액": total_usd,
        "실현손익": realized,
        "배당": dividends,
        "투입원가": invested,
        "누적손익": total_usd + realized + dividends - invested,
        "기준일": ref_date,
        "환율": fx,
    }
