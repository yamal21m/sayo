import json
import math
import os
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta

import yfinance as yf

# 한국 시간(KST) 표시용
KST = timezone(timedelta(hours=9))
now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M KST 업데이트")


def load_previous_state():
  """이전 data.json이 존재하면 읽어서 직전 F&G 및 종목 데이터를 복구용으로 로드합니다."""
  empty = {
      "fng": 50,
      "fng_label": "Neutral",
      "items": {},
      "ratios": {},
      "kimchi": None,
      "usFng": None,
      "vix": None,
      "move": None,
      "gvz": None,
      "ovx": None,
      "kospiVol": None,
      "fxVol": None,
  }
  if not os.path.exists("data.json"):
    return empty
  try:
    with open("data.json", "r", encoding="utf-8") as f:
      data = json.load(f)

    prev_market = data.get("market") or {}
    prev_crypto_fng = prev_market.get("cryptoFng") or {}

    prev_fng = prev_crypto_fng.get("value")
    prev_fng_label = prev_crypto_fng.get("label") or "Neutral"
    if not isinstance(prev_fng, (int, float)):
      prev_fng = 50
      for r in data.get("ratios", []):
        if "공포" in r.get("title", ""):
          m = re.search(r"(\d+)점", r.get("desc", ""))
          if m:
            prev_fng = int(m.group(1))

    prev_items = {}
    for cat in data.get("categories", []):
      for it in cat.get("items", []):
        prev_items[it["ticker"]] = it

    # 상대 가성비 비교는 시세가 없을 때 직전 값을 그대로 유지합니다.
    prev_ratios = {}
    for r in data.get("ratios", []):
      if isinstance(r, dict) and r.get("title"):
        prev_ratios[r["title"]] = r

    return {
        "fng": int(prev_fng),
        "fng_label": prev_fng_label,
        "items": prev_items,
        "ratios": prev_ratios,
        "kimchi": prev_market.get("kimchi"),
        "usFng": prev_market.get("usFng"),
        "vix": prev_market.get("vix"),
        "move": prev_market.get("move"),
        "gvz": prev_market.get("gvz"),
        "ovx": prev_market.get("ovx"),
        "kospiVol": prev_market.get("kospiVol"),
        "fxVol": prev_market.get("fxVol"),
    }
  except Exception as e:
    print(f"Warning: could not read previous data.json: {e}")
    return empty


# 1. trade.xyz / Hyperliquid 무료 펀딩비 가져오기 (키 불필요, 3회 재시도)
def get_hl_funding(max_retries=3):
  url = "https://api.hyperliquid.xyz/info"
  payload = json.dumps({"type": "metaAndAssetCtxs"}).encode("utf-8")
  headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}

  for attempt in range(1, max_retries + 1):
    try:
      req = urllib.request.Request(url, data=payload, headers=headers)
      with urllib.request.urlopen(req, timeout=10) as res:
        data = json.loads(res.read().decode("utf-8"))
        universe = data[0]["universe"]
        ctxs = data[1]
        funding_map = {}
        for i, asset in enumerate(universe):
          funding_map[asset["name"]] = float(ctxs[i].get("funding", 0.0))
        return funding_map
    except Exception as e:
      print(
          f"Hyperliquid funding fetch attempt {attempt}/{max_retries} failed:"
          f" {e}"
      )
      if attempt < max_retries:
        time.sleep(1.5 * attempt)

  print("Warning: All Hyperliquid attempts failed. Using empty funding map.")
  return {}


# 2. 크립토 공포탐욕지수 무료 조회 (키 불필요, 3회 재시도 및 직전값 보존)
FNG_LABELS_KO = {
    "Extreme Fear": "극단적 공포",
    "Fear": "공포",
    "Neutral": "중립",
    "Greed": "탐욕",
    "Extreme Greed": "극단적 탐욕",
}


def get_crypto_fear_greed(fallback_val=50, fallback_label="Neutral", max_retries=3):
  url = "https://api.alternative.me/fng/?limit=1"
  headers = {"User-Agent": "Mozilla/5.0"}

  for attempt in range(1, max_retries + 1):
    try:
      req = urllib.request.Request(url, headers=headers)
      with urllib.request.urlopen(req, timeout=10) as res:
        data = json.loads(res.read().decode("utf-8"))
        row = data["data"][0]
        label = row.get("value_classification") or "Neutral"
        return {
            "value": int(row["value"]),
            "label": label,
            "labelKo": FNG_LABELS_KO.get(label, label),
        }
    except Exception as e:
      print(f"Fear & Greed fetch attempt {attempt}/{max_retries} failed: {e}")
      if attempt < max_retries:
        time.sleep(1.5 * attempt)

  print(f"Warning: Fear & Greed API failed. Falling back to: {fallback_val}")
  return {
      "value": int(fallback_val),
      "label": fallback_label,
      "labelKo": FNG_LABELS_KO.get(fallback_label, fallback_label),
  }


# 2-1. 무료 공개 API 단건 JSON 호출 helper (키·가입 불필요)
def _http_json(url, timeout=10):
  req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
  with urllib.request.urlopen(req, timeout=timeout) as res:
    return json.loads(res.read().decode("utf-8"))


# 2-2. 김치 프리미엄(BTC) : 업비트 KRW 시세 vs 해외 USD 시세 * 원/달러 환율
# 해외 시세는 무료 공개 API를 순서대로 시도합니다(바이낸스는 지역 차단이 잦아 뒤로 배치).
OVERSEAS_BTC_SOURCES = [
    (
        "Coinbase",
        "https://api.coinbase.com/v2/prices/BTC-USD/spot",
        lambda d: float(d["data"]["amount"]),
    ),
    (
        "CoinGecko",
        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
        lambda d: float(d["bitcoin"]["usd"]),
    ),
    (
        "Kraken",
        "https://api.kraken.com/0/public/Ticker?pair=XBTUSD",
        lambda d: float(d["result"]["XXBTZUSD"]["c"][0]),
    ),
    (
        "OKX",
        "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
        lambda d: float(d["data"][0]["last"]),
    ),
    (
        "Binance",
        "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT",
        lambda d: float(d["price"]),
    ),
]

FX_SOURCES = [
    (
        "open.er-api.com",
        "https://open.er-api.com/v6/latest/USD",
        lambda d: float(d["rates"]["KRW"]),
    ),
    (
        "frankfurter.dev",
        "https://api.frankfurter.dev/v1/latest?base=USD&symbols=KRW",
        lambda d: float(d["rates"]["KRW"]),
    ),
    (
        "jsdelivr/currency-api",
        "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.json",
        lambda d: float(d["usd"]["krw"]),
    ),
]


