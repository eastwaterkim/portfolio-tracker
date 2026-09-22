"""app.py — Streamlit 화면 (UI 전용).

계산은 engine.py, 저장은 db.py, 가격 수집은 prices.py에 맡기고
이 파일은 '보여주기'와 '입력받기'만 담당한다.
"""

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import analytics
import db
import engine
import prices

st.set_page_config(page_title="포트폴리오 트래커", page_icon="📊", layout="wide")

db.init_db()  # 테이블이 없으면 만든다 (있으면 아무 일도 안 함)

# 차트 색: 검증된 고정 순서 팔레트 (비중 큰 순서대로 이 순서로 칠한다)
PALETTE = ["#2a78d6", "#1baf7a", "#eda100", "#008300",
           "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
GRID = "#e1e0d9"
INK_MUTED = "#898781"


def first_trade_date():
    txs = db.get_transactions()
    return txs["date"].min() if not txs.empty else date.today().strftime("%Y-%m-%d")


def fmt_qty(v):
    """수량 표시: 자연수면 소수점 없이(150), 소수면 뒤 0 없이(0.02688)."""
    if pd.isna(v):
        return "—"
    v = float(v)
    if v.is_integer():
        return f"{v:,.0f}"
    return f"{v:,.8f}".rstrip("0").rstrip(".")


def portfolio_day_return(hdf):
    """보유 표(hdf)로 포트폴리오 전체의 그날 수익률(%)을 구한다.
    종목별 일간수익률을 평가액으로 가중평균(현금은 등락 0으로 분모에 포함)."""
    if hdf is None or hdf.empty:
        return None
    den = hdf["평가액($)"].sum()
    stocks = hdf[hdf["일간(%)"].notna()]
    if den <= 0 or stocks.empty:
        return None
    return float((stocks["평가액($)"] * stocks["일간(%)"]).sum() / den)


def weight_pie(df, height=430):
    """보유 표(df)로 비중 도넛 차트를 만든다. 대시보드·날짜조회에서 공용."""
    fig = go.Figure(
        go.Pie(
            labels=df["티커"], values=df["평가액($)"],
            hole=0.45, sort=False,
            marker=dict(colors=PALETTE[: len(df)],
                        line=dict(color="#ffffff", width=2)),
            textposition="inside", texttemplate="%{percent:.0%}",
            insidetextorientation="horizontal",
            hovertemplate="%{label}<br>$%{value:,.0f} (%{percent})<extra></extra>",
        )
    )
    fig.update_layout(
        margin=dict(t=10, b=10, l=10, r=10), height=height,
        uniformtext_minsize=11, uniformtext_mode="hide",
        showlegend=True,
        legend=dict(orientation="h", x=0.5, xanchor="center",
                    y=-0.05, yanchor="top", font=dict(size=12)),
    )
    return fig


def check_password():
    """비밀번호 관문. secrets에 APP_PASSWORD가 있을 때만 작동한다.
    (로컬 개발에는 secrets가 없으니 그대로 통과 → 배포 때만 잠금)"""
    try:
        pw = st.secrets["APP_PASSWORD"] if "APP_PASSWORD" in st.secrets else None
    except Exception:
        pw = None
    if not pw:
        return True                      # 비번 미설정(로컬) → 통과
    if st.session_state.get("auth_ok"):
        return True
    st.title("🔒 포트폴리오 트래커")
    entered = st.text_input("비밀번호", type="password")
    if entered:
        if entered == pw:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()


check_password()

st.title("📊 포트폴리오 트래커")

tab_dash, tab_realized, tab_calendar, tab_input, tab_manage = st.tabs(
    ["대시보드", "실현손익", "날짜 조회", "거래 입력", "관리"]
)


# ======================================================================
# 탭 1: 대시보드
# ======================================================================
with tab_dash:
    assets = db.get_assets()
    txs = db.get_transactions()

    if assets.empty or txs.empty:
        st.info("아직 데이터가 없습니다. [관리] 탭에서 종목을 등록하고 [거래 입력] 탭에서 거래를 넣어주세요.")
    elif db.get_prices().empty:
        st.warning("가격 데이터가 없습니다. 아래 버튼으로 가격을 받아오세요.")
        if st.button("가격 받아오기", key="fetch_first"):
            with st.spinner("yfinance에서 가격을 받아오는 중..."):
                prices.update_all(first_trade_date())
            st.rerun()
    else:
        # ---- 요약 카드 ----
        pnl = engine.cumulative_pnl()
        fx = pnl["환율"]
        simple = (pnl["누적손익"] / pnl["투입원가"] * 100) if pnl["투입원가"] else None
        profit = pnl["누적손익"]  # 수익금 (총 손익 금액)

        df, total, fx, ref_date = engine.holdings_table()  # 보유 표(아래에서도 재사용)
        day_ret = portfolio_day_return(df)                 # 오늘의 수익률

        profit_str = f"{'+' if profit >= 0 else '-'}${abs(profit):,.0f}"  # 부호로 시작(색 적용)

        col1, col2 = st.columns(2)
        col1.metric("평가금액", f"${pnl['평가액']:,.0f}",
                    f"{day_ret:+.2f}%" if day_ret is not None else None,
                    help="옆 화살표·색 = 오늘의 수익률(포트폴리오 전일 대비 등락). "
                         "오르면 초록↑, 내리면 빨강↓.")
        col2.metric("수익률 (원금 대비)",
                    f"{simple:+.2f}%" if simple is not None else "—",
                    profit_str,
                    help="큰 숫자 = 원금 대비 수익률(누적손익÷투입원가), "
                         "밑 = 수익금(손익 금액).")

        ref = pnl["기준일"]
        won = f"₩{pnl['평가액'] * fx:,.0f} · " if fx else ""
        st.caption(
            f"{won}가격 기준일: {pd.to_datetime(ref).date()} · 환율 ₩{fx:,.1f}/$"
            if ref is not None and fx else "가격 기준일: -"
        )
        if st.button("🔄 가격 갱신"):
            with st.spinner("증분 갱신 중..."):
                result = prices.update_all(first_trade_date())
            st.toast(f"갱신 완료: {sum(result.values())}개 새 가격", icon="✅")
            st.rerun()

        st.divider()

        # ---- 보유 표 + 원그래프 ----
        left, right = st.columns([3, 2])

        with left:
            st.subheader("현재 보유")
            if df.empty:
                st.info("보유 중인 종목이 없습니다.")
            else:
                # 대시보드 표시 컬럼: 티커만(이름 생략), 평가액은 달러만(원화 생략),
                # 종목별 총수익률·일간수익률 추가
                view = df[["티커", "수량", "평단", "통화", "현재가", "평가액($)",
                           "총수익률(%)", "일간(%)", "미실현($)", "비중(%)"]]
                st.dataframe(
                    view.style.format(
                        {
                            "수량": fmt_qty, "평단": "{:,.2f}", "현재가": "{:,.2f}",
                            "평가액($)": "{:,.2f}", "미실현($)": "{:+,.2f}",
                            "총수익률(%)": "{:+.2f}", "일간(%)": "{:+.2f}",
                            "비중(%)": "{:.1f}",
                        },
                        na_rep="—",  # 현금 행의 평단·수익률 등은 빈 값
                    ),
                    width="stretch", hide_index=True,
                )

        with right:
            st.subheader("비중")
            if not df.empty:
                st.plotly_chart(weight_pie(df), width="stretch")

        # ---- 총자산 시계열 ----
        st.subheader("총자산 추이 (USD)")
        ts = engine.portfolio_value_series()
        if not ts.empty:
            fig = go.Figure(
                go.Scatter(
                    x=ts.index, y=ts.values, mode="lines",
                    line=dict(color=PALETTE[0], width=2),
                    hovertemplate="%{x|%Y-%m-%d}<br>$%{y:,.0f}<extra></extra>",
                )
            )
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=10), height=380,
                xaxis=dict(showgrid=False, color=INK_MUTED),
                yaxis=dict(gridcolor=GRID, color=INK_MUTED, tickformat="$,.0f"),
                plot_bgcolor="rgba(0,0,0,0)", hovermode="x unified",
            )
            st.plotly_chart(fig, width="stretch")

        st.divider()

        # ---- 테마 비중 ----
        st.subheader("테마 비중")
        st.caption(
            "한 종목이 여러 테마에 속하면 테마 수로 나눠 균등 배분합니다 "
            "(예: 테마 2개면 각 테마에 절반씩). 그래서 합계는 100%가 됩니다. "
            "겹침까지 살린 '진짜 노출'은 아래 묶음 노출에서 확인하세요."
        )
        theme_df, theme_total = analytics.theme_exposure()
        if not theme_df.empty:
            tdf = theme_df.sort_values("비중(%)")  # 가로 막대는 아래→위로 커지게
            fig = go.Figure(
                go.Bar(
                    x=tdf["비중(%)"], y=tdf["테마"], orientation="h",
                    marker=dict(color=PALETTE[0]),
                    text=[f"{v:.1f}%" for v in tdf["비중(%)"]],
                    textposition="outside",
                    hovertemplate="%{y}<br>%{x:.1f}% ($%{customdata:,.0f})<extra></extra>",
                    customdata=tdf["평가액($)"],
                )
            )
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=40),
                height=max(240, 46 * len(tdf)),
                xaxis=dict(showgrid=True, gridcolor=GRID, color=INK_MUTED,
                           ticksuffix="%", range=[0, tdf["비중(%)"].max() * 1.18]),
                yaxis=dict(color=INK_MUTED),
                plot_bgcolor="rgba(0,0,0,0)",
            )
            chart_col, pie_col = st.columns([3, 2])
            chart_col.plotly_chart(fig, width="stretch")

            # 막대그래프의 수치와 같은 기준(다중 테마는 균등 배분)으로 원그래프를 그린다.
            # 테마가 팔레트보다 많아져도 색이 끊기지 않도록 반복한다.
            theme_colors = [PALETTE[i % len(PALETTE)] for i in range(len(theme_df))]
            theme_pie = go.Figure(
                go.Pie(
                    labels=theme_df["테마"], values=theme_df["평가액($)"],
                    hole=0.45, sort=False,
                    marker=dict(colors=theme_colors,
                                line=dict(color="#ffffff", width=2)),
                    textposition="inside", texttemplate="%{percent:.0%}",
                    insidetextorientation="horizontal",
                    hovertemplate="%{label}<br>$%{value:,.0f} (%{percent})<extra></extra>",
                )
            )
            theme_pie.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                height=max(300, min(430, 46 * len(theme_df))),
                uniformtext_minsize=11, uniformtext_mode="hide",
                showlegend=True,
                legend=dict(orientation="h", x=0.5, xanchor="center",
                            y=-0.08, yanchor="top", font=dict(size=12)),
            )
            pie_col.plotly_chart(theme_pie, width="stretch")
            st.caption(f"합계: {theme_df['비중(%)'].sum():.0f}%")

            # ---- 묶음(숨은 집중도) ----
            st.markdown("**묶음 노출** — 여러 테마를 하나로 합쳐서 봅니다 (종목 중복은 한 번만 계산). "
                        "다중 테마 종목도 전액 반영되므로, 위 막대 합보다 클 수 있습니다.")
            picked = st.multiselect(
                "합칠 테마 선택 (예: AI 광의 = 네오클라우드 + 데이터센터 + 반도체 + 메모리 반도체)",
                theme_df["테마"].tolist(),
            )
            if picked:
                val, pct, names = analytics.composite_exposure(picked)
                st.metric("묶음 노출", f"{pct:.1f}%", f"${val:,.0f}", delta_color="off")
                st.caption("포함 종목: " + ", ".join(names))


