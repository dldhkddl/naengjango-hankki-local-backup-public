"""전체 조회 범위, 소매 우선 선택, 가격 출처와 부분 실패를 검증한다."""
from datetime import date

import pytest
import requests

from src.agents import price_agent
from api.routers.price import MatchedIngredient


def item(name='양파', market='01', price=3000, **extra):
    return {'item_name': name, 'unit': '1kg', 'price': price,
            'category_code': '200', 'market_code': market, **extra}


def test_fetches_all_categories_in_both_markets_without_region_restriction(monkeypatch):
    calls = []
    def get(url, *, params, timeout):
        calls.append(params)
        class Response:
            def json(self):
                return {'data': {'error_code': '000', 'item': [
                    {'item_name': '양파', 'unit': '1kg', 'dpr1': '3,000'}]}}
        return Response()
    monkeypatch.setattr(price_agent.requests, 'get', get)
    rows = price_agent.get_all_prices()
    assert len(rows) == len(calls) == 12
    assert {(p['p_product_cls_code'], p['p_item_category_code']) for p in calls} == {
        (m, c) for m in ['01', '02'] for c in ['100', '200', '300', '400', '500', '600']}
    assert all(p['p_country_code'] == '' and p['p_convert_kg_yn'] == 'N' for p in calls)


def test_retail_is_preferred_and_wholesale_remains_available():
    wholesale = item(market='02', price=1500)
    retail = item(market='01', price=3000)
    assert price_agent.match_ingredient_price('양파', [wholesale, retail]) is retail
    assert price_agent.match_ingredient_price('양파', [wholesale]) is wholesale
    cost = price_agent.estimate_recipe_total_cost([{'name': '양파', 'amount': 100, 'unit': 'g'}], [wholesale, retail])
    assert cost['total_cost'] == 300


def test_exact_wholesale_match_beats_different_retail_product():
    retail = item('알배기배추')
    wholesale = item('배추', market='02')
    assert price_agent.match_ingredient_price('배추', [retail, wholesale]) is wholesale


def test_tier_compares_prices_within_the_same_market():
    names = ['양파', '오이', '당근']
    rows = [item(n, '01', 10000) for n in names] + [item(n, '02', 1000) for n in names]
    result = price_agent.estimate_recipe_price_tier(names, rows)
    assert result['tier'] == '기본'
    assert [r['ratio'] for r in result['matched']] == [1, 1, 1]


def test_fallback_price_keeps_its_own_date_and_grade_in_api(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2026, 1, 5)
    class Response:
        def json(self):
            return {'data': {'error_code': '000', 'item': [{
                'item_name': '사과', 'kind_name': '후지(10개)', 'rank': '상품', 'unit': '10개',
                'dpr1': '-', 'dpr2': 'NaN', 'dpr3': '20,000', 'day3': '1주일전 (12/29)',
            }]}}
    monkeypatch.setattr(price_agent, 'date', FixedDate)
    monkeypatch.setattr(price_agent.requests, 'get', lambda *a, **kw: Response())
    row = price_agent.fetch_category_prices('400', '01')[0]
    response = MatchedIngredient(**row, ingredient='사과', ratio=None)
    assert response.price == 20000
    assert response.price_date == '2025-12-29'
    assert (response.source_market, response.source_region, response.kind_name, response.rank) == (
        '소매', '전국 평균', '후지(10개)', '상품')


def test_one_failed_category_does_not_cache_incomplete_coverage(monkeypatch):
    def fetch(code, market):
        if code == '400' and market == '01':
            raise ValueError('일시 오류')
        return [item()]
    monkeypatch.setattr(price_agent, 'fetch_category_prices', fetch)
    with pytest.raises(ValueError, match='일시 오류'):
        price_agent.get_all_prices()


def test_request_failure_does_not_expose_credentials(monkeypatch):
    def get(*a, **kw):
        raise requests.RequestException('https://example.com/?p_cert_key=secret-fixture')
    monkeypatch.setattr(price_agent.requests, 'get', get)
    with pytest.raises(ValueError) as exc:
        price_agent.fetch_category_prices('400')
    assert 'secret-fixture' not in str(exc.value)


@pytest.mark.parametrize('payload', [['001'], {'error_code': '001'}])
def test_normal_no_data_response_is_not_a_network_error(monkeypatch, payload):
    class Response:
        def json(self):
            return {'data': payload}
    monkeypatch.setattr(price_agent.requests, 'get', lambda *a, **kw: Response())
    assert price_agent.fetch_category_prices('400') == []