def get_kimchi_premium(prev=None):
  """김프(%) = ((업비트 KRW - 해외 USD * 환율) / (해외 USD * 환율)) * 100"""
  prev = prev or {}

  upbit_krw = None
  try:
    upbit_krw = float(
        _http_json("https://api.upbit.com/v1/ticker?markets=KRW-BTC")[0][
            "trade_price"
        ]
    )
  except Exception as e:
    print(f"Warning: Upbit BTC price fetch failed: {e}")

  overseas_usd, usd_source = None, None
  for name, url, pick in OVERSEAS_BTC_SOURCES:
    try:
      price = pick(_http_json(url))
      if price and price > 0:
        overseas_usd, usd_source = price, name
        break
    except Exception as e:
      print(f"Warning: overseas BTC price from {name} failed: {e}")

  usd_krw, fx_source = None, None
  for name, url, pick in FX_SOURCES:
    try:
      rate = pick(_http_json(url))
      if rate and rate > 0:
        usd_krw, fx_source = rate, name
        break
    except Exception as e:
      print(f"Warning: USD/KRW from {name} failed: {e}")

  # 일부 소스만 실패하면 직전 정상값으로 메꿔서 김프를 계속 계산
  if upbit_krw is None:
    upbit_krw = prev.get("upbitKrw")
  if overseas_usd is None:
    overseas_usd = prev.get("overseasUsd")
    usd_source = prev.get("usdSource")
  if usd_krw is None:
    usd_krw = prev.get("usdKrw")
    fx_source = prev.get("fxSource")

  if not (upbit_krw and overseas_usd and usd_krw):
    print("Warning: kimchi premium unavailable, keeping previous snapshot.")
    return prev or None

  overseas_krw = float(overseas_usd) * float(usd_krw)
  premium = round((float(upbit_krw) - overseas_krw) / overseas_krw * 100, 2)

  return {
      "premium": premium,
      "upbitKrw": round(float(upbit_krw)),
      "overseasUsd": round(float(overseas_usd), 2),
      "usdKrw": round(float(usd_krw), 2),
      "usdSource": usd_source,
      "fxSource": fx_source,
  }


# 2-3. 미국 주식 공포·탐욕 지수(CNN Fear & Greed) — 무료 공개 JSON, 키·가입 불필요
#   CNN 데이터 허브(production.dataviz.cnn.io)가 브라우저 User-Agent 로 응답합니다.
#   점수는 0~100 (0 = 극단적 공포, 100 = 극단적 탐색). 실패하면 직전값 유지
CNN_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
CNN_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
}

CNN_RATINGS_KO = {
    "extreme fear": "극단적 공포",
    "fear": "공포",
    "neutral": "중립",
    "greed": "탐욕",
    "extreme greed": "극단적 탐욕",
}

# CNN이 공개하는 7개 구성 지표 (한국어 표기)
CNN_COMPONENTS = [
    ("market_momentum_sp500", "S&P500 모멘텀"),
    ("stock_price_strength", "주가 강도(52주 신고가)"),
    ("stock_price_breadth", "시장 폭(상승 종목)"),
    ("put_call_options", "풋/콜 옵션"),
    ("market_volatility_vix", "VIX 변동성"),
    ("junk_bond_demand", "정크본드 수요"),
    ("safe_haven_demand", "안전자산 선호"),
]


def _num(value):
  """숫자로 쓸 수 있는 값만 float 로 돌려줍니다(문자열·None 방어)."""
  try:
    f = float(value)
    return f if math.isfinite(f) else None
  except (TypeError, ValueError):
    return None


def get_cnn_fear_greed(prev=None, max_retries=3):
  """미국 주식 CNN 공포·탐욕 지수와 구성 지표 7종을 가져옵니다."""
  for attempt in range(1, max_retries + 1):
    try:
      req = urllib.request.Request(CNN_FNG_URL, headers=CNN_HEADERS)
      with urllib.request.urlopen(req, timeout=15) as res:
        data = json.loads(res.read().decode("utf-8"))

      block = data.get("fear_and_greed") or {}
      score = _num(block.get("score"))
      if score is None:
        raise ValueError("fear_and_greed.score missing")

      rating = str(block.get("rating") or "").strip().lower()

      components = []
      for key, name in CNN_COMPONENTS:
        part = data.get(key) or {}
        c_score = _num(part.get("score"))
        if c_score is None:
          continue
        c_rating = str(part.get("rating") or "").strip().lower()
        components.append({
            "key": key,
            "name": name,
            "score": round(c_score, 1),
            "rating": c_rating,
            "labelKo": CNN_RATINGS_KO.get(c_rating, c_rating),
        })

      # 풋/콜 옵션 최신 관측치 (CNN 집계값)
      put_call = None
      pc_part = data.get("put_call_options") or {}
      pc_points = pc_part.get("data") or []
      if pc_points:
        pc_value = _num(pc_points[-1].get("y"))
        if pc_value is not None:
          pc_rating = str(pc_part.get("rating") or "").strip().lower()
          put_call = {
              "value": round(pc_value, 2),
              "score": round(_num(pc_part.get("score")) or 0, 1),
              "rating": pc_rating,
              "labelKo": CNN_RATINGS_KO.get(pc_rating, pc_rating),
          }

      def _round(key):
        v = _num(block.get(key))
        return round(v, 1) if v is not None else None

      return {
          "value": round(score, 1),
          "label": block.get("rating") or "",
          "labelKo": CNN_RATINGS_KO.get(rating, block.get("rating") or ""),
          "timestamp": block.get("timestamp"),
          "previousClose": _round("previous_close"),
          "previous1Week": _round("previous_1_week"),
          "previous1Month": _round("previous_1_month"),
          "previous1Year": _round("previous_1_year"),
          "components": components,
          "putCall": put_call,
          "source": "CNN Fear & Greed",
      }
    except Exception as e:
      print(
          f"CNN Fear & Greed fetch attempt {attempt}/{max_retries} failed: {e}"
      )
      if attempt < max_retries:
        time.sleep(2 * attempt)

  if prev:
    print("Warning: CNN Fear & Greed failed. Retaining previous value.")
  else:
    print("Warning: CNN Fear & Greed failed and no previous value exists.")
  return prev