# ======================================================================
# 탭 2: 실현손익 (매도 건별 로그 + 종목별 롤업)
# ======================================================================
with tab_realized:
    st.subheader("실현손익")
    st.caption(
        "이동평균 평단을 쓰며, 각 매도 시점의 평단을 스냅샷해 계산합니다 "
        "(현재 평단으로 과거 매도를 소급하지 않습니다). 미실현 손익은 여기 넣지 않고 "
        "대시보드 보유 표에서 따로 봅니다. 금액은 종목 통화 기준입니다."
    )

    # 맨 위: 역대 누적 실현손익 (USD 합산, 배당 별도)
    _pnl = engine.cumulative_pnl()
    st.metric("역대 누적 실현손익", f"${_pnl['실현손익']:,.2f}",
              f"배당 ${_pnl['배당']:+,.2f}" if _pnl["배당"] else None,
              delta_color="off",
              help="지금까지 매도로 확정된 손익의 누계(USD 합산). 배당은 별도 표시.")

    log = engine.realized_log()
    roll = engine.realized_rollup()

    st.markdown("#### 종목별 실현손익 롤업")
    if roll.empty:
        st.info("아직 매도·배당 기록이 없습니다.")
    else:
        st.dataframe(
            roll.style.format({
                "실현자본손익": "{:+,.2f}", "배당": "{:+,.2f}", "합계": "{:+,.2f}",
            }),
            width="stretch", hide_index=True,
        )
        st.caption("자본손익 = 매도 실현손익 합, 배당 = 배당 수령액. 둘을 분리해 보여줍니다.")

    st.markdown("#### 실현매매 로그 (매도 건별)")
    if log.empty:
        st.info("아직 매도 기록이 없습니다.")
    else:
        show = log.sort_values("날짜", ascending=False)
        st.dataframe(
            show.style.format({
                "수량": "{:,.4f}", "매도가": "{:,.2f}", "매도금액": "{:,.2f}",
                "사용평단": "{:,.2f}", "실현손익": "{:+,.2f}", "수익률(%)": "{:+.1f}",
            }, na_rep="—"),
            width="stretch", hide_index=True,
        )
        st.download_button(
            "실현매매 로그 CSV",
            log.to_csv(index=False).encode("utf-8-sig"),
            "realized_log.csv", "text/csv",
        )


