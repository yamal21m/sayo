import json
import os
import re
import time
import urllib.request
from datetime import datetime, timezone, timedelta

import yfinance as yf

# 한국 시간(KST) 표시용
KST = timezone(timedelta(hours=9))
now_str = datetime.now(KST).strftime("%Y.%m.%d %H:%M 업데이트")


def load_previous_state():
  """이전 data.json이 존재하면 읽어서 직전 F&G 및 종목 데이터를 복구용으로 로드합니다."""
  empty = {
      "fng": 50,
      "fng_label": "Neutral",
      "items": {},
      "kimchi": None,
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

    return {
        "fng": int(prev_fng),
        "fng_label": prev_fng_label,
        "items": prev_items,
        "kimchi": prev_market.get("kimchi"),
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


# 4. 개별 종목 데이터 수집 및 100점 채점 공식 (2회 재시도 + 직전 정상값 보존)
def analyze_ticker(
    yf_ticker,
    display_ticker,
    name,
    unit_prefix,
    unit_suffix,
    fng,
    hl_funding,
    prev_item=None,
    max_retries=2,
):
  for attempt in range(1, max_retries + 1):
    try:
      hist = yf.Ticker(yf_ticker).history(period="1y")
      if hist.empty or len(hist) < 30:
        raise ValueError("Data insufficient or empty history")

      closes = hist["Close"].dropna().tolist()
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
      rsi = calc_rsi(closes)

      # [채점 1] 200일 평균선 대비 위치 (35점 만점: 평균선보다 낮을수록 고득점)
      ma_diff_pct = ((curr - ma200) / ma200) * 100
      s1 = int(max(5, min(35, round(22 - ma_diff_pct * 1.2))))

      # [채점 2] 1년 고점 대비 할인율(MDD) + RSI 과매도 (30점 만점)
      mdd_pct = ((high_1y - curr) / high_1y) * 100
      rsi_score = max(0, min(15, round((70 - rsi) * 0.35)))
      mdd_score = max(0, min(15, round(mdd_pct * 0.7)))
      s2 = int(max(4, min(30, rsi_score + mdd_score)))

      # [채점 3] 1년 가격 박스권 위치 매력도 (20점 만점: 박스권 하단일수록 고득점)
      box_pos = (
          (curr - low_1y) / (high_1y - low_1y) if high_1y != low_1y else 0.5
      )
      s3 = int(max(4, min(20, round((1.0 - box_pos) * 20))))

      # [채점 4] 시장 공포심리 & 선물 펀딩비 역발상 점수 (15점 만점)
      s4 = int(max(3, min(15, round((100 - fng) * 0.15))))
      coin_key = display_ticker.replace("/KRW", "")
      if coin_key in hl_funding and hl_funding[coin_key] < 0:
        s4 = min(15, s4 + 3)  # 숏 과열(마이너스 펀딩비) 시 역발상 가산점

      total_score = s1 + s2 + s3 + s4

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

      # 자동 코멘트 생성
      if total_score >= 70:
        comment = (
            "200일 평균선 대비 유리한 구간이며 고점 대비"
            f" -{mdd_pct:.1f}% 할인 중입니다. 장기 적립 비중을 늘리기 좋은"
            " 초록불 구간입니다."
        )
      elif total_score >= 40:
        comment = (
            f"장기 추세선 부근의 적정 가치 구간(RSI {rsi:.0f})입니다. 정해진"
            " 날짜에 평소대로 분할 적립을 유지하세요."
        )
      else:
        comment = (
            "단기 급등으로 장기 평균선 대비"
            f" +{ma_diff_pct:.1f}% 높은 과열 구간입니다. 추격 매수는 참아 보고"
            " 눌림목을 기다리세요."
        )

      return {
          "ticker": display_ticker,
          "name": name,
          "price": price_str,
          "chg1d": chg1d,
          "chg1w": chg1w,
          "score": total_score,
          "s1": s1,
          "s2": s2,
          "s3": s3,
          "s4": s4,
          "comment": comment,
          "raw_price": curr,
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

  fng_info = get_crypto_fear_greed(
      fallback_val=prev_fng,
      fallback_label=prev_state.get("fng_label", "Neutral"),
  )
  fng = fng_info["value"]
  kimchi = get_kimchi_premium(prev_state.get("kimchi"))
  hl_funding = get_hl_funding()

  config = [
      (
          "bonds",
          "💵",
          "채권 · 환율",
          [
              ("TLT", "TLT", "미국 20년+ 장기채", "$", ""),
              ("IEF", "IEF", "미국 7-10년 중기채", "$", ""),
              ("JPYKRW=X", "JPY/KRW", "엔화 환율 (100엔)", "", "원"),
              ("SGOV", "SGOV", "미국 초단기채(파킹)", "$", ""),
              ("KRW=X", "USD/KRW", "달러 환율", "", "원"),
          ],
      ),
      (
          "metals",
          "🥈",
          "귀금속 (금·은)",
          [
              ("SLV", "SLV", "은 ETF (Silver)", "$", ""),
              ("GLD", "GLD", "금 ETF (Gold)", "$", ""),
          ],
      ),
      (
          "reits",
          "🏢",
          "부동산 리츠",
          [
              ("O", "O", "리얼티 인컴 (월배당)", "$", ""),
              ("VNQ", "VNQ", "미국 부동산 리츠", "$", ""),
              ("476800.KS", "TIGER리츠", "한국 리츠 인프라", "", "원"),
          ],
      ),
      (
          "commodities",
          "🛢️",
          "원자재 (유가·구리)",
          [
              ("USO", "USO", "WTI 원유 ETF", "$", ""),
              ("CPER", "CPER", "구리 ETF (닥터코퍼)", "$", ""),
          ],
      ),
      (
          "stocks",
          "📈",
          "주식 (미·한)",
          [
              ("SCHD", "SCHD", "미국 배당다우존스", "$", ""),
              ("SOXX", "SOXX", "미국 반도체 ETF", "$", ""),
              ("^KS11", "KOSPI", "한국 코스피 지수", "", ""),
              ("SPY", "SPY", "미국 S&P 500", "$", ""),
              ("QQQ", "QQQ", "미국 나스닥 100", "$", ""),
          ],
      ),
      (
          "crypto",
          "🪙",
          "크립토 (코인)",
          [
              ("ETH-USD", "ETH", "이더리움 (Ethereum)", "$", ""),
              ("BTC-USD", "BTC", "비트코인 (Bitcoin)", "$", ""),
              ("SOL-USD", "SOL", "솔라나 (Solana)", "$", ""),
          ],
      ),
  ]

  categories = []
  raw_map = {}

  for cid, icon, cname, tickers in config:
    items = []
    for yf_t, disp_t, tname, pre, suf in tickers:
      p_item = prev_items.get(disp_t)
      res = analyze_ticker(
          yf_t,
          disp_t,
          tname,
          pre,
          suf,
          fng,
          hl_funding,
          prev_item=p_item,
          max_retries=2,
      )
      if res:
        raw_price = res.pop("raw_price", None)
        if raw_price is not None:
          raw_map[disp_t] = raw_price
        items.append(res)
    if items:
      categories.append(
          {"id": cid, "icon": icon, "name": cname, "items": items}
      )

  # 상대 가성비 자동 계산 (6대 핵심 가성비 지표)
  # 1. 금 vs 은
  gld_p = raw_map.get("GLD", 360)
  slv_p = raw_map.get("SLV", 45)
  gs_ratio = round((gld_p * 10) / slv_p, 1) if slv_p else 80.0

  # 2. 이더 vs 비트
  eth_p = raw_map.get("ETH", 2000)
  btc_p = raw_map.get("BTC", 70000)
  eth_btc_ratio = round(eth_p / btc_p, 4) if btc_p else 0.030

  # 3. 장기채 vs 단기채
  tlt_p = raw_map.get("TLT", 80)
  sgov_p = raw_map.get("SGOV", 100)
  tlt_sgov_ratio = round(tlt_p / sgov_p, 2) if sgov_p else 0.80

  # 4. 성장주 vs 배당주
  qqq_p = raw_map.get("QQQ", 600)
  schd_p = raw_map.get("SCHD", 35)
  qs_ratio = round(qqq_p / schd_p, 1) if schd_p else 18.0

  # 5. 구리 vs 금
  cper_p = raw_map.get("CPER", 37)
  cg_ratio = round((cper_p * 10) / gld_p, 2) if gld_p else 1.0

  ratios = [
      {
          "title": "금 vs 은 (ETF 상대 비율)",
          "winner": (
              "은(SLV) 저평가 우위" if gs_ratio >= 75 else "금·은 균형 구간"
          ),
          "desc": (
              f"현재 GLD/SLV 환산비율 {gs_ratio} (높을수록 은이 상대적으로 저렴)"
          ),
          "pct": min(95, max(20, int(gs_ratio))),
      },
      {
          "title": "크립토 공포·탐욕 지수 (BTC 심리)",
          "winner": (
              "공포 구간 (매수 유리)"
              if fng < 45
              else ("과열 주의" if fng > 65 else "중립 구간")
          ),
          "desc": (
              f"현재 크립토 투자 심리 {fng}점 · {fng_info['labelKo']}"
              " (alternative.me 기준, 낮을수록 대중이 공포에 떤 바닥권)"
          ),
          "pct": 100 - fng,
      },
      {
          "title": "이더 vs 비트 (ETH / BTC 비율)",
          "winner": (
              "이더리움(ETH) 저평가 우위"
              if eth_btc_ratio <= 0.040
              else ("비트코인(BTC) 우세 구간" if eth_btc_ratio >= 0.060 else "이더·비트 균형 구간")
          ),
          "desc": (
              f"현재 ETH/BTC 비율 {eth_btc_ratio:.4f} (0.040 이하 시 이더·알트 가성비 바닥 매력)"
          ),
          "pct": min(95, max(20, int((0.065 - eth_btc_ratio) / 0.045 * 100))),
      },
      {
          "title": "장기채 vs 단기채 (TLT / SGOV)",
          "winner": (
              "장기채(TLT) 반등 차익 우위"
              if tlt_sgov_ratio <= 0.90
              else "단기채(SGOV) 파킹 우위"
          ),
          "desc": (
              f"현재 TLT/SGOV 비율 {tlt_sgov_ratio:.2f} (낮을수록 장기 금리 상단·차익 메리트)"
          ),
          "pct": min(95, max(20, int((1.10 - tlt_sgov_ratio) / 0.35 * 100))),
      },
      {
          "title": "성장주 vs 배당주 (QQQ / SCHD)",
          "winner": (
              "배당주(SCHD) 저평가 우위"
              if qs_ratio >= 16.0
              else ("성장주(QQQ) 밸류 우위" if qs_ratio <= 12.0 else "성장·가치 균형 구간")
          ),
          "desc": (
              f"현재 QQQ/SCHD 비율 {qs_ratio:.1f} (높을수록 나스닥 대비 배당가치주 가성비 상승)"
          ),
          "pct": min(95, max(20, int((qs_ratio - 11.0) / 10.0 * 100))),
      },
      {
          "title": "구리 vs 금 (CPER / GLD 경기 선행)",
          "winner": (
              "구리(CPER) 반등 가성비 우위"
              if cg_ratio <= 1.15
              else "금(GLD) 안전자산 우위"
          ),
          "desc": (
              f"현재 CPER/GLD 환산비율 {cg_ratio:.2f} (낮을수록 실물 경기 회복 시 구리 잠재력 우위)"
          ),
          "pct": min(95, max(20, int((1.60 - cg_ratio) / 0.70 * 100))),
      },
  ]

  market = {
      "kimchi": kimchi,
      "cryptoFng": fng_info,
  }

  output = {
      "updatedAt": now_str,
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


if __name__ == "__main__":
  main()