# 2-4. 변동성·채권 심리 지수 (yfinance 무료 시세)
#   ^VIX = CBOE S&P500 변동성, ^MOVE = ICE BofA 미국 국채 변동성(MOVE)
VIX_LEVELS = [
    (12, "극도의 안심"),
    (16, "안정"),
    (20, "보통"),
    (25, "긴장"),
    (30, "공포"),
    (999, "극단적 공포"),
]
MOVE_LEVELS = [
    (70, "안정"),
    (90, "보통"),
    (110, "긴장"),
    (130, "불안"),
    (999, "공포"),
]
KOSPI_VOL_LEVELS = [
    (12, "안정"),
    (18, "보통"),
    (25, "긴장"),
    (999, "공포"),
]
#   ^GVZ = CBOE 금 변동성 지수, ^OVX = CBOE 원유 변동성 지수
GVZ_LEVELS = [
    (15, "안정"),
    (20, "보통"),
    (28, "긴장"),
    (38, "공포"),
    (999, "극단적 공포"),
]
OVX_LEVELS = [
    (28, "안정"),
    (38, "보통"),
    (50, "긴장"),
    (65, "공포"),
    (999, "극단적 공포"),
]


def _level_label(value, levels):
  for limit, label in levels:
    if value < limit:
      return label
  return levels[-1][1]


def _last_close(hist):
  """실제 종가가 있는 마지막 거래일만 남긴 시계열을 돌려줍니다.

  야후 응답의 마지막 행은 아직 종가가 비어 있는 경우(NaN)가 있어, 그대로
  hist.index[-1] 을 쓰면 날짜와 값이 어긋납니다. 종가가 있는 행만 남겨
  날짜와 값을 같은 기준으로 맞춥니다.
  """
  closes = hist["Close"].dropna()
  if closes.empty:
    raise ValueError("no close price in history")
  return closes


def _asof_regressed(new_asof, prev):
  """새 응답 날짜가 직전 스냅샷 날짜보다 과거면 True(역행 응답 방어).

  야후가 캐시·지연 응답으로 이미 반영된 날짜보다 과거 시세를 돌려주면
  최신 스냅샷을 과거 값으로 덮어쓰지 않기 위해 사용합니다.
  """
  if not new_asof or not isinstance(prev, dict):
    return False
  prev_asof = prev.get("asof")
  if not prev_asof:
    return False
  return str(new_asof) < str(prev_asof)


def get_index_snapshot(yf_ticker, prev=None, max_retries=2):
  """지수 최근 종가·전일 대비·주간 대비를 가져옵니다(실패 시 직전값 유지)."""
  for attempt in range(1, max_retries + 1):
    try:
      hist = yf.Ticker(yf_ticker).history(period="2mo")
      closes_series = _last_close(hist)
      closes = closes_series.tolist()
      if len(closes) < 2:
        raise ValueError("history too short")
      asof = str(closes_series.index[-1].date())
      if _asof_regressed(asof, prev):
        print(
            f"Warning: {yf_ticker} 응답 날짜 {asof} 가 직전"
            f" {prev.get('asof')} 보다 과거라 갱신을 건너뜁니다."
        )
        return prev
      curr = float(closes[-1])
      prev_1d = float(closes[-2])
      prev_1w = float(closes[-6]) if len(closes) >= 6 else float(closes[0])
      return {
          "value": round(curr, 2),
          "chg1d": round((curr - prev_1d) / prev_1d * 100, 2),
          "chg1w": round((curr - prev_1w) / prev_1w * 100, 2),
          "asof": asof,
      }
    except Exception as e:
      print(
          f"Index snapshot {yf_ticker} attempt {attempt}/{max_retries} failed:"
          f" {e}"
      )
      if attempt < max_retries:
        time.sleep(1.5 * attempt)
  return prev


FX_PAIRS = [("KRW=X", "USD/KRW"), ("JPYKRW=X", "JPY/KRW")]


def get_fx_realized_vol(yf_ticker, label, prev=None, max_retries=2):
  """환율 20거래일 실현변동성(연율 %).

  변동성 지수(VIX·MOVE·GVZ·OVX)는 주식·채권·금·원유에만 있고 환율에는
  무료 지수가 없어, 각 환율 자체의 최근 20거래일 일간 로그수익률
  표준편차를 연율화(×√252)한 값을 같은 의미의 수치로 씁니다(추정값 아님).
  """
  for attempt in range(1, max_retries + 1):
    try:
      hist = yf.Ticker(yf_ticker).history(period="3mo")
      closes_series = _last_close(hist)
      closes = [float(c) for c in closes_series.tolist()]
      if len(closes) < 22:
        raise ValueError("history too short")
      asof = str(closes_series.index[-1].date())
      if _asof_regressed(asof, prev):
        print(
            f"Warning: {label} 응답 날짜 {asof} 가 직전"
            f" {prev.get('asof')} 보다 과거라 갱신을 건너뜁니다."
        )
        return prev
      rets = [
          math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
      ][-20:]
      mean = sum(rets) / len(rets)
      var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
      vol = math.sqrt(var * 252) * 100
      return {
          "value": round(vol, 2),
          "chg1d": round((closes[-1] - closes[-2]) / closes[-2] * 100, 2),
          "asof": asof,
      }
    except Exception as e:
      print(
          f"FX realized vol {label} attempt {attempt}/{max_retries} failed: {e}"
      )
      if attempt < max_retries:
        time.sleep(1.5 * attempt)
  return prev


def get_fx_vol_map(prev=None):
  """환율쌍별 20일 실현변동성을 모아 {표시 티커: 스냅샷} 으로 돌려줍니다."""
  prev = prev or {}
  out = {}
  for yf_t, label in FX_PAIRS:
    snap = get_fx_realized_vol(yf_t, label, prev.get(label))
    if isinstance(snap, dict) and isinstance(snap.get("value"), (int, float)):
      out[label] = snap
  return out


def get_kospi_realized_vol(prev=None, min_asof=None, max_retries=2):
  """국장 변동성: 코스피 일간 종가 20거래일 실현변동성(연율 %).

  VKOSPI(코스피200 변동성지수)는 무료 공개 API가 없어, 같은 의미의
  변동성 수치를 실제 코스피 종가로 계산합니다(추정값 아님).
  """
  for attempt in range(1, max_retries + 1):
    try:
      hist = yf.Ticker("^KS11").history(period="3mo")
      closes_series = _last_close(hist)
      closes = [float(c) for c in closes_series.tolist()]
      if len(closes) < 22:
        raise ValueError("history too short")
      asof = str(closes_series.index[-1].date())
      # 역행 가드: 이미 반영된 날짜보다 과거 응답이면 최신 스냅샷을 유지합니다.
      floor_asof = min_asof or (prev or {}).get("asof")
      if _asof_regressed(asof, {"asof": floor_asof}):
        print(
            f"Warning: ^KS11 응답 날짜 {asof} 가 직전 코스피 스냅샷"
            f" {floor_asof} 보다 과거라 갱신을 건너뜁니다."
        )
        return prev
      rets = [
          math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
      ][-20:]
      mean = sum(rets) / len(rets)
      var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
      vol = math.sqrt(var * 252) * 100
      return {
          "value": round(vol, 2),
          "chg1d": round((closes[-1] - closes[-2]) / closes[-2] * 100, 2),
          "kospi": round(closes[-1], 2),
          "asof": asof,
      }
    except Exception as e:
      print(
          f"KOSPI realized vol attempt {attempt}/{max_retries} failed: {e}"
      )
      if attempt < max_retries:
        time.sleep(1.5 * attempt)
  return prev