# ======================================================================
# 탭 3: 날짜 조회 (날짜 입력 → 그날 재구성)
# ======================================================================
with tab_calendar:
    st.subheader("날짜별 조회")
    st.caption(
        "날짜를 입력하면 그날의 매매·보유·비중·총자산을 장부에서 재구성해 보여줍니다 "
        "(따로 저장하지 않습니다)."
    )

    txs = db.get_transactions()
    if txs.empty:
        st.info("거래가 없습니다.")
    else:
        assets = db.get_assets()
        name_map = dict(zip(assets["ticker"], assets["name"]))

        first_d = pd.to_datetime(txs["date"].min()).date()
        today_d = date.today()

        picked_date = st.date_input(
            "조회할 날짜", value=today_d,
            min_value=first_d, max_value=today_d,
            help=f"입력 가능 범위: {first_d} ~ {today_d}",
        )
        picked = str(picked_date)
        prev = str(picked_date - timedelta(days=1))  # 그날 매매 직전 = 전날까지의 보유

        # 매매가 있던 날짜들을 힌트로 보여준다
        trade_days = sorted(txs["date"].unique(), reverse=True)
        with st.expander(f"매매가 있던 날 ({len(trade_days)}일) — 참고"):
            st.write(", ".join(trade_days))

        st.markdown(f"### 📅 {picked}")

        day_tx = txs[txs["date"] == picked].copy()
        has_trade = not day_tx.empty

        def show_snapshot(label, hdf, htotal):
            st.markdown(f"**{label} · 총자산 ${htotal:,.0f}**")
            if hdf.empty:
                st.caption("보유 종목이 없습니다 (측정 시작 이전이거나 전량 매도).")
                return
            tcol, pcol = st.columns([3, 2])
            tcol.dataframe(
                hdf[["티커", "수량", "현재가", "평가액($)", "비중(%)"]].style.format(
                    {"수량": fmt_qty, "현재가": "{:,.2f}",
                     "평가액($)": "{:,.2f}", "비중(%)": "{:.1f}"}, na_rep="—"),
                width="stretch", hide_index=True,
            )
            pcol.plotly_chart(weight_pie(hdf, height=340), width="stretch",
                              key=f"pie_{label}")

        # 매매 전/후를 '같은 날 종가'로 평가해 순수 매매 효과만 비교
        before = engine.holdings_table(as_of=prev, price_as_of=picked)
        after = engine.holdings_table(as_of=picked)

        # 그 날 수익률·총자산을 맨 위에 먼저 표시 (수익률은 색·화살표로)
        day_r = portfolio_day_return(after[0])
        st.metric("그 날 총자산", f"${after[1]:,.0f}",
                  f"{day_r:+.2f}%" if day_r is not None else None,
                  help="화살표·색 = 그 날 수익률(포트폴리오 전일 대비 등락). "
                       "종목별 일간수익률을 평가액으로 가중평균. 오르면 초록↑, 내리면 빨강↓.")

        # ① 매매 전
        st.markdown("#### ① 매매 전 (그날 시작)")
        show_snapshot("매매 전", before[0], before[1])

        # ② 그날의 매매
        st.markdown("#### ② 그날의 매매")
        if has_trade:
            day_tx.insert(1, "종목명", day_tx["ticker"].map(name_map))
            st.dataframe(day_tx[["종목명", "type", "quantity", "price", "amount"]],
                         width="stretch", hide_index=True)
        else:
            st.caption("그날은 매매가 없습니다 → 전/후가 동일합니다.")

        # ③ 매매 후
        st.markdown("#### ③ 매매 후 (그날 마감)")
        show_snapshot("매매 후", after[0], after[1])


