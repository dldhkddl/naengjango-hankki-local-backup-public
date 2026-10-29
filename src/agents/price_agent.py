"""
Price Agent [선택] - 가격대별 메뉴 등급 + 재료비 총액 추정
- KAMIS(농산물유통정보) API로 식량작물(100)/채소류(200)/특용작물(300)/과일류(400)/축산물(500)/수산물(600) 6개 부류의
  전국 소매·도매가격을 가져와서, 같은 시장·부류 안에서 상대적인 가격을 비교한다
  (estimate_recipe_price_tier). 이건 절대 금액을 합산하지 않고 "중앙값 대비 몇 배인지"로만 등급을 매긴다.
- 추가로 estimate_recipe_total_cost()는 "이 레시피를 만드는 데 대략 얼마 드는지"를 원 단위로 추정한다.
  재료마다 KAMIS 단위가 제각각이라(kg/g/개/단 등), g 단위로 정확히 환산되는 재료만 계산에 포함하고,
  "개"처럼 개수 단위인 재료는 AVG_PIECE_WEIGHT_G(품목별 평균 중량표)로 추정 환산한다. 매칭이 안 되거나
  단위 환산이 안 되는 재료는 계산에서 빠지므로, 결과는 "포함된 재료만의 부분 합계"다.
- 두부처럼 가공식품인 재료는 KAMIS가 다루는 범위(농·축·수산물) 밖이라 매칭되지 않는다 (정보부족으로 표시).
- 지침 8번 원칙: 가격 정보는 참고용이며, 최신 정보는 공식 채널(KAMIS 등)에서 재확인을 권장한다.
"""

import math
import os
import re
import statistics
import requests
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from dotenv import load_dotenv

from src.agents import portion_agent

load_dotenv()
CERT_KEY = os.getenv("KAMIS_CERT_KEY")
CERT_ID = os.getenv("KAMIS_CERT_ID")

# 직접 테스트해서 확인한 부류코드만 사용한다 (검증 안 된 코드는 추가하지 않음)
CATEGORY_CODES = {
    "100": "식량작물",
    "200": "채소류",
    "300": "특용작물",
    "400": "과일류",
    "500": "축산물",
    "600": "수산물",
}
MARKET_CODES = {"01": "소매", "02": "도매"}

# 일반 흰밥의 비용 추정 가정. 물양·취사법에 따라 수율은 달라진다.
# 참고: https://www.sommeliertimes.com/news/articleView.html?idxno=1675
COOKED_RICE_NAMES = frozenset({"밥", "쌀밥", "흰밥", "백미밥", "흰쌀밥", "찬밥"})
RICE_COOKED_WEIGHT_RATIO = 2.3
RICE_CONVERSION_NOTE = "쌀 1g → 밥 2.3g 가정 · 쌀값만 계산 (취사 비용 제외)"

# 채소류(200) 실제 품목 목록을 확인한 뒤 채워둔 동의어. 방향 규칙(재료명이 품목명에 포함)만으로는
# "애호박"이 "호박"에 매칭되지 않는 것처럼 놓치는 경우가 있어 자주 쓰는 것만 보정한다.
VEGETABLE_SYNONYMS = {
    "애호박": "호박",
    # 단호박을 애호박 시세로 대체하지 않는다. 실제 대응 품목이 있을 때만 사용한다.
    "마늘": "깐마늘",
    "다진마늘": "깐마늘",
    "다진 마늘": "깐마늘",
    "다진생강": "생강",
}

# KAMIS 품목명이 한 글자짜리인 경우(소/닭/돼지) 부분일치가 오작동하지 않도록 별도 매핑
MEAT_SYNONYMS = {
    "소고기": "소", "쇠고기": "소",
    "돼지고기": "돼지",
    "닭고기": "닭",
}