# 3. RSI(14) 계산 함수
def calc_rsi(closes, period=14):
  if len(closes) < period + 1:
    return 50.0
  deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
  gains = [d if d > 0 else 0 for d in deltas[-period:]]
  losses = [-d if d < 0 else 0 for d in deltas[-period:]]
  avg_gain = sum(gains) / period
  avg_loss = sum(losses) / period
  if avg_loss == 0:
    return 100.0
  rs = avg_gain / avg_loss
  return 100.0 - (100.0 / (1.0 + rs))


# 4. 점수 산식 (100점 = s1 35 + s2 30 + s3 20 + s4 15)
#
#   [채점 1] 200일 평균선 대비 위치 — 평균선 부근(0%)은 17점. 아래로 갈수록 완만하게
#            (-0.55점/%), 위로 갈수록 빠르게(-1.2점/%) 깎입니다. 만점(35)은
#            -33% 이하의 큰 할인 구간에서만 나옵니다.
#   [채점 2] 1년 고점 대비 할인율 + RSI 과매도 (30점)
#   [채점 3] 1년 가격 박스권 위치 (20점)
#   [채점 4] 공포 심리 — 자산군마다 기준 지표가 다릅니다(아래 S4_*).
#
# [채점 4] 공포 심리 기준 (자산군별 분리)
#   · 크립토        : 크립토 공포·탐욕 지수(alternative.me) + Hyperliquid 마이너스 펀딩비
#   · 미장 주식·리츠 : 미국 CNN 공포·탐욕 지수 (미수신 시 중립 50)
#   · 국장 주식·리츠 : 무료 공식 한국 공탐 지수가 없어 코스피 20거래일 실현변동성의
#                     공포 구간에만 가산 (변동성이 낮으면 가산 없음)
#   · 채권          : ICE BofA MOVE 지수(^MOVE, 미국 국채 변동성)
#   · 귀금속        : CBOE 금 변동성 지수(^GVZ)
#   · 원자재        : CBOE 원유 변동성 지수(^OVX)
#   · 환율          : 각 환율 자체의 20거래일 실현변동성(연율 %, ×√252)
#   변동성 지수는 '평온 → 3점, 공포 이상 → 15점' 선형 스케일로 3~15점을 매깁니다.
S4_NEUTRAL = 5
S4_LABEL = {
    "crypto": "크립토 공탐",
    "us": "미장 CNN 공탐",
    "kr": "국장 변동성",
    "bonds": "채권 MOVE 지수",
    "metals": "금 변동성(GVZ)",
    "commodities": "원유 변동성(OVX)",
    "fx": "환율 20일 변동성",
    "neutral": "자산 중립(공탐 제외)",
}


def vol_fear_score(value, calm, panic):
  """변동성 지수값 → 공포 가산점(3~15).

  calm 이하면 3점, panic 이상이면 15점, 그 사이는 선형입니다.
  값이 없으면 None 을 돌려 호출한 쪽이 중립으로 처리하게 합니다.
  """
  if not isinstance(value, (int, float)) or isinstance(value, bool):
    return None
  ratio = (float(value) - calm) / float(panic - calm) if panic > calm else 0.0
  ratio = max(0.0, min(1.0, ratio))
  return int(round(3 + 12 * ratio))


def kospi_vol_fear_score(vol):
  """코스피 20일 실현변동성 → 공포 가산점(4~15). 낮은 변동성은 가산하지 않습니다."""
  if not isinstance(vol, (int, float)):
    return 7
  if vol < 12:
    return 4
  if vol < 18:
    return 6
  if vol < 25:
    return 9
  if vol < 35:
    return 12
  return 15


