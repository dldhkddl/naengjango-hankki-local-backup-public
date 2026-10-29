"""부족한 재료 화면과 가격 API 사이에서 발견한 누락을 검증한다."""
import pytest

from src.agents import price_agent
from api.routers import price as price_router
from helpers import signup_body


def kamis(name, price, unit="1kg", **extra):
    return dict(item_name=name, price=price, unit=unit, category_code="200", category_name="채소류", **extra)


@pytest.fixture(autouse=True)
def clear_cache():
    price_router._prices_cache.clear()
    yield
    price_router._prices_cache.clear()


@pytest.mark.parametrize("name", ["양파", "파프리카", "마늘", "콩나물"])
def test_priced_ingredients_are_not_removed_by_seasoning_filter(name):
    source_name = "깐마늘(국산)" if name == "마늘" else name
    result = price_agent.estimate_recipe_total_cost(
        [dict(name=name, amount=100, unit="g")], [kamis(source_name, 1000)],
    )
    assert result["total_cost"] == pytest.approx(100)
    assert result["included"][0]["ingredient"] == name


def test_section_prefix_is_removed_only_when_confirmed_by_recipe_tags():
    items = [
        dict(name="재료 단호박", amount=100, unit="g"),
        dict(name="양념 다진 마늘", amount=5, unit="g"),
        dict(name="양념 닭고기", amount=100, unit="g"),
        dict(name="주재료", amount=None, unit=None),
    ]
    result = price_agent.prepare_recipe_price_items(items, ["단호박", "다진 마늘"])
    assert [row["name"] for row in result] == ["단호박", "다진 마늘", "양념 닭고기"]
    assert items[0]["name"] == "재료 단호박"  # 원본을 변경하지 않는다.


def test_squash_and_pear_do_not_use_unrelated_prices():
    items = [kamis("호박", 47000, "20개", kind_name="애호박(20개)"), kamis("배추", 1000)]
    assert price_agent.match_ingredient_price("단호박", items) is None
    assert price_agent.match_ingredient_price("배", items) is None


def test_minced_garlic_uses_peeled_not_unpeeled_price():
    items = [kamis("피마늘", 3000), kamis("깐마늘(국산)", 8000)]
    assert price_agent.match_ingredient_price("다진 마늘", items)["item_name"] == "깐마늘(국산)"


def test_zucchini_kind_is_selected_instead_of_first_pumpkin_row():
    items = [
        kamis("호박", 3000, kind_name="쥬키니(10kg)"),
        kamis("호박", 5000, kind_name="애호박(20개)"),
    ]
    assert price_agent.match_ingredient_price("애호박", items)["price"] == 5000


def account(client):
    body = client.post('/auth/signup', json=signup_body('price_gap_test')).json()
    return body['user_id'], {'Authorization': 'Bearer ' + body['token']}


def test_api_uses_same_names_as_missing_ingredients(client, db_conn, monkeypatch):
    user_id, headers = account(client)
    db_conn.execute("UPDATE recipe_ingredients SET name='재료 양파' WHERE recipe_id=1 AND name='양파'")
    db_conn.execute("INSERT INTO recipe_tags (recipe_id,tag_type,tag_value) VALUES (1,'ingredient','오이')")
    monkeypatch.setattr(price_agent, 'get_all_prices', lambda: [kamis('양파', 1500), kamis('오이', 2000)])
    response = client.get('/recommendation/recipes/1/price', params={'user_id': user_id}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    # 기본 1인 가구 / 원본 2인분이므로 양파 50g의 절반인 25g.
    assert data['included'][0]['ingredient'] == '양파'
    assert data['included'][0]['cost'] == pytest.approx(37.5)
    # 수량 행이 없는 태그도 참고 시세는 연결된다.
    assert {row['ingredient'] for row in data['matched']} == {'양파', '오이'}


def test_empty_api_prices_are_retriable_not_cached_as_no_data(client, monkeypatch):
    user_id, headers = account(client)
    monkeypatch.setattr(price_agent, 'get_all_prices', lambda: [])
    first = client.get('/recommendation/recipes/1/price', params={'user_id': user_id}, headers=headers)
    assert first.status_code == 503
    monkeypatch.setattr(price_agent, 'get_all_prices', lambda: [kamis('양파', 1500)])
    second = client.get('/recommendation/recipes/1/price', params={'user_id': user_id}, headers=headers)
    assert second.status_code == 200
    assert second.json()['included'][0]['ingredient'] == '양파'
