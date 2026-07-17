"""analytics.py — Phase 2 분석 계산.

engine.py가 "지금 얼마 갖고 있나"를 담당한다면, 이 파일은 그 위에서
"어떻게 나뉘어 있나(테마)", "포트폴리오 자체 성과는(TWRR)", "지수 대비 어떤가(벤치마크)",
"고점 대비 얼마나 빠졌나(드로다운)"를 계산한다.

수익률 지표는 TWRR(시간가중 수익률)을 쓴다. 현금흐름(입출금) 날짜마다 구간을 쪼개
각 구간 수익률을 기하 연결(곱)하므로, 납입 타이밍 효과가 상쇄되고 포트폴리오 자체
성과만 남는다 → 벤치마크(매수후보유)와 직접 비교 가능.
"""

from datetime import date

import pandas as pd

import db
import engine
from prices import FX_TICKER


# ---------- 테마 노출 ----------

def _themes_of(ticker, assets):
    """한 종목의 테마 태그 리스트. 현금 행은 '현금성', 태그 없으면 '미분류'."""
    if str(ticker).startswith("현금-"):
        return ["현금성"]
    if ticker in assets.index:
        raw = assets.loc[ticker, "themes"]
        tags = [s.strip() for s in str(raw).split(",") if s.strip()]
        if tags:
            return tags
    return ["미분류"]


def theme_exposure(as_of=None):
    """테마별 비중. 한 종목이 여러 테마에 속하면 테마 수로 나눠 균등 배분한다
    (예: 테마 2개면 각 테마에 평가액의 절반씩). 그래서 합계가 100%가 된다.

    겹침을 그대로 살린 '노출' 관점은 composite_exposure(묶음)에서 본다.

    반환: (DataFrame[테마, 평가액($), 비중(%)] 내림차순, 총평가액USD)
    """
    df, total, _fx, _ref = engine.holdings_table(as_of)
    if df.empty or total <= 0:
        return pd.DataFrame(columns=["테마", "평가액($)", "비중(%)"]), total

    assets = db.get_assets().set_index("ticker")
    exposure = {}
    for _, row in df.iterrows():
        themes = _themes_of(row["티커"], assets)
        share = row["평가액($)"] / len(themes)  # 여러 테마면 균등 분할
        for th in themes:
            exposure[th] = exposure.get(th, 0.0) + share

    out = pd.DataFrame(
        [{"테마": k, "평가액($)": v, "비중(%)": v / total * 100}
         for k, v in exposure.items()]
    ).sort_values("비중(%)", ascending=False).reset_index(drop=True)
    return out, total


def composite_exposure(selected_themes, as_of=None):
    """여러 테마를 하나로 묶은 노출. 한 종목이 선택된 테마 중 둘 이상에
    속해도 '한 번만' 센다(테마별 단순 합산과 달리 이중계상 없음).
    스펙의 'AI 광의 노출 = AI캐펙스 + 메모리' 같은 숨은 집중도 확인용.

    반환: (묶음 평가액USD, 묶음 노출%, 포함된 종목 이름 리스트)
    """
    df, total, _fx, _ref = engine.holdings_table(as_of)
    if df.empty or total <= 0 or not selected_themes:
        return 0.0, 0.0, []

    assets = db.get_assets().set_index("ticker")
    selected = set(selected_themes)
    value = 0.0
    names = []
    for _, row in df.iterrows():
        if selected & set(_themes_of(row["티커"], assets)):
            value += row["평가액($)"]
            names.append(row["이름"])
    return value, value / total * 100, names


# ---------- TWRR (시간가중 수익률) ----------

def _to_usd(amount, currency, on_date, wide):
    """cashflow 금액을 USD로 환산. KRW면 그날 환율(forward-fill)로 나눈다."""
    if currency == "USD":
        return amount
    fx = engine.price_on(wide, FX_TICKER, on_date)
    return amount / fx if fx else amount  # 환율 없으면 원금 그대로(최선의 근사)


def _net_flows_usd(wide):
    """날짜별 순 현금흐름(USD). 입금 +, 출금 −. {Timestamp(정규화): USD}"""
    flows = {}
    for _, r in db.get_cashflows().iterrows():
        d = pd.to_datetime(r["date"]).normalize()
        usd = _to_usd(r["amount"], r["currency"], r["date"], wide)
        flows[d] = flows.get(d, 0.0) + (usd if r["type"] == "deposit" else -usd)
    return flows


