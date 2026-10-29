import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import MissingIngredientsCard from './MissingIngredientsCard'
import { renderWithProviders } from '../test/renderWithProviders'

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

const missing = {
  coverage: { total: 3, matched: 1, missing: 2, coverage_pct: 33 },
  missing_ingredients: [
    { ingredient: '쌀', suggestion: '직접 구매 필요', type: 'core_missing' },
    { ingredient: '두부', suggestion: '대체 가능', type: 'substitute' },
  ],
}
const prices = {
  tier: '정보부족', total_cost: 9295, unmatched: ['두부'],
  included: [
    { ingredient: '쌀', matched_name: '쌀', amount_g: 100, cost: 295, is_estimated: false },
    { ingredient: '당근', matched_name: '당근', amount_g: 200, cost: 9000, is_estimated: false },
  ],
  matched: [{ ingredient: '쌀', item_name: '쌀', unit: '20kg', price: 59000, ratio: 1 }],
  excluded: [{ ingredient: '두부', reason: 'KAMIS 가격 매칭 안 됨' }],
}

function mockApi(priceReply: (url: string) => Promise<Response> = async () => json(prices)) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input)
    if (url.includes('/price?')) return priceReply(url)
    if (url.includes('/shopping-links')) return json({
      earner: 'none', author_name: null,
      links: [{ ingredient: '쌀', coupang: 'https://example.com/c', naver: 'https://example.com/n' }],
    })
    return json(missing)
  })
}

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('naengjango.userId', '116')
  localStorage.setItem('naengjango.token', 'test-only')
})
afterEach(() => vi.restoreAllMocks())

it.each([true, false])('밥 가격의 환산 기준을 표시한다 (필요량 있음: %s)', async (hasCost) => {
  const note = '쌀 1g → 밥 2.3g 가정 · 쌀값만 계산 (취사 비용 제외)'
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    if (String(input).includes('/price?')) return json({
      ...prices,
      included: hasCost ? [{ ingredient: '밥', matched_name: '쌀', amount_g: 230,
        source_amount_g: 100, cost: 300, is_estimated: true, conversion_note: note }] : [],
      matched: [{ ...prices.matched[0], ingredient: '밥', conversion_note: note }],
    })
    return json({ ...missing, missing_ingredients: [{ ingredient: '밥', suggestion: '구매 필요', type: 'core_missing' }] })
  })
  renderWithProviders(<MissingIngredientsCard recipeId={352} />)
  expect(await screen.findByText(note)).toBeInTheDocument()
  expect(screen.getByText('시세 기준 품목: 쌀')).toBeInTheDocument()
  if (hasCost) {
    expect(screen.getByText('취사 전 쌀 약 100g으로 환산')).toBeInTheDocument()
    expect(screen.getByText(/필요량 230g · 쌀 가격으로 환산한 추정 비용/)).toBeInTheDocument()
    expect(screen.getByText('약 300원')).toBeInTheDocument()
    expect(screen.queryByText('평균 중량으로 환산한 추정치')).not.toBeInTheDocument()
  } else {
    expect(screen.getByText(/참고 시세 20kg당/)).toBeInTheDocument()
    expect(screen.queryByText(/쌀 가격으로 환산한 추정 비용/)).not.toBeInTheDocument()
  }
})

it.each([true, false])('사용량 비용과 원본 시세에 시장·기준일·품종을 표시한다 (비용 환산: %s)', async (hasCost) => {
  mockApi(async () => json({ ...prices, included: hasCost ? prices.included : [], matched: [
    { ...prices.matched[0], source_market: '소매', source_region: '전국 평균',
      price_date: '2026-09-07', kind_name: '20kg(20kg)', rank: '상품' },
  ] }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('전국 평균 · 소매 · 2026-09-07 기준')).toBeInTheDocument()
  expect(screen.getByText('조사 품목: 20kg(20kg) · 상품')).toBeInTheDocument()
})

it('부족한 재료의 필요량 비용만 보여주고 미조회 재료를 0원으로 표시하지 않는다', async () => {
  const fetchMock = mockApi()
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('약 295원')).toBeInTheDocument()
  expect(screen.getByText(/필요량 100g/)).toBeInTheDocument()
  expect(screen.getByText('연결 가능한 KAMIS 시세 없음 · 비용 계산에서 제외')).toBeInTheDocument()
  expect(screen.queryByText(/9,000원|9,295원|약 0원/)).not.toBeInTheDocument()
  expect(screen.getByText(/실제 구매 금액은 판매처와 포장 크기에 따라/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'KAMIS' })).toHaveAttribute('href', 'https://www.kamis.or.kr/')
  const calls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/price?'))
  expect(calls).toHaveLength(1)
  expect(String(calls[0][0])).toContain('/recipes/912/price?user_id=116')
})

