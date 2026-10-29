"""KAMIS 응답 → 재료비 계산의 단위 일치와 포장 크기 독립성을 검증한다."""

import pytest

from src.agents import price_agent


def kamis_item(name, unit, price):
    return {
        "item_name": name, "unit": unit, "price": price,
        "category_code": "100", "category_name": "식량작물",
    }


@pytest.mark.parametrize("price_field", ["dpr1", "dpr2"])
def test_rice_fetch_to_cost_does_not_convert_twice(monkeypatch, price_field):
    # 실제 응답의 함정: Y이면 가격만 1kg 기준이고 unit은 여전히 20kg이다.
    # 2026-09-07 확인값: N=59,000원/20kg, Y=2,950원/kg.
    def fake_get(url, *, params, timeout):
        raw_price = "2,950" if params["p_convert_kg_yn"] == "Y" else "59,000"

        class Response:
            def json(self):
                row = {"item_name": "쌀", "unit": "20kg", "dpr1": "-"}
                row[price_field] = raw_price
                return {"data": {"error_code": "000", "item": [row]}}

        return Response()

    monkeypatch.setattr(price_agent.requests, "get", fake_get)
    prices = price_agent.fetch_category_prices("100")
    result = price_agent.estimate_recipe_total_cost(
        [{"name": "쌀", "amount": 100, "unit": "g"}], prices,
    )
    assert result["total_cost"] == pytest.approx(295)
    assert result["included"][0]["is_estimated"] is False
    assert result["excluded"] == []


@pytest.mark.parametrize("unit,price", [("20kg", 59000), ("1kg", 2950), ("100g", 295)])
def test_same_rice_cost_for_different_lot_sizes(unit, price):
    result = price_agent.estimate_recipe_total_cost(
        [{"name": "쌀", "amount": 100, "unit": "g"}],
        [kamis_item("쌀", unit, price)],
    )
    assert result["total_cost"] == pytest.approx(295)


def test_price_tier_is_independent_of_lot_size():
    names = ["쌀", "콩", "감자"]
    per_kg = [kamis_item(name, "1kg", 3000) for name in names]
    mixed_lots = [
        kamis_item("쌀", "20kg", 60000),
        kamis_item("콩", "500g", 1500),
        kamis_item("감자", "1kg", 3000),
    ]
    expected = price_agent.estimate_recipe_price_tier(names, per_kg)
    actual = price_agent.estimate_recipe_price_tier(names, mixed_lots)
    assert actual["tier"] == expected["tier"] == "기본"
    assert [item["ratio"] for item in actual["matched"]] == pytest.approx([1, 1, 1])
    # 표시용 가격과 단위는 원본 조사 단위를 유지한다.
    assert actual["matched"][0]["price"] == 60000
    assert actual["matched"][0]["unit"] == "20kg"


@pytest.mark.parametrize("unit,grams", [("20kg", 20000), ("0.5kg", 500), ("100g", 100), ("1 KG", 1000)])
def test_mass_units(unit, grams):
    assert price_agent._kamis_unit_to_grams(unit, "쌀") == (grams, False)


@pytest.mark.parametrize("unit", ["1봉", "1상자", "1kg추정", "1.2.3kg", "0kg", "-1kg", "", None])
def test_unknown_or_invalid_unit_is_not_guessed(unit):
    assert price_agent._kamis_unit_to_grams(unit, "배추") == (None, False)


def test_count_unit_cost_still_marked_estimated():
    result = price_agent.estimate_recipe_total_cost(
        [{"name": "오이", "amount": 150, "unit": "g"}],
        [kamis_item("오이", "10개", 10000)],
    )
    assert result["total_cost"] == pytest.approx(1000)
    assert result["included"][0]["is_estimated"] is True


def test_unconvertible_prices_do_not_distort_tier():
    items = [kamis_item(name, "1상자", 100000) for name in ["쌀", "콩", "감자"]]
    result = price_agent.estimate_recipe_price_tier(["쌀", "콩", "감자"], items)
    assert result["tier"] == "정보부족"
    assert all(row["ratio"] is None for row in result["matched"])