# KAMIS 가격은 "20개", "1단" 처럼 개수 단위로 나오는 품목이 있어서, 레시피에 필요한 g(그램)량과
# 맞추려면 "개당 평균 몇 g인지" 가정이 필요하다. 완벽할 필요는 없고, 자주 나오는 품목만 채워둔다
# (지침 6번 원칙과 동일). 여기 없는 개수 단위 품목은 재료비 계산에서 제외되고 "환산 불가"로 표시된다.
AVG_PIECE_WEIGHT_G = {
    "호박": 300,       # 애호박 1개 평균 (단호박은 이 시세로 대체하지 않음)
    "배추": 2000,      # 1포기
    "양배추": 1500,    # 1통
    "무": 1000,        # 1개
    "오이": 150,       # 1개
    "양파": 200,       # 1개
    "참외": 300,       # 1개
    "수박": 4500,      # 1통
    "멜론": 1500,      # 1개
    "토마토": 200,     # 1개
    "방울토마토": 15,  # 1개
    "파프리카": 150,   # 1개
    "피망": 80,        # 1개
    "상추": 15,        # 1장
    "깻잎": 2,         # 1장
}


def _kamis_unit_to_grams(unit: str, item_name: str) -> tuple[float | None, bool]:
    """
    KAMIS의 unit 문자열(예: "20kg", "1kg", "20개")이 실제로 몇 g에 해당하는 가격인지 계산한다.
    반환값: (그램 수 또는 None, is_estimated) - is_estimated는 AVG_PIECE_WEIGHT_G 추정치를
    썼는지 여부다 (kg/g처럼 정확히 환산되는 경우는 False).
    - 개/포기/통/장 단위는 AVG_PIECE_WEIGHT_G에 있는 품목만 추정 환산하고,
      없으면 (None, False)를 반환해서 "이 재료는 원가 계산에서 제외"하도록 한다.
    """
    if not unit:
        return None, False
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([가-힣a-zA-Z]+)", unit.strip())
    if not m:
        return None, False
    qty = float(m.group(1))
    suffix = m.group(2).lower()
    if not math.isfinite(qty) or qty <= 0:
        return None, False

    if suffix == "kg":
        return qty * 1000, False
    if suffix == "g":
        return qty, False

    # 봉/상자/단/속은 묶음 안의 개수를 모르므로 1개 평균 중량으로 대체하지 않는다.
    if suffix not in {"개", "포기", "통", "장"}:
        return None, False
    # 개수 단위: 품목별 평균 중량표에 있을 때만 추정 환산
    avg_weight = AVG_PIECE_WEIGHT_G.get(item_name)
    if avg_weight is None:
        return None, False
    return qty * avg_weight, True


def prepare_recipe_price_items(items: list[dict], ingredient_names: list[str]) -> list[dict]:
    """수량 행의 구획 제목을 제거하되, 실제 재료 태그와 일치할 때만 이름을 연결한다."""
    known_names = set(ingredient_names)
    result = []
    for item in items:
        name = item["name"].strip()
        if portion_agent.classify_ingredient_row(name, item.get("amount")) != "ingredient":
            continue
        if name not in known_names:
            for title in portion_agent.SECTION_TITLES:
                candidate = name.removeprefix(title + " ").strip()
                if candidate != name and candidate in known_names:
                    name = candidate
                    break
        result.append({**item, "name": name})
    return result