it('필요량 계산이 안 되면 조사 단위의 참고 시세로 구분한다', async () => {
  mockApi(async () => json({ ...prices, included: [] }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('약 59,000원')).toBeInTheDocument()
  expect(screen.getByText(/참고 시세 20kg당/)).toBeInTheDocument()
  expect(screen.getByText('필요한 양의 비용은 계산할 수 없어요.')).toBeInTheDocument()
})

it('다른 이름의 시세를 사용하면 기준 품목도 표시한다', async () => {
  mockApi(async () => json({ ...prices, included: [], matched: [
    { ...prices.matched[0], item_name: '쌀 대표품목' },
  ] }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('시세 기준 품목: 쌀 대표품목')).toBeInTheDocument()
})

it('가격 조회 실패 후에도 구매 링크를 사용하고 가격만 재시도할 수 있다', async () => {
  const user = userEvent.setup()
  let attempts = 0
  const fetchMock = mockApi(async () => ++attempts === 1 ? json({ detail: 'KAMIS 오류' }, 503) : json(prices))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  await screen.findByText(/가격을 불러오지 못했어요/)
  expect(screen.getByRole('link', { name: /쿠팡에서 쌀 검색/ })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '가격 다시 조회' }))
  expect(await screen.findByText('약 295원')).toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes('/substitution'))).toHaveLength(1)
})

it('느린 가격 응답을 기다리는 동안 재료와 구매 버튼을 먼저 보여준다', async () => {
  let resolve!: (response: Response) => void
  mockApi(() => new Promise((done) => { resolve = done }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('가격을 확인하고 있어요…')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /쿠팡에서 쌀 검색/ })).toBeInTheDocument()
  await act(async () => resolve(json(prices)))
  expect(await screen.findByText('약 295원')).toBeInTheDocument()
})

it('평균 중량 추정과 1원 미만 비용을 구분한다', async () => {
  mockApi(async () => json({ ...prices, included: [
    { ingredient: '쌀', matched_name: '쌀', amount_g: 0.1, cost: 0.295, is_estimated: true },
  ] }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('1원 미만')).toBeInTheDocument()
  expect(screen.getByText('평균 중량으로 환산한 추정치')).toBeInTheDocument()
})

it('같은 재료가 여러 구획에 있으면 필요량과 비용을 합친다', async () => {
  mockApi(async () => json({ ...prices, included: [prices.included[0], prices.included[0]] }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('약 590원')).toBeInTheDocument()
  expect(screen.getByText(/필요량 200g/)).toBeInTheDocument()
})

it('같은 재료의 일부 수량이 누락됐으면 부분 비용을 완전한 비용처럼 보이지 않는다', async () => {
  mockApi(async () => json({ ...prices, excluded: [{ ingredient: '쌀', reason: 'g 단위가 아님' }] }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByText('약 59,000원')).toBeInTheDocument()
  expect(screen.queryByText('약 295원')).not.toBeInTheDocument()
})

it('응답 모양이 잘못되어도 재료와 링크는 유지된다', async () => {
  mockApi(async () => json({ included: null }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  expect(await screen.findByRole('button', { name: '가격 다시 조회' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /쿠팡에서 쌀 검색/ })).toBeInTheDocument()
})

it('부족한 재료가 없으면 가격 API를 호출하지 않는다', async () => {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(json({
    coverage: { total: 3, matched: 3, missing: 0, coverage_pct: 100 }, missing_ingredients: [],
  }))
  renderWithProviders(<MissingIngredientsCard recipeId={912} />)
  await screen.findByText(/냉장고에 다 있어요/)
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/price?'))).toBe(false)
})

it('레시피 변경 뒤 도착한 이전 가격을 새 레시피에 표시하지 않는다', async () => {
  let resolveOld!: (response: Response) => void
  mockApi(async (url) => url.includes('/912/') ? new Promise((done) => { resolveOld = done }) : json({
    ...prices, included: [{ ...prices.included[0], cost: 600 }],
  }))
  function Host() {
    const [id, setId] = useState(912)
    return <><button onClick={() => setId(913)}>다음 레시피</button><MissingIngredientsCard recipeId={id} /></>
  }
  renderWithProviders(<Host />)
  await waitFor(() => expect(resolveOld).toBeDefined())
  await userEvent.setup().click(screen.getByRole('button', { name: '다음 레시피' }))
  await screen.findByText('약 600원')
  await act(async () => resolveOld(json(prices)))
  const rice = screen.getByText('쌀').closest('li')!
  expect(within(rice).getByText('약 600원')).toBeInTheDocument()
  expect(screen.queryByText('약 295원')).not.toBeInTheDocument()
})