def apply_score(item, basis, ctx):
  """수집한 원 지표(item)로 s1~s4·총점·코멘트를 다시 계산해 item에 채웁니다."""
  ma_diff = float(item["maDiff"])
  mdd = float(item["mddPct"])
  rsi = float(item["rsi"])
  box = float(item["boxPos"])

  # [채점 1] 200일 평균선 대비 (35점 만점: 평균선보다 낮을수록 고득점)
  slope = 1.2 if ma_diff > 0 else 0.55
  s1 = int(max(5, min(35, round(17 - ma_diff * slope))))

  # [채점 2] 1년 고점 대비 할인율(MDD) + RSI 과매도 (30점 만점)
  rsi_score = max(0, min(15, round((70 - rsi) * 0.3)))
  mdd_score = max(0, min(15, round(mdd * 0.5)))
  s2 = int(max(4, min(30, rsi_score + mdd_score)))

  # [채점 3] 1년 가격 박스권 위치 매력도 (20점 만점: 박스권 하단일수록 고득점)
  s3 = int(max(4, min(20, round((1.0 - box) * 20))))

  # [채점 4] 자산군별 공포 심리 (15점 만점)
  if basis == "crypto":
    fng_info = ctx.get("crypto_fng") or {}
    fng_val = fng_info.get("value", 50)
    s4 = int(max(3, min(15, round((100 - fng_val) * 0.15))))
    s4_basis = f"크립토 공포탐욕 {fng_val} ({fng_info.get('labelKo', '중립')})"
    coin_key = str(item["ticker"]).replace("/KRW", "")
    funding = ctx.get("hl_funding") or {}
    if coin_key in funding and funding[coin_key] < 0:
      s4 = min(15, s4 + 3)  # 숏 과열(마이너스 펀딩비) 시 역발상 가산점
      s4_basis += " · 마이너스 펀딩비 +3"
  elif basis == "us":
    us_fng = ctx.get("us_fng") or {}
    us_val = us_fng.get("value")
    if isinstance(us_val, (int, float)):
      s4 = int(max(3, min(15, round((100 - us_val) * 0.15))))
      s4_basis = f"미장 CNN 공포탐욕 {us_val}"
      if us_fng.get("labelKo"):
        s4_basis += f" ({us_fng['labelKo']})"
    else:
      s4 = int(max(3, min(15, round((100 - 50) * 0.15))))
      s4_basis = "미장 CNN 공탐 미수신 → 중립 50으로 계산"
  elif basis == "kr":
    vol_info = ctx.get("kospi_vol") or {}
    vol = vol_info.get("value")
    s4 = kospi_vol_fear_score(vol)
    if isinstance(vol, (int, float)):
      s4_basis = f"코스피 20일 변동성 {vol}%"
      if vol_info.get("labelKo"):
        s4_basis += f" ({vol_info['labelKo']})"
    else:
      s4_basis = "코스피 변동성 미수신 → 중립 가산"
  elif basis == "bonds":
    # ICE BofA MOVE: 평온 55~75 · 보통 80~100 · 공포 110~140+
    val = (ctx.get("move") or {}).get("value")
    s4 = vol_fear_score(val, 55, 140)
    if s4 is None:
      s4 = S4_NEUTRAL
      s4_basis = "채권 MOVE 미수신 → 중립 5점"
    else:
      s4_basis = f"채권 변동성 MOVE {val:.1f}"
  elif basis == "metals":
    # CBOE Gold VIX(GVZ): 평온 12~16 · 보통 18~24 · 공포 30~40+
    val = (ctx.get("gvz") or {}).get("value")
    s4 = vol_fear_score(val, 12, 38)
    if s4 is None:
      s4 = S4_NEUTRAL
      s4_basis = "금 변동성 GVZ 미수신 → 중립 5점"
    else:
      s4_basis = f"CBOE 금 변동성 GVZ {val:.1f}"
  elif basis == "commodities":
    # CBOE Oil VIX(OVX): 평온 20~30 · 보통 32~45 · 공포 55~70+
    val = (ctx.get("ovx") or {}).get("value")
    s4 = vol_fear_score(val, 20, 65)
    if s4 is None:
      s4 = S4_NEUTRAL
      s4_basis = "원유 변동성 OVX 미수신 → 중립 5점"
    else:
      s4_basis = f"CBOE 원유 변동성 OVX {val:.1f}"
  elif basis == "fx":
    # 환율 20일 실현변동성: 평온 5% 이하 · 보통 7~10% · 고변동성 14~18%+
    val = ((ctx.get("fx_vol") or {}).get(str(item["ticker"])) or {}).get("value")
    s4 = vol_fear_score(val, 4, 16)
    if s4 is None:
      s4 = S4_NEUTRAL
      s4_basis = "환율 변동성 미수신 → 중립 5점"
    else:
      s4_basis = f"환율 20일 실현변동성 {val:.1f}%"
  else:
    s4 = S4_NEUTRAL
    s4_basis = "이 자산군은 공탐 지수를 쓰지 않습니다(중립 5점 고정)"

  total_score = s1 + s2 + s3 + s4

  # 자동 코멘트 생성 (신호등 커트라인 80 / 40 과 같은 기준)
  if total_score >= 80:
    comment = (
        f"200일 평균선 대비 {ma_diff:+.1f}%, 1년 고점 대비 -{mdd:.1f}%로"
        " 과매도 구간입니다. 장기 적립 비중을 늘리기 좋은 초록불 구간입니다."
    )
  elif total_score >= 40:
    comment = (
        f"장기 추세선 부근의 적정 가치 구간(RSI {rsi:.0f})입니다. 정해진"
        " 날짜에 평소대로 분할 적립을 유지하세요."
    )
  else:
    comment = (
        "단기 급등으로 장기 평균선 대비"
        f" {ma_diff:+.1f}% 위치한 과열 구간입니다. 추격 매수는 참아 보고"
        " 눌림목을 기다리세요."
    )

  item["score"] = total_score
  item["s1"] = s1
  item["s2"] = s2
  item["s3"] = s3
  item["s4"] = s4
  item["s4Label"] = S4_LABEL.get(basis, S4_LABEL["neutral"])
  item["s4Basis"] = s4_basis
  item["comment"] = comment
  return item


def analyze_ticker(
    yf_ticker,
    display_ticker,
    name,
    unit_prefix,
    unit_suffix,
    prev_item=None,
    min_asof=None,
    max_retries=2,
):
  for attempt in range(1, max_retries + 1):
    try:
      hist = yf.Ticker(yf_ticker).history(period="1y")
      if hist.empty or len(hist) < 30:
        raise ValueError("Data insufficient or empty history")

      closes_series = _last_close(hist)
      closes = closes_series.tolist()
      asof = str(closes_series.index[-1].date())
      # 역행 가드: 이미 반영된 날짜보다 과거 응답이면 직전 정상값을 유지합니다.
      if _asof_regressed(asof, {"asof": min_asof}):
        print(
            f"Warning: {display_ticker} 응답 날짜 {asof} 가 직전"
            f" {min_asof} 보다 과거라 갱신을 건너뜁니다."
        )
        return dict(prev_item) if prev_item else None
      curr = closes[-1]
      prev_1d = closes[-2] if len(closes) >= 2 else curr
      prev_1w = closes[-6] if len(closes) >= 6 else closes[0]

      chg1d = round(((curr - prev_1d) / prev_1d) * 100, 1)
      chg1w = round(((curr - prev_1w) / prev_1w) * 100, 1)

      ma200 = (
          sum(closes[-200:]) / len(closes[-200:])
          if len(closes) >= 200
          else sum(closes) / len(closes)
      )
      high_1y = max(closes)
      low_1y = min(closes)
      # 1년 고점이 언제였는지(문구에 날짜를 병기해 기준 시점을 분명히 함)
      high_date = str(
          closes_series.index[max(range(len(closes)), key=lambda i: closes[i])].date()
      )
      rsi = calc_rsi(closes)

      # 채점에 쓰는 원 지표를 그대로 담아 둡니다. 점수(s1~s4)는 main()에서 자산군별
      # 기준을 붙여 매기므로, 시세 조회가 실패해 직전 스냅샷을 유지할 때도 같은
      # 산식으로 다시 채점할 수 있습니다.
      ma_diff_pct = ((curr - ma200) / ma200) * 100
      mdd_pct = ((high_1y - curr) / high_1y) * 100
      box_pos = (
          (curr - low_1y) / (high_1y - low_1y) if high_1y != low_1y else 0.5
      )

      # 가격 포맷팅
      if unit_suffix == "원":
        price_val = curr * 100 if display_ticker == "JPY/KRW" else curr
        price_str = f"{price_val:,.0f}원"
      else:
        price_str = (
            f"{unit_prefix}{curr:,.1f}"
            if curr < 1000
            else f"{unit_prefix}{curr:,.0f}"
        )

      return {
          "ticker": display_ticker,
          "name": name,
          "price": price_str,
          "chg1d": chg1d,
          "chg1w": chg1w,
          "asof": asof,
          "raw_price": curr,
          # 1년 고점 대비 할인율: '국장 vs 미장' 상대 비교에 쓴 뒤 저장 전 제거합니다.
          "raw_mdd": round(mdd_pct, 2),
          "raw_high_date": high_date,
          # 점수 재계산용 원 지표(모달 설명·검증에도 그대로 쓸 수 있습니다)
          "maDiff": round(ma_diff_pct, 2),
          "mddPct": round(mdd_pct, 2),
          "rsi": round(rsi, 1),
          "boxPos": round(box_pos, 3),
      }
    except Exception as e:
      print(f"Error on {display_ticker} (attempt {attempt}/{max_retries}): {e}")
      if attempt < max_retries:
        time.sleep(1.5 * attempt)

  # 모든 재시도 실패 시 직전 정상 데이터가 있으면 유지 (종목 누락 방지)
  if prev_item:
    print(
        f"Warning: Retaining previous snapshot for {display_ticker} due to"
        " fetch error."
    )
    fallback_item = dict(prev_item)
    return fallback_item

  return None