# ======================================================================
# 탭 4: 거래 입력 (매매·배당 / 입출금 / 수정·삭제)
# ======================================================================
with tab_input:
    assets = db.get_assets()
    tickers = assets["ticker"].tolist() if not assets.empty else []
    # 저장은 늘 티커로 하되, 화면에는 등록된 이름으로 보여준다
    name_map = dict(zip(assets["ticker"], assets["name"]))
    fmt = lambda t: f"{name_map.get(t, t)} ({t})"

    st.subheader("매매·배당 입력")
    if not tickers:
        st.info("먼저 [관리] 탭에서 종목을 등록해주세요.")
    else:
        with st.form("tx_form", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            tx_date = c1.date_input("날짜", value=date.today())
            tx_ticker = c2.selectbox("종목", tickers, format_func=fmt)
            tx_type = c3.selectbox("유형", ["buy", "sell", "dividend"],
                                   format_func=lambda t: {"buy": "매수", "sell": "매도", "dividend": "배당"}[t])
            c4, c5, c6 = st.columns(3)
            tx_qty = c4.number_input("수량 (배당이면 0)", min_value=0.0, step=1.0, format="%.6f")
            tx_price = c5.number_input("단가 (배당이면 0)", min_value=0.0, step=0.01, format="%.4f")
            tx_amount = c6.number_input("배당 수령액 (배당일 때만)", min_value=0.0, step=0.01)
            c7, c8 = st.columns(2)
            tx_fee = c7.number_input("수수료·세금", min_value=0.0, step=0.01)
            tx_memo = c8.text_input("메모")

            if st.form_submit_button("저장"):
                # 저장 성공 시에만 rerun (연속 입력이 안정적으로 되도록).
                # 알림은 toast로 띄워 몇 초 뒤 자동으로 사라지게 한다.
                saved = False
                if tx_type == "dividend":
                    if tx_amount <= 0:
                        st.error("배당은 수령액을 입력해야 합니다.")
                    else:
                        db.add_transaction(str(tx_date), tx_ticker, "dividend",
                                           None, None, tx_amount, tx_fee, tx_memo)
                        st.toast("배당 저장 완료", icon="✅")
                        saved = True
                elif tx_qty <= 0 or tx_price <= 0:
                    st.error("매수/매도는 수량과 단가를 입력해야 합니다.")
                else:
                    # 엣지 케이스: 보유량보다 많이 팔 수 없다
                    held = (engine.replay_holdings(str(tx_date)).get(tx_ticker, {"qty": 0})["qty"]
                            if tx_type == "sell" else None)
                    if tx_type == "sell" and tx_qty > held + 1e-9:
                        st.error(f"매도 수량({tx_qty:g})이 해당일 보유 수량({held:g})보다 많습니다.")
                    else:
                        db.add_transaction(str(tx_date), tx_ticker, tx_type,
                                           tx_qty, tx_price, tx_qty * tx_price, tx_fee, tx_memo)
                        st.toast(f"{'매수' if tx_type == 'buy' else '매도'} 저장: "
                                 f"{fmt(tx_ticker)} {tx_qty:g}주 @ {tx_price:g}", icon="✅")
                        st.toast("💡 예수금이 바뀌었으면 아래 '예수금 기록'도 갱신하세요.")
                        saved = True
                if saved:
                    st.rerun()

    st.divider()
    st.subheader("입출금 입력 (IRR 재료 — 필수)")
    with st.form("cf_form", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns(4)
        cf_date = c1.date_input("날짜", value=date.today(), key="cf_date")
        cf_type = c2.selectbox("유형", ["deposit", "withdraw"],
                               format_func=lambda t: "입금" if t == "deposit" else "출금")
        cf_amount = c3.number_input("금액", min_value=0.0, step=100.0)
        cf_currency = c4.selectbox("통화", ["USD", "KRW"])
        if st.form_submit_button("저장"):
            if cf_amount <= 0:
                st.error("금액을 입력해주세요.")
            else:
                db.add_cashflow(str(cf_date), cf_type, cf_amount, cf_currency)
                st.toast("입출금 저장 완료", icon="✅")
                st.rerun()

    st.divider()
    st.subheader("예수금 기록 (현금 = 종목)")
    st.caption(
        "현금도 하나의 종목으로 취급합니다. 매매·입출금 후 현재 예수금을 기록하면 "
        "보유 표·비중·총자산에 반영됩니다. 다음 기록 전까지는 마지막 잔고가 유지됩니다."
    )

    snapshots = engine.cash_snapshots()
    if snapshots:
        cols = st.columns(len(snapshots))
        for col, (cur, c) in zip(cols, snapshots.items()):
            symbol = "$" if cur == "USD" else "₩"
            col.metric(f"현금 ({cur})", f"{symbol}{c['amount']:,.0f}",
                       f"기준일 {c['date']}", delta_color="off")

    with st.form("cash_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        cb_date = c1.date_input("기준일", value=date.today(), key="cb_date")
        cb_currency = c2.selectbox("통화", ["KRW", "USD"], key="cb_cur")
        cb_amount = c3.number_input("현재 예수금", min_value=0.0, step=1000.0)
        if st.form_submit_button("기록"):
            db.add_cash_balance(str(cb_date), cb_currency, cb_amount)
            st.toast("예수금 기록 완료", icon="✅")
            st.rerun()

    cb_hist = db.get_cash_balances()
    if not cb_hist.empty:
        with st.expander("예수금 기록 이력 (잘못 넣었으면 여기서 삭제)"):
            cb_view = cb_hist.sort_values(["date", "id"], ascending=False)
            cb_event = st.dataframe(
                cb_view, width="stretch", hide_index=True,
                on_select="rerun", selection_mode="single-row", key="cb_table",
            )
            if cb_event.selection.rows:
                cb_row = cb_view.iloc[cb_event.selection.rows[0]]
                if st.button(f"🗑 #{int(cb_row['id'])} {cb_row['date']} "
                             f"{cb_row['amount']:g} {cb_row['currency']} 삭제"):
                    db.delete_cash_balance(int(cb_row["id"]))
                    st.rerun()

    st.divider()
    st.subheader("거래 내역 — 수정·삭제")

    txs = db.get_transactions()
    if not txs.empty:
        # 필터로 좁힌 뒤 표에서 행을 클릭해 선택한다 (내역이 많아져도 찾기 쉽게)
        f1, f2, f3 = st.columns(3)
        f_tickers = f1.multiselect("종목 필터", sorted(txs["ticker"].unique()),
                                   format_func=fmt)
        f_types = f2.multiselect(
            "유형 필터", ["buy", "sell", "dividend"],
            format_func=lambda t: {"buy": "매수", "sell": "매도", "dividend": "배당"}[t],
        )
        f_period = f3.selectbox("기간", ["전체", "최근 30일", "최근 90일", "올해"])

        view = txs.sort_values(["date", "id"], ascending=False)  # 최신이 위로
        view.insert(2, "종목명", view["ticker"].map(name_map))  # 표에 이름 병기
        if f_tickers:
            view = view[view["ticker"].isin(f_tickers)]
        if f_types:
            view = view[view["type"].isin(f_types)]
        if f_period != "전체":
            today = pd.Timestamp.today()
            cutoff = {
                "최근 30일": today - pd.Timedelta(days=30),
                "최근 90일": today - pd.Timedelta(days=90),
                "올해": pd.Timestamp(today.year, 1, 1),
            }[f_period].strftime("%Y-%m-%d")
            view = view[view["date"] >= cutoff]

        st.caption(f"{len(view)}건 — 행 왼쪽 체크박스를 클릭하면 아래에 수정 폼이 열립니다")
        event = st.dataframe(
            view, width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row", key="tx_table",
        )

        selected = event.selection.rows  # 클릭된 행 번호 (표시된 표 기준)
        if not selected:
            st.info("수정하거나 삭제할 거래를 위 표에서 선택하세요.")
            row = None
        else:
            row = view.iloc[selected[0]]
            sel_id = int(row["id"])

        if row is not None:
            st.markdown(f"**선택된 거래:** #{sel_id} · {row['date']} · {fmt(row['ticker'])} · {row['type']}")
            with st.form("edit_tx"):
                c1, c2, c3 = st.columns(3)
                e_date = c1.text_input("날짜 (YYYY-MM-DD)", row["date"])
                e_qty = c2.number_input("수량", value=0.0 if pd.isna(row["quantity"]) else float(row["quantity"]), format="%.6f")
                e_price = c3.number_input("단가", value=0.0 if pd.isna(row["price"]) else float(row["price"]), format="%.4f")
                c4, c5 = st.columns(2)
                e_fee = c4.number_input("수수료", value=float(row["fee"]))
                e_memo = c5.text_input("메모", row["memo"])
                b1, b2 = st.columns(2)
                if b1.form_submit_button("수정 저장"):
                    if row["type"] == "dividend":
                        db.update_transaction(sel_id, e_date, row["ticker"], row["type"],
                                              None, None, row["amount"], e_fee, e_memo)
                    else:
                        db.update_transaction(sel_id, e_date, row["ticker"], row["type"],
                                              e_qty, e_price, e_qty * e_price, e_fee, e_memo)
                    st.toast("수정 완료", icon="✅"); st.rerun()
                if b2.form_submit_button("🗑 삭제", type="secondary"):
                    db.delete_transaction(sel_id)
                    st.toast("삭제 완료", icon="✅"); st.rerun()

    cfs = db.get_cashflows()
    if not cfs.empty:
        st.write("**입출금 내역** — 행을 선택하면 삭제 버튼이 나타납니다")
        cf_view = cfs.sort_values(["date", "id"], ascending=False)
        cf_event = st.dataframe(
            cf_view, width="stretch", hide_index=True,
            on_select="rerun", selection_mode="single-row", key="cf_table",
        )
        if cf_event.selection.rows:
            cf_row = cf_view.iloc[cf_event.selection.rows[0]]
            if st.button(f"🗑 #{int(cf_row['id'])} {cf_row['date']} "
                         f"{cf_row['amount']:g} {cf_row['currency']} 삭제"):
                db.delete_cashflow(int(cf_row["id"]))
                st.toast("삭제 완료", icon="✅"); st.rerun()


# ======================================================================
# 탭 5: 관리 (종목 등록 / 가격 / CSV 백업)
# ======================================================================
with tab_manage:
    st.subheader("종목 등록")
    with st.form("asset_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        a_ticker = c1.text_input("티커 (내부 식별자, 예: IREN, 005930, BTC)")
        a_symbol = c2.text_input("yfinance 심볼 (예: IREN, 005930.KS, BTC-USD)")
        c3, c4, c5 = st.columns(3)
        a_name = c3.text_input("표시 이름")
        a_currency = c4.selectbox("통화", ["USD", "KRW"])
        a_themes = c5.text_input("테마 태그 (콤마 구분)")
        if st.form_submit_button("등록"):
            if not (a_ticker and a_symbol and a_name):
                st.error("티커, 심볼, 이름은 필수입니다.")
            elif a_ticker.strip().upper() in db.get_assets()["ticker"].values:
                st.error("이미 등록된 티커입니다.")
            else:
                db.add_asset(a_ticker, a_symbol, a_name, a_currency, a_themes)
                st.toast(f"{a_name} 등록 완료", icon="✅"); st.rerun()

    assets = db.get_assets()
    if not assets.empty:
        st.write("**등록된 종목** (상장폐지·티커 변경 시 yf_symbol만 바꾸면 됩니다)")
        st.dataframe(assets, width="stretch", hide_index=True)

        m_map = dict(zip(assets["ticker"], assets["name"]))
        sel_asset = st.selectbox("수정할 종목", assets["ticker"].tolist(),
                                 format_func=lambda t: f"{m_map.get(t, t)} ({t})")
        arow = assets[assets["ticker"] == sel_asset].iloc[0]
        with st.form("edit_asset"):
            c1, c2, c3, c4 = st.columns(4)
            u_symbol = c1.text_input("yfinance 심볼", arow["yf_symbol"])
            u_name = c2.text_input("이름", arow["name"])
            u_currency = c3.selectbox("통화", ["USD", "KRW"],
                                      index=0 if arow["currency"] == "USD" else 1)
            u_themes = c4.text_input("테마", arow["themes"])
            if st.form_submit_button("수정 저장"):
                db.update_asset(sel_asset, u_symbol, u_name, u_currency, u_themes)
                st.toast("수정 완료", icon="✅"); st.rerun()

    st.divider()
    st.subheader("CSV 백업")
    st.caption("장부가 유일한 원본이므로 주기적으로 내려받아 두세요.")
    c1, c2, c3, c4 = st.columns(4)
    c1.download_button("거래 내역 CSV", db.get_transactions().to_csv(index=False).encode("utf-8-sig"),
                       "transactions.csv", "text/csv")
    c2.download_button("입출금 CSV", db.get_cashflows().to_csv(index=False).encode("utf-8-sig"),
                       "cashflows.csv", "text/csv")
    c3.download_button("종목 목록 CSV", db.get_assets().to_csv(index=False).encode("utf-8-sig"),
                       "assets.csv", "text/csv")
    c4.download_button("예수금 기록 CSV", db.get_cash_balances().to_csv(index=False).encode("utf-8-sig"),
                       "cash_balances.csv", "text/csv")
