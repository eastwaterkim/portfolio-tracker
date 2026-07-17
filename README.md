# 포트폴리오 트래커

거래장부 기반 개인용 포트폴리오 대시보드 (Streamlit).

---

## 앱 실행하는 법

터미널(PowerShell)을 열고 아래 4줄을 순서대로 입력합니다.

```powershell
# 1. 프로젝트 폴더로 이동
cd "C:\Users\user\Desktop\Portfolio_tracker"

# 2. 가상환경 켜기 (성공하면 줄 앞에 (venv) 표시가 붙음)
venv\Scripts\activate

# 3. 앱 실행 (브라우저가 자동으로 열림)
streamlit run app.py
```

- 브라우저가 안 열리면 주소창에 **localhost:8501** 을 직접 입력
- **끄는 법:** 터미널에서 `Ctrl + C`, 또는 터미널 창을 닫기

> 터미널을 처음 열려면: `Windows 키` → `PowerShell` 검색 → 실행
> (VSCode에서는 상단 메뉴 `Terminal → New Terminal`)

---

## 매일 쓸 때 팁

- 앱을 열면 대시보드의 **🔄 가격 갱신** 버튼을 눌러주세요.
  → 그날 총자산이 기록되며 시계열·드로다운·IRR 곡선이 하루씩 채워집니다.
- 입금/출금을 하면 **거래 입력 탭**에 그때그때 기록하세요 (IRR·벤치마크 계산 재료).
- 매매 후에는 **예수금 기록**도 갱신하면 현금 비중이 정확해집니다.

---

## 파일 구조 (역할 분리)

| 파일 | 역할 |
|---|---|
| `app.py` | 화면 (UI만) — 5개 탭: 대시보드 / 분석 / 달력 / 거래 입력 / 관리 |
| `db.py` | 데이터 저장·조회 (SQLite) |
| `prices.py` | 가격·환율·분할 수집 (yfinance 격리층) |
| `engine.py` | 보유·평단·손익·시계열 재구성 |
| `analytics.py` | 테마 노출 · IRR · 드로다운 · 벤치마크 (Phase 2) |
| `portfolio.db` | 데이터 파일 (git 제외 — 개인정보) |

> **참고:** `db.py` / `prices.py` / `engine.py` / `analytics.py` 같은 부품 파일을
> 수정하면 서버를 껐다 켜야(재시작) 반영됩니다. `app.py`만 고쳤을 때는
> 브라우저 새로고침으로 충분합니다.

---

## 환경 다시 설치해야 할 때

가상환경이 깨졌거나 다른 PC에서 열 때:

```powershell
cd "C:\Users\user\Desktop\Portfolio_tracker"
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```