def _twrr_daily_returns(start, end):
    """[start, end] 구간의 일별 시간가중 수익률 Series.

    입출금은 '그날 값에서 빼서(직전 평가액)' 제거하므로 시장 수익만 남는다:
      일수익률 r_t = (V_t − 그날 순유입 F_t) / V_{t-1} − 1
    (규칙: 입출금은 그날 종가 후 반영 = V_t는 유입 반영 후 값 → F_t를 빼면 직전 값)
    """
    ts = engine.portfolio_value_series()
    if ts.empty:
        return None
    start = pd.to_datetime(start) if start is not None else ts.index.min()
    end = pd.to_datetime(end) if end is not None else ts.index.max()
    V = ts[(ts.index >= start) & (ts.index <= end)]
    if len(V) < 2:
        return None

    flows = _net_flows_usd(engine.price_series_wide())
    dates = V.index
    rec = []
    for i in range(1, len(dates)):
        v0 = float(V.iloc[i - 1])
        v1 = float(V.iloc[i])
        f = flows.get(dates[i].normalize(), 0.0)
        rec.append((dates[i], ((v1 - f) / v0 - 1) if v0 > 0 else 0.0))
    return pd.Series([r for _, r in rec], index=[d for d, _ in rec])


def twrr(start=None, end=None):
    """시간가중 수익률. 현금흐름 날짜마다 구간을 쪼개 기하 연결한다.

    반환: dict
      ok         : 계산 가능 여부
      cumulative : 누적 총수익률 (소수, 0.1 = 10%)
      cagr       : 연평균 복리(CAGR, 연율화). 기간 짧으면 None
      days, start, end
      segments   : [(구간시작, 구간끝, 구간수익률)] — 현금흐름 경계로 분할
      n_flows    : 구간을 나눈 현금흐름 수
      msg        : 계산 불가 시 안내
    """
    txs = db.get_transactions()
    if txs.empty:
        return {"ok": False, "msg": "거래가 없습니다."}
    first = pd.to_datetime(txs["date"].min())
    start = pd.to_datetime(start) if start else first
    end = pd.to_datetime(end) if end else pd.Timestamp(date.today())
    if start < first:
        start = first

    r = _twrr_daily_returns(start, end)
    if r is None or len(r) == 0:
        return {"ok": False, "msg": "기간이 짧아 아직 계산할 수 없습니다 (총자산 이력 필요)."}

    cumulative = float((1 + r).prod() - 1)
    real_end = r.index[-1]
    days = (real_end - start).days
    cagr = ((1 + cumulative) ** (365.0 / days) - 1) if days > 0 and cumulative > -1 else None

    # 현금흐름 경계로 구간 분할 (각 구간은 일수익률의 기하곱)
    flows = _net_flows_usd(engine.price_series_wide())
    boundaries = sorted(d for d in flows if start < d <= real_end)
    segments = []
    prev = start
    for b in boundaries + [real_end]:
        if b == prev:
            continue
        mask = (r.index > prev) & (r.index <= b)
        seg_r = float((1 + r[mask]).prod() - 1) if mask.any() else 0.0
        segments.append((prev, b, seg_r))
        prev = b

    return {"ok": True, "cumulative": cumulative, "cagr": cagr, "days": days,
            "start": start, "end": real_end, "segments": segments,
            "n_flows": len(boundaries)}


def twrr_index_series(start=None, end=None):
    """TWRR 누적 성장 지수(시작=100). 벤치마크 비교 차트용. 없으면 None."""
    txs = db.get_transactions()
    if txs.empty:
        return None
    first = pd.to_datetime(txs["date"].min())
    start = pd.to_datetime(start) if start else first
    if start < first:
        start = first
    r = _twrr_daily_returns(start, end)
    if r is None or len(r) == 0:
        return None
    idx = (1 + r).cumprod() * 100
    return pd.concat([pd.Series([100.0], index=[start]), idx])