def estimate_recipe_total_cost(scaled_items: list[dict], all_items: list[dict]) -> dict:
    """
    레시피 재료들(이름 + 필요한 양(g) + 단위)을 KAMIS 가격과 매칭해서, 실제로 이 레시피를
    만드는 데 드는 재료비 총액(원)을 추정한다.
    - scaled_items: [{"name": ..., "amount": 숫자 또는 None, "unit": 문자열 또는 None}, ...]
      (portion_agent가 인분수에 맞춰 환산해둔 값. amount/unit이 없거나 단위가 "g"이 아니면 계산에서 제외)
    - 반환값의 total_cost는 "포함된 재료"만 합산한 부분 합계이며, 실제 총 재료비보다 적을 수 있다
      (지침 8번 원칙: 가격 정보는 참고용, 최신 정보는 공식 채널 재확인 권장).
    """
    included = []
    excluded = []

    for item in scaled_items:
        name = item.get("name")
        amount = item.get("amount")
        unit = item.get("unit")

        # 추천 점수용 조미료 제외 규칙은 가격 계산에 적용하지 않는다.
        # 양파·파프리카가 '파', 나물이 '물'에 걸려 가격이 있어도 빠지던 원인이다.
        if amount is None or unit != "g":
            excluded.append({"ingredient": name, "reason": "양(g) 정보가 없거나 g 단위가 아님"})
            continue

        if not isinstance(amount, (int, float)) or not math.isfinite(amount) or amount <= 0:
            excluded.append({"ingredient": name, "reason": "유효한 양(g) 정보가 아님"})
            continue

        matched = match_ingredient_price(name, all_items)
        if matched is None:
            excluded.append({"ingredient": name, "reason": "KAMIS 가격 매칭 안 됨"})
            continue

        lot_grams, is_estimated = _kamis_unit_to_grams(matched["unit"], matched["item_name"])
        if lot_grams is None or lot_grams <= 0:
            excluded.append({"ingredient": name, "reason": f"단위({matched['unit']}) 환산 불가"})
            continue

        price_per_g = matched["price"] / lot_grams
        is_cooked_rice = name.strip() in COOKED_RICE_NAMES
        source_amount = amount / RICE_COOKED_WEIGHT_RATIO if is_cooked_rice else amount
        cost = price_per_g * source_amount

        included.append({
            "ingredient": name,
            "matched_name": matched["item_name"],
            "amount_g": amount,
            "cost": cost,
            "is_estimated": is_estimated or is_cooked_rice,
            "source_amount_g": source_amount if is_cooked_rice else None,
            "conversion_note": RICE_CONVERSION_NOTE if is_cooked_rice else None,
        })

    total_cost = sum(i["cost"] for i in included)
    return {"total_cost": total_cost, "included": included, "excluded": excluded}


def _extract_price(item: dict) -> float | None:
    """dpr1(당일)부터 dpr4(2주일전)까지 순서대로 값이 있는 걸 사용한다 (당일 데이터는 '-'인 경우가 많음)."""
    for key in ["dpr1", "dpr2", "dpr3", "dpr4"]:
        raw = item.get(key, "")
        if raw and raw != "-":
            try:
                price = float(str(raw).replace(",", ""))
                if math.isfinite(price) and price > 0:
                    return price
            except (TypeError, ValueError):
                continue
    return None