def main():
  prev_state = load_previous_state()
  prev_fng = prev_state.get("fng", 50)
  prev_items = prev_state.get("items", {})
  # 이미 반영된 코스피 종가 날짜(역행 응답 가드용)
  prev_kospi_asof = (prev_items.get("KOSPI") or {}).get("asof") or (
      prev_state.get("kospiVol") or {}
  ).get("asof")

  fng_info = get_crypto_fear_greed(
      fallback_val=prev_fng,
      fallback_label=prev_state.get("fng_label", "Neutral"),
  )
  fng = fng_info["value"]
  kimchi = get_kimchi_premium(prev_state.get("kimchi"))
  hl_funding = get_hl_funding()

  # 시장 온도계: 미장 CNN 공포·탐욕 + VIX + 채권 MOVE + 국장 변동성
  us_fng = get_cnn_fear_greed(prev_state.get("usFng"))

  vix = get_index_snapshot("^VIX", prev_state.get("vix"))
  if vix and "value" in vix:
    vix["labelKo"] = _level_label(vix["value"], VIX_LEVELS)

  move = get_index_snapshot("^MOVE", prev_state.get("move"))
  if move and "value" in move:
    move["labelKo"] = _level_label(move["value"], MOVE_LEVELS)

  # 귀금속·원자재 변동성 지수 (채권 MOVE와 같은 무료 야후 시세)
  gvz = get_index_snapshot("^GVZ", prev_state.get("gvz"))
  if gvz and "value" in gvz:
    gvz["labelKo"] = _level_label(gvz["value"], GVZ_LEVELS)

  ovx = get_index_snapshot("^OVX", prev_state.get("ovx"))
  if ovx and "value" in ovx:
    ovx["labelKo"] = _level_label(ovx["value"], OVX_LEVELS)

  # 환율은 지수가 없어 각 환율 자체 20일 실현변동성으로 본다
  fx_vol = get_fx_vol_map(prev_state.get("fxVol"))

  kospi_vol = get_kospi_realized_vol(
      prev_state.get("kospiVol"), min_asof=prev_kospi_asof
  )
  if kospi_vol and "value" in kospi_vol:
    kospi_vol["labelKo"] = _level_label(kospi_vol["value"], KOSPI_VOL_LEVELS)

  # 자산군 아이디(cid) · 아이콘 · 이름 · [(야후 심볼, 표시 티커, 이름, 단위 접두, 단위, [채점4] 기준)]
  #   [채점4] 기준: crypto(크립토 공탐) / us(미장 CNN 공탐) / kr(코스피 변동성)
  #             / bonds(MOVE) / fx(환율 실현변동성) / metals(GVZ) / commodities(OVX)
  config = [
      (
          "bonds",
          "💵",
          "채권 · 환율",
          [
              ("TLT", "TLT", "미국 20년+ 장기채", "$", "", "bonds"),
              ("IEF", "IEF", "미국 7-10년 중기채", "$", "", "bonds"),
              ("JPYKRW=X", "JPY/KRW", "엔화 환율 (100엔)", "", "원", "fx"),
              ("SGOV", "SGOV", "미국 초단기채(파킹)", "$", "", "bonds"),
              ("KRW=X", "USD/KRW", "달러 환율", "", "원", "fx"),
          ],
      ),
      (
          "metals",
          "🥈",
          "귀금속 (금·은)",
          [
              ("SLV", "SLV", "은 ETF (Silver)", "$", "", "metals"),
              ("GLD", "GLD", "금 ETF (Gold)", "$", "", "metals"),
          ],
      ),
      (
          "reits",
          "🏢",
          "부동산 리츠",
          [
              ("O", "O", "리얼티 인컴 (월배당)", "$", "", "us"),
              ("VNQ", "VNQ", "미국 부동산 리츠", "$", "", "us"),
              ("476800.KS", "TIGER리츠", "한국 리츠 인프라", "", "원", "kr"),
          ],
      ),
      (
          "commodities",
          "🛢️",
          "원자재 (유가·구리)",
          [
              ("USO", "USO", "WTI 원유 ETF", "$", "", "commodities"),
              ("CPER", "CPER", "구리 ETF (닥터코퍼)", "$", "", "commodities"),
          ],
      ),
      (
          "stocks",
          "📈",
          "주식 (미·한)",
          [
              ("SCHD", "SCHD", "미국 배당다우존스", "$", "", "us"),
              ("SOXX", "SOXX", "미국 반도체 ETF", "$", "", "us"),
              ("^KS11", "KOSPI", "한국 코스피 지수", "", "", "kr"),
              ("SPY", "SPY", "미국 S&P 500", "$", "", "us"),
              ("QQQ", "QQQ", "미국 나스닥 100", "$", "", "us"),
          ],
      ),
      (
          "crypto",
          "🪙",
          "크립토 (코인)",
          [
              ("ETH-USD", "ETH", "이더리움 (Ethereum)", "$", "", "crypto"),
              ("BTC-USD", "BTC", "비트코인 (Bitcoin)", "$", "", "crypto"),
              ("SOL-USD", "SOL", "솔라나 (Solana)", "$", "", "crypto"),
          ],
      ),
  ]

  categories = []
  raw_map = {}
  mdd_map = {}
  high_date_map = {}
  score_ctx = {
      "crypto_fng": fng_info,
      "us_fng": us_fng,
      "kospi_vol": kospi_vol,
      "hl_funding": hl_funding,
      "move": move,
      "gvz": gvz,
      "ovx": ovx,
      "fx_vol": fx_vol,
  }

  for cid, icon, cname, tickers in config:
    items = []
    for yf_t, disp_t, tname, pre, suf, basis in tickers:
      p_item = prev_items.get(disp_t)
      res = analyze_ticker(
          yf_t,
          disp_t,
          tname,
          pre,
          suf,
          prev_item=p_item,
          min_asof=prev_kospi_asof if yf_t == "^KS11" else None,
          max_retries=2,
      )
      if res:
        raw_price = res.pop("raw_price", None)
        raw_mdd = res.pop("raw_mdd", None)
        raw_high_date = res.pop("raw_high_date", None)
        if raw_price is not None:
          raw_map[disp_t] = raw_price
        if raw_mdd is not None:
          mdd_map[disp_t] = raw_mdd
        if raw_high_date:
          high_date_map[disp_t] = raw_high_date
        # 시세 조회가 실패해 직전 스냅샷을 유지한 종목도 같은 산식으로 다시 채점합니다.
        if "maDiff" in res:
          apply_score(res, basis, score_ctx)
        items.append(res)
    if items:
      categories.append(
          {"id": cid, "icon": icon, "name": cname, "items": items}
      )


  # ─────────────────────────────────────────────────────────────
  # 상대 가성비 6종 — 모두 'A vs B' 양방향 비교
  #   각 항목: left / right / winnerSide / winner / pct(우위 강도 50~95) / title / desc
  #   · pct 50 = 거의 균형, 95 = 한쪽이 확실히 눌림 (막대가 그만큼 쏠립니다)
  #   · 실제 수집 시세만 사용합니다. 비교에 필요한 값이 없으면 지어내지 않고
  #     직전 data.json 의 같은 카드를 그대로 유지합니다.
  # ─────────────────────────────────────────────────────────────
  prev_ratios = prev_state.get("ratios", {})
  ratios = []

  def _dominance(value, pivot, span):
    """기준(pivot)에서 벗어난 정도를 50~95% 우위 강도로 환산합니다."""
    if not span:
      return 50
    ratio = min(1.0, abs(value - pivot) / span)
    return int(round(50 + 45 * ratio))

  def _ratio(title, left, right, left_wins, pct, desc):
    pct = max(50, min(95, int(pct)))
    side = "left" if left_wins else "right"
    winner = (
        f"{left if left_wins else right} {pct}% 우세"
        if pct >= 55
        else "거의 균형"
    )
    return {
        "title": title,
        "left": left,
        "right": right,
        "winnerSide": side,
        "winner": winner,
        "pct": pct,
        "desc": desc,
    }

  def _add_ratio(title, payload):
    if payload is not None:
      ratios.append(payload)
      return
    prev_r = prev_ratios.get(title)
    if prev_r:
      print(f"Warning: {title} 비교 시세가 없어 직전 값을 유지합니다.")
      ratios.append(prev_r)
    else:
      print(f"Warning: {title} 비교 시세가 없어 이번 갱신에서는 제외합니다.")

  # 1) 금 vs 은 — 금 1온스에 은 몇 온스인지(금/은 비율). 높을수록 은이 눌림.
  payload = None
  gld_p, slv_p = raw_map.get("GLD"), raw_map.get("SLV")
  if gld_p and slv_p:
    gs_ratio = round((gld_p * 10) / slv_p, 1)
    silver_cheap = gs_ratio >= 70.0
    payload = _ratio(
        "금 vs 은",
        "금",
        "은",
        not silver_cheap,
        _dominance(gs_ratio, 70.0, 20.0),
        (
            f"은 쪽이 금보다 상대적으로 많이 눌려 있어요 (금/은 {gs_ratio}배)"
            if silver_cheap
            else f"금 쪽이 은보다 상대적으로 많이 눌려 있어요 (금/은 {gs_ratio}배)"
        ),
    )
  _add_ratio("금 vs 은", payload)

  # 2) 국장 vs 미장 — 코스피와 나스닥(QQQ)의 1년 고점 대비 할인율(실현 낙폭) 비교.
  #    지수 레벨은 서로 비교할 수 없어, '고점에서 얼마나 눌렸는지'를 기준으로 봅니다.
  payload = None
  kospi_mdd, qqq_mdd = mdd_map.get("KOSPI"), mdd_map.get("QQQ")
  kospi_high, qqq_high = high_date_map.get("KOSPI"), high_date_map.get("QQQ")

  def _md(date_str):
    """'2026-06-22' → '6/22' (고점 날짜를 짧게)"""
    try:
      _, m, d = str(date_str).split("-")
      return f"{int(m)}/{int(d)}"
    except Exception:
      return str(date_str)

  if kospi_mdd is not None and qqq_mdd is not None:
    kospi_cheap = kospi_mdd >= qqq_mdd
    kospi_high_txt = f"({_md(kospi_high)})" if kospi_high else ""
    qqq_high_txt = f"({_md(qqq_high)})" if qqq_high else ""
    payload = _ratio(
        "국장 vs 미장",
        "코스피",
        "나스닥",
        kospi_cheap,
        _dominance(kospi_mdd - qqq_mdd, 0.0, 15.0),
        (
            f"코스피 쪽이 더 눌려 있어요 (1년 고점{kospi_high_txt} 대비 -{kospi_mdd:.1f}%"
            f" vs 나스닥 고점{qqq_high_txt} 대비 -{qqq_mdd:.1f}%)"
            if kospi_cheap
            else f"나스닥 쪽이 더 눌려 있어요 (1년 고점{qqq_high_txt} 대비 -{qqq_mdd:.1f}%"
            f" vs 코스피 고점{kospi_high_txt} 대비 -{kospi_mdd:.1f}%)"
        ),
    )
  _add_ratio("국장 vs 미장", payload)

  # 3) 이더 vs 비트 — ETH/BTC 비율. 낮을수록 이더가 눌림.
  payload = None
  eth_p, btc_p = raw_map.get("ETH"), raw_map.get("BTC")
  if eth_p and btc_p:
    eth_btc_ratio = round(eth_p / btc_p, 4)
    eth_cheap = eth_btc_ratio <= 0.050
    payload = _ratio(
        "이더 vs 비트",
        "이더",
        "비트",
        eth_cheap,
        _dominance(eth_btc_ratio, 0.050, 0.025),
        (
            f"이더 쪽이 비트보다 상대적으로 싸요 (ETH/BTC {eth_btc_ratio:.4f})"
            if eth_cheap
            else f"비트 쪽이 이더보다 상대적으로 싸요 (ETH/BTC {eth_btc_ratio:.4f})"
        ),
    )
  _add_ratio("이더 vs 비트", payload)

  # 4) 장기채 vs 단기채 — TLT/SGOV 비율. 낮을수록 장기채가 눌린 상태.
  payload = None
  tlt_p, sgov_p = raw_map.get("TLT"), raw_map.get("SGOV")
  if tlt_p and sgov_p:
    tlt_sgov_ratio = round(tlt_p / sgov_p, 2)
    long_cheap = tlt_sgov_ratio <= 0.90
    payload = _ratio(
        "장기채 vs 단기채",
        "장기채",
        "단기채",
        long_cheap,
        _dominance(tlt_sgov_ratio, 0.90, 0.18),
        (
            "장기채가 더 눌려 있어 반등 여지가 커요"
            f" (TLT/SGOV {tlt_sgov_ratio:.2f})"
            if long_cheap
            else "장기채가 비싸 단기채 파킹이 유리해요"
            f" (TLT/SGOV {tlt_sgov_ratio:.2f})"
        ),
    )
  _add_ratio("장기채 vs 단기채", payload)

  # 5) 성장주 vs 배당주 — QQQ/SCHD 비율. 높을수록 배당주가 눌림.
  payload = None
  qqq_p, schd_p = raw_map.get("QQQ"), raw_map.get("SCHD")
  if qqq_p and schd_p:
    qs_ratio = round(qqq_p / schd_p, 1)
    dividend_cheap = qs_ratio >= 16.0
    payload = _ratio(
        "성장주 vs 배당주",
        "성장주",
        "배당주",
        not dividend_cheap,
        _dominance(qs_ratio, 16.0, 5.0),
        (
            f"배당주 쪽이 성장주보다 상대적으로 싸요 (QQQ/SCHD {qs_ratio:.1f})"
            if dividend_cheap
            else f"성장주 쪽이 배당주보다 상대적으로 싸요 (QQQ/SCHD {qs_ratio:.1f})"
        ),
    )
  _add_ratio("성장주 vs 배당주", payload)

  # 6) 구리 vs 금 — CPER/GLD 환산 비율. 낮을수록 구리가 눌림(경기 회복 시 여지).
  payload = None
  cper_p = raw_map.get("CPER")
  if cper_p and gld_p:
    cg_ratio = round((cper_p * 10) / gld_p, 2)
    copper_cheap = cg_ratio <= 1.25
    payload = _ratio(
        "구리 vs 금",
        "구리",
        "금",
        copper_cheap,
        _dominance(cg_ratio, 1.25, 0.35),
        (
            f"구리 쪽이 금보다 상대적으로 싸요 (CPER/GLD {cg_ratio:.2f})"
            if copper_cheap
            else f"금 쪽이 구리보다 상대적으로 싸요 (CPER/GLD {cg_ratio:.2f})"
        ),
    )
  _add_ratio("구리 vs 금", payload)

  market = {
      "kimchi": kimchi,
      "cryptoFng": fng_info,
      "usFng": us_fng,
      "vix": vix,
      "move": move,
      "gvz": gvz,
      "ovx": ovx,
      "kospiVol": kospi_vol,
      "fxVol": fx_vol,
  }

  output = {
      "updatedAt": now_str,
      # 클라이언트가 기기 시계·문자열 포맷에 덜 의존하도록 epoch(ms)·ISO 도 같이 남깁니다.
      "updatedAtTs": int(datetime.now(KST).timestamp() * 1000),
      "updatedAtISO": datetime.now(KST).isoformat(timespec="seconds"),
      "categories": categories,
      "ratios": ratios,
      "market": market,
  }

  # 원자적 파일 쓰기 (작성 도중단 방지)
  tmp_file = "data.json.tmp"
  with open(tmp_file, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
  os.replace(tmp_file, "data.json")
  print(
      f"[{now_str}] Successfully updated data.json with {len(categories)}"
      " categories."
  )
  if kimchi:
    print(
        "Kimchi premium (BTC):"
        f" {kimchi['premium']}% / upbit {kimchi['upbitKrw']:,} KRW vs"
        f" {kimchi['overseasUsd']} USD ({kimchi['usdSource']}) *"
        f" {kimchi['usdKrw']} KRW ({kimchi['fxSource']})"
    )
  print(f"Crypto Fear & Greed: {fng} ({fng_info['labelKo']})")
  if us_fng:
    print(
        "US (CNN) Fear & Greed:"
        f" {us_fng['value']} ({us_fng['labelKo']}) · components"
        f" {len(us_fng.get('components') or [])}개"
    )
  else:
    print("Warning: US Fear & Greed value unavailable this run.")
  if vix:
    print(f"VIX: {vix['value']} ({vix.get('labelKo')}) as of {vix.get('asof')}")
  if move:
    print(f"MOVE: {move['value']} ({move.get('labelKo')}) as of {move.get('asof')}")
  if gvz:
    print(f"GVZ: {gvz['value']} ({gvz.get('labelKo')}) as of {gvz.get('asof')}")
  if ovx:
    print(f"OVX: {ovx['value']} ({ovx.get('labelKo')}) as of {ovx.get('asof')}")
  for pair, info in (fx_vol or {}).items():
    print(
        f"FX realized vol {pair}: {info['value']}% as of {info.get('asof')}"
    )
  if kospi_vol:
    print(
        "KOSPI 20d realized vol:"
        f" {kospi_vol['value']}% ({kospi_vol.get('labelKo')}) as of"
        f" {kospi_vol.get('asof')}"
    )
  kospi_item = next(
      (it for cat in categories for it in cat["items"]
       if it["ticker"] == "KOSPI"),
      None,
  )
  if kospi_item:
    print(
        f"KOSPI: {kospi_item['price']} ({kospi_item['score']}점) as of"
        f" {kospi_item.get('asof')}"
    )

  # 신호등 분포를 로그에 남깁니다 (커트라인 80 / 40)
  all_items = [it for cat in categories for it in cat["items"]]
  greens = [it["ticker"] for it in all_items if it.get("score", 0) >= 80]
  print(
      f"Signal split: 🟢 {len(greens)} / 🟡"
      f" {sum(1 for it in all_items if 40 <= it.get('score', 0) < 80)} / 🔴"
      f" {sum(1 for it in all_items if it.get('score', 0) < 40)} (total"
      f" {len(all_items)})"
  )
  print(f"🟢 지금 사요(80점↑): {', '.join(greens) if greens else '없음'}")


if __name__ == "__main__":
  main()
