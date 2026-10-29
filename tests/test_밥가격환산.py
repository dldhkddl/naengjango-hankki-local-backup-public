"""밥을 취사 전 쌀 중량으로 환산하되 원본 필요량·추정 근거를 보존한다."""
import pytest

from api.routers import price as price_router
from src.agents import price_agent
from helpers import signup_body


RICE = {'item_name': '쌀', 'unit': '20kg', 'price': 60000,
        'category_code': '100', 'market_code': '01', 'source_market': '소매',
        'price_date': '2026-09-07'}


@pytest.mark.parametrize('name', ['밥', '쌀밥', '흰밥', '백미밥', '흰쌀밥', '찬밥'])
def test_cooked_rice_uses_dry_rice_weight_and_retains_served_weight(name):
    row = price_agent.estimate_recipe_total_cost(
        [{'name': name, 'amount': 230, 'unit': 'g'}], [RICE])['included'][0]
    assert row['amount_g'] == 230
    assert row['source_amount_g'] == pytest.approx(100)
    assert row['cost'] == pytest.approx(300)
    assert row['is_estimated'] is True
    assert '2.3g 가정' in row['conversion_note']
    assert row['matched_name'] == '쌀'


def test_raw_rice_is_not_converted_twice():
    row = price_agent.estimate_recipe_total_cost(
        [{'name': '쌀', 'amount': 230, 'unit': 'g'}], [RICE])['included'][0]
    assert row['cost'] == pytest.approx(690)
    assert row['is_estimated'] is False
    assert row['source_amount_g'] is None
    assert row['conversion_note'] is None


@pytest.mark.parametrize('name', ['잡곡밥', '현미밥', '즉석밥', '김치볶음밥', '요거트'])
def test_mixed_or_packaged_food_is_not_assumed_to_be_plain_rice(name):
    assert price_agent.match_ingredient_price(name, [RICE]) is None


@pytest.mark.parametrize('amount,unit', [(None, 'g'), (1, '공기'), (-10, 'g'), (0, 'g'), (float('nan'), 'g')])
def test_unknown_or_invalid_weight_has_no_invented_cost(amount, unit):
    result = price_agent.estimate_recipe_total_cost(
        [{'name': '밥', 'amount': amount, 'unit': unit}], [RICE])
    assert result['included'] == []
    assert result['excluded'][0]['ingredient'] == '밥'
    # 수량이 없더라도 쌀 원본 시세와 환산 가정은 전달한다.
    matched = price_agent.estimate_recipe_price_tier(['밥'], [RICE])['matched'][0]
    assert matched['unit'] == '20kg'
    assert matched['price'] == 60000
    assert matched['conversion_note']


def test_missing_rice_price_does_not_create_a_fallback_amount():
    result = price_agent.estimate_recipe_total_cost([{'name': '밥', 'amount': 230, 'unit': 'g'}], [])
    assert result['included'] == []


def test_api_converts_after_household_scaling_and_exposes_basis(client, db_conn, monkeypatch):
    account = client.post('/auth/signup', json=signup_body('rice_cost_test')).json()
    headers = {'Authorization': 'Bearer ' + account['token']}
    db_conn.execute("UPDATE recipe_ingredients SET name='밥', amount=460, unit='g' WHERE recipe_id=1 AND name='양파'")
    db_conn.execute("UPDATE recipe_tags SET tag_value='밥' WHERE recipe_id=1 AND tag_type='ingredient' AND tag_value='양파'")
    price_router._prices_cache.clear()
    monkeypatch.setattr(price_agent, 'get_all_prices', lambda: [RICE])
    try:
        response = client.get('/recommendation/recipes/1/price', params={'user_id': account['user_id']}, headers=headers)
        assert response.status_code == 200
        row = next(r for r in response.json()['included'] if r['ingredient'] == '밥')
        # 기본 1인 가구 / 원본 2인분: 밥 460g → 230g → 쌀 100g.
        assert row['amount_g'] == 230
        assert row['source_amount_g'] == pytest.approx(100)
        assert row['cost'] == pytest.approx(300)
        assert row['is_estimated'] is True
        assert row['conversion_note']
        matched = next(r for r in response.json()['matched'] if r['ingredient'] == '밥')
        assert matched['conversion_note'] == row['conversion_note']
        assert matched['price_date'] == '2026-09-07'
    finally:
        price_router._prices_cache.clear()