def fetch_category_prices(category_code: str, market_code: str = "01") -> list[dict]:
    """한 부류·시장의 전국 가격을 조회한다. 통신 실패와 정상적인 자료 없음을 구분한다."""
    url = "https://www.kamis.or.kr/service/price/xml.do"
    query_date = date.today()
    params = {
        "action": "dailyPriceByCategoryList",
        "p_product_cls_code": market_code,
        "p_item_category_code": category_code,
        "p_country_code": "",  # 특정 지역 제한 없이 전국 평균
        "p_regday": query_date.isoformat(),
        # Y는 가격만 1kg으로 바꾸면서 unit은 원래 포장 단위(쌀: 20kg)를
        # 남긴다. N으로 조사 단위 가격을 받아 중량 환산을 한 번만 수행한다.
        "p_convert_kg_yn": "N",
        "p_cert_key": CERT_KEY,
        "p_cert_id": CERT_ID,
        "p_returntype": "json",
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
    except (requests.exceptions.RequestException, ValueError):
        # requests 예외에는 인증키가 포함된 URL이 들어갈 수 있다.
        raise ValueError(f"KAMIS {market_code}/{category_code} 조회 실패") from None

    payload = data.get("data") if isinstance(data, dict) else None
    if payload == ["001"] or (isinstance(payload, dict) and payload.get("error_code") == "001"):
        return []
    if not isinstance(payload, dict) or payload.get("error_code") != "000":
        raise ValueError(f"KAMIS {market_code}/{category_code} 응답 오류")

    items = payload.get("item", [])
    if not isinstance(items, list):
        raise ValueError("KAMIS 품목 목록 형식 오류")
    result = []
    for i in items:
        price = _extract_price(i)
        if price is None:
            continue
        # 선택된 가격과 같은 열의 날짜를 보존한다. 조회일을 가격 기준일로 표시하지 않는다.
        price_date = None
        for index in range(1, 5):
            if _extract_price({"dpr1": i.get(f"dpr{index}")}) is not None:
                day = re.search(r"\((\d{2})/(\d{2})\)", i.get(f"day{index}", ""))
                if day:
                    month, day_number = map(int, day.groups())
                    year = query_date.year - ((month, day_number) > (query_date.month, query_date.day))
                    price_date = date(year, month, day_number).isoformat()
                break
        result.append({
            "item_name": i.get("item_name"),
            "kind_name": i.get("kind_name", ""),
            "rank": i.get("rank", ""),
            "market_code": market_code,
            "source_market": MARKET_CODES[market_code],
            "source_region": "전국 평균",
            "price_date": price_date,
            "unit": i.get("unit"),
            "price": price,
            "category_code": category_code,
            "category_name": CATEGORY_CODES[category_code],
        })
    return result


def get_all_prices() -> list[dict]:
    """6개 부류 × 소매·도매를 모두 조회한다. 일부 실패를 품목 부재로 캐시하지 않는다."""
    with ThreadPoolExecutor(max_workers=4) as pool:
        pending = [pool.submit(fetch_category_prices, code, market)
                   for market in MARKET_CODES for code in CATEGORY_CODES]
        return [item for future in pending for item in future.result()]


def _price_per_kg(item: dict) -> float | None:
    """등급 비교용 가격만 1kg 기준으로 환산한다. 표시용 원본 단위는 유지한다."""
    grams, _ = _kamis_unit_to_grams(item["unit"], item["item_name"])
    if grams is None:
        return None
    return item["price"] * 1000 / grams


def _category_medians(all_items: list[dict]) -> dict:
    """부류별 1kg 가격 중앙값. 포장 중량 차이로 비싼 재료가 되지 않도록 한다."""
    by_cat = {}
    for i in all_items:
        price_per_kg = _price_per_kg(i)
        if price_per_kg is not None:
            by_cat.setdefault((i.get("market_code"), i["category_code"]), []).append(price_per_kg)
    return {code: statistics.median(prices) for code, prices in by_cat.items() if prices}


def match_ingredient_price(ingredient_name: str, all_items: list[dict]) -> dict | None:
    """
    재료명으로 KAMIS 품목을 찾는다.
    - 방향을 한 쪽으로만 허용한다: "재료명이 품목명 안에 포함되는" 경우만 매칭
      (예: "오징어"가 "마른오징어"에 포함 -> OK).
    - 반대 방향("품목명이 재료명 안에 포함")은 허용하지 않는다. 이걸 허용하면
      "콩"(대두)이라는 짧은 품목명이 "콩나물"(전혀 다른 재료) 안에 우연히 포함돼 있다는
      이유만으로 잘못 매칭되는 문제가 있었다 (실제 테스트에서 발견됨).
    - 정확히 이름이 같은 품목이 있으면 그것을 최우선으로 쓴다. 안 그러면 "배추"를 찾을 때
      "배추" 대신 "알배기배추"(다른 품종)에 걸릴 수 있다 (실제 채소류 목록 확인 중 발견).
    - 소/닭/돼지처럼 KAMIS 쪽 품목명이 축약형인 경우만 동의어 매핑으로 보정한다.
    """
    ingredient_name = ingredient_name.strip()
    search_key = ("쌀" if ingredient_name in COOKED_RICE_NAMES else
                  VEGETABLE_SYNONYMS.get(ingredient_name, MEAT_SYNONYMS.get(ingredient_name, ingredient_name)))

    exact_matches = [i for i in all_items if i["item_name"] == search_key]
    kind_matches = [
        i for i in all_items
        if (i.get("kind_name") or "").split("(", 1)[0].strip() == ingredient_name
    ]
    matches = kind_matches or exact_matches
    if not matches and len(search_key) >= 2:
        matches = [i for i in all_items if search_key in i["item_name"]]
    if not matches:
        return None

    # 국내산과 수입산이 둘 다 있으면 "수입" 표시 없는 쪽(국내산)을 기본으로 우선한다.
    # 이름/품종 일치가 우선이고, 같은 후보 중 소매 → 도매 순이다.
    # 가격 차이가 큰 부위·등급은 응답에 보존하여 화면에 명시한다.
    return min(matches, key=lambda m: (
        {"01": 0, "02": 1}.get(m.get("market_code"), 2),
        "수입" in m["item_name"],
    ))


def estimate_recipe_price_tier(ingredient_names: list[str], all_items: list[dict]) -> dict:
    """
    레시피 재료들을 KAMIS 가격과 매칭해서, 같은 부류 내 중앙값 대비 상대적으로
    비싼 재료가 많은지 싼 재료가 많은지로 등급을 매긴다.
    조사 단위가 다른 가격은 같은 중량으로 환산해서 비교한다.
    """
    medians = _category_medians(all_items)

    matched = []
    unmatched = []
    for name in ingredient_names:
        m = match_ingredient_price(name, all_items)
        if m is None:
            unmatched.append(name)
            continue
        median = medians.get((m.get("market_code"), m["category_code"]))
        price_per_kg = _price_per_kg(m)
        ratio = (price_per_kg / median) if median and price_per_kg is not None else None
        matched.append({**m, "ingredient": name, "ratio": ratio,
                        "conversion_note": RICE_CONVERSION_NOTE if name.strip() in COOKED_RICE_NAMES else None})

    # 매칭된 재료가 너무 적으면 등급을 매길 근거가 부족하다고 본다.
    # 원래는 2개만 매칭돼도 등급을 매겼는데, 매칭 2개 중 1개만 비싼 재료여도
    # "비싼 재료 비율 50%"가 되어 40% 기준을 넘겨버려서, 총 재료비가 몇십 원인 반찬이
    # "프리미엄"으로 표시되는 문제가 실사용에서 발견됐다(#68). 표본 2~3개로는 비율이
    # 재료 하나 차이로 크게 흔들려서 등급 자체가 신뢰하기 어려우므로, 최소 3개로 올렸다.
    total_names = len(ingredient_names) or 1
    ratios = [m["ratio"] for m in matched if m["ratio"] is not None]
    if len(ratios) < 3 or (len(ratios) / total_names) < 0.3:
        return {"tier": "정보부족", "matched": matched, "unmatched": unmatched}

    expensive = sum(1 for r in ratios if r >= 1.3)
    cheap = sum(1 for r in ratios if r <= 0.7)
    total = len(ratios) or 1

    if expensive / total >= 0.4:
        tier = "프리미엄"
    elif cheap / total >= 0.6:
        tier = "가성비"
    else:
        tier = "기본"

    return {"tier": tier, "matched": matched, "unmatched": unmatched}


if __name__ == "__main__":
    print("KAMIS 6개 부류 가격 조회 중...")
    all_items = get_all_prices()
    print(f"총 {len(all_items)}개 품목 가격 확인\n")

    test_ingredients = ["배추", "두부", "돼지고기", "새우", "콩나물", "계란", "애호박", "단호박", "마늘"]
    result = estimate_recipe_price_tier(test_ingredients, all_items)
    print(f"테스트 재료: {test_ingredients}")
    print(f"등급: {result['tier']}")
    for m in result["matched"]:
        ratio_txt = f"{m['ratio']:.2f}배" if m["ratio"] else "비교불가"
        print(f"  - {m['ingredient']} -> {m['item_name']}({m['unit']}): {m['price']:,.0f}원, 부류 중앙값 대비 {ratio_txt}")
    if result["unmatched"]:
        print(f"  매칭 안 된 재료: {result['unmatched']}")
