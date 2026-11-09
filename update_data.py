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
  if not os.path.exists("data.json"):
    return {"fng": 50, "items": {}}
  try:
    with open("data.json", "r", encoding="utf-8") as f:
      data = json.load(f)

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

    return {"fng": prev_fng, "items": prev_items}
  except Exception as e:
    print(f"Warning: could not read previous data.json: {e}")
    return {"fng": 50, "items": {}}


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
def get_fear_greed(fallback_val=50, max_retries=3):
  url = "https://api.alternative.me/fng/?limit=1"
  headers = {"User-Agent": "Mozilla/5.0"}

  for attempt in range(1, max_retries + 1):
    try:
      req = urllib.request.Request(url, headers=headers)
      with urllib.request.urlopen(req, timeout=10) as res:
        data = json.loads(res.read().decode("utf-8"))
        return int(data["data"][0]["value"])
    except Exception as e:
      print(f"Fear & Greed fetch attempt {attempt}/{max_retries} failed: {e}")
      if attempt < max_retries:
        time.sleep(1.5 * attempt)

  print(f"Warning: Fear & Greed API failed. Falling back to: {fallback_val}")
  return fallback_val


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

  fng = get_fear_greed(fallback_val=prev_fng)
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

  # 상대 가성비 자동 계산
  gld_p = raw_map.get("GLD", 220)
  slv_p = raw_map.get("SLV", 27)
  gs_ratio = round((gld_p * 10) / slv_p, 1) if slv_p else 80.0

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
          "title": "시장 심리 온도계 (공포·탐욕)",
          "winner": (
              "공포 구간 (매수 유리)"
              if fng < 45
              else ("과열 주의" if fng > 65 else "중립 구간")
          ),
          "desc": (
              f"현재 글로벌 투자 심리 지수 {fng}점 (낮을수록 대중이 공포에 떤"
              " 바닥권)"
          ),
          "pct": 100 - fng,
      },
  ]

  output = {"updatedAt": now_str, "categories": categories, "ratios": ratios}

  # 원자적 파일 쓰기 (작성 도중단 방지)
  tmp_file = "data.json.tmp"
  with open(tmp_file, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
  os.replace(tmp_file, "data.json")
  print(
      f"[{now_str}] Successfully updated data.json with {len(categories)}"
      " categories."
  )


if __name__ == "__main__":
  main()
