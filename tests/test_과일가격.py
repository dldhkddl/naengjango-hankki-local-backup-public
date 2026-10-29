"""전체 조회에서 과일·특용작물 부류가 빠지는 문제의 회귀 검증."""
import pytest
from src.agents import price_agent


@pytest.mark.parametrize('name,amount,expected', [('사과', 100, 590), ('오렌지', 100, 61500 / 180)])
def test_all_prices_fetches_fruit_and_computes_gram_cost(monkeypatch, name, amount, expected):
    def fake_get(url, *, params, timeout):
        rows = []
        if params['p_item_category_code'] == '400':
            rows = [
                {'item_name': '사과', 'unit': '10kg', 'dpr1': '59,000'},
                {'item_name': '오렌지', 'unit': '18kg', 'dpr1': '61,500'},
            ]
        class Response:
            def json(self):
                return {'data': {'error_code': '000', 'item': rows}}
        return Response()
    monkeypatch.setattr(price_agent.requests, 'get', fake_get)
    prices = price_agent.get_all_prices()
    result = price_agent.estimate_recipe_total_cost([{'name': name, 'amount': amount, 'unit': 'g'}], prices)
    assert result['total_cost'] == pytest.approx(expected)
    assert result['included'][0]['ingredient'] == name


def test_all_prices_includes_special_crops(monkeypatch):
    def fake_get(url, *, params, timeout):
        rows = [{'item_name': '참깨', 'unit': '1kg', 'dpr1': '10,000'}] if params['p_item_category_code'] == '300' else []
        class Response:
            def json(self):
                return {'data': {'error_code': '000', 'item': rows}}
        return Response()
    monkeypatch.setattr(price_agent.requests, 'get', fake_get)
    result = price_agent.estimate_recipe_total_cost(
        [{'name': '참깨', 'amount': 10, 'unit': 'g'}], price_agent.get_all_prices(),
    )
    assert result['total_cost'] == pytest.approx(100)


@pytest.mark.parametrize('name', ['잡곡밥', '요거트'])
def test_processed_food_is_not_replaced_with_raw_ingredient_price(name):
    prices = [{'item_name': '쌀', 'price': 59000, 'unit': '20kg'}, {'item_name': '우유', 'price': 3000, 'unit': '1kg'}]
    result = price_agent.estimate_recipe_total_cost([{'name': name, 'amount': 100, 'unit': 'g'}], prices)
    assert result['included'] == []
    assert result['excluded'][0]['ingredient'] == name