def period_options():
    """기간 필터 선택지: 전체 / YTD / 데이터에 존재하는 각 연도."""
    txs = db.get_transactions()
    if txs.empty:
        return ["전체"]
    years = sorted({d[:4] for d in txs["date"]}, reverse=True)
    return ["전체", "YTD"] + [f"{y}년" for y in years]


def period_window(period):
    """기간 문자열 → (start, end). '전체'는 (None, None)."""
    today = pd.Timestamp(date.today())
    if period == "YTD":
        return pd.Timestamp(today.year, 1, 1), today
    if period.endswith("년") and period[:-1].isdigit():
        y = int(period[:-1])
        return pd.Timestamp(y, 1, 1), pd.Timestamp(y, 12, 31)
    return None, None  # 전체


# ---------- 드로다운 / MDD ----------

def drawdown():
    """총자산의 언더워터 곡선과 최대낙폭(MDD).

    각 날짜의 낙폭% = (그날 값 / 그날까지의 최고값 − 1) × 100  (0 이하).
    반환: dict 또는 None
      series      : 날짜별 낙폭% Series (0 = 신고점, 음수 = 고점 아래)
      mdd         : 최대낙폭%(가장 깊은 값)
      peak_date   : 그 낙폭의 직전 고점 날짜
      trough_date : 바닥 날짜
      recovery    : 고점 회복 날짜(아직이면 None)
    """
    ts = engine.portfolio_value_series()
    if ts.empty or len(ts) < 2:
        return None

    running_max = ts.cummax()
    dd = (ts / running_max - 1) * 100  # 0 이하

    trough_date = dd.idxmin()
    mdd = float(dd.loc[trough_date])
    peak_date = ts.loc[:trough_date].idxmax()      # 바닥 직전의 고점
    peak_val = float(ts.loc[peak_date])

    after = ts.loc[trough_date:]
    recovered = after[after >= peak_val]
    recovery = recovered.index[0] if not recovered.empty else None

    return {
        "series": dd,
        "mdd": mdd,
        "peak_date": peak_date,
        "trough_date": trough_date,
        "recovery": recovery,
    }


# ---------- 벤치마크 비교 ----------

def _benchmark_close(label):
    """벤치마크(BM_라벨)의 일별 종가 Series (달력 전체로 forward-fill). 없으면 None."""
    px = db.get_prices(f"BM_{label}")
    if px.empty:
        return None
    s = px.set_index("date")["close"]
    s.index = pd.to_datetime(s.index)
    full = pd.date_range(s.index.min(), s.index.max(), freq="D")
    return s.reindex(full).ffill()


def benchmark_twrr_index(labels, start=None, end=None):
    """포트폴리오 TWRR 지수(시작=100) vs 각 벤치마크 지수(시작=100).
    TWRR은 납입 효과를 제거하므로 벤치마크(매수후보유)와 같은 잣대로 비교된다.
    반환: {이름: 지수 Series} 또는 None"""
    port = twrr_index_series(start, end)
    if port is None or len(port) < 2:
        return None

    out = {"내 포트폴리오 (TWRR)": port}
    lo, hi = port.index.min(), port.index.max()
    for lb in labels:
        s = _benchmark_close(lb)
        if s is None:
            continue
        s = s[(s.index >= lo) & (s.index <= hi)]
        if s.empty or s.iloc[0] == 0:
            continue
        out[lb] = s / s.iloc[0] * 100
    return out


def benchmark_returns(labels, start=None, end=None):
    """같은 창에서 포트폴리오 TWRR 총수익률 vs 각 벤치마크 총수익률(매수후보유).
    반환: DataFrame[대상, 총수익률(%)] 또는 None"""
    tw = twrr(start, end)
    if not tw.get("ok"):
        return None
    lo, hi = tw["start"], tw["end"]

    rows = [{"대상": "내 포트폴리오 (TWRR)", "총수익률(%)": tw["cumulative"] * 100}]
    for lb in labels:
        s = _benchmark_close(lb)
        if s is None:
            continue
        s = s[(s.index >= lo) & (s.index <= hi)]
        if s.empty or s.iloc[0] == 0:
            continue
        rows.append({"대상": lb, "총수익률(%)": (s.iloc[-1] / s.iloc[0] - 1) * 100})
    return pd.DataFrame(rows)
