import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { getRecipePrice, getSubstitution, type RecipePrice, type Substitution } from '../api/recipes'
import { getShoppingLinks, type ShoppingLinks } from '../api/shopping'
import { useAuth } from '../auth/context'
import styles from './MissingIngredientsCard.module.css'

/**
 * 제휴 링크에는 대가성 고지가 붙어야 한다. 주체가 사이트일 때와 레시피 작성자일 때가
 * 다르므로 서버가 내려준 `earner`로 고른다 - 화면이 판단하면 서버와 어긋난다.
 */
function disclosure(links: ShoppingLinks | null): string | null {
  if (!links) return null
  if (links.earner === 'author') {
    const who = links.author_name ?? '작성자'
    return `구매 링크는 이 레시피를 올린 ${who}님의 제휴 링크예요. 구매하시면 그분에게 수수료가 갑니다.`
  }
  if (links.earner === 'site') {
    return '구매 링크에는 제휴 링크가 포함될 수 있어요. 구매하시면 운영자에게 일정 수수료가 갑니다.'
  }
  // 'none' - 아무 키도 안 붙은 일반 검색 링크라 고지할 것이 없다.
  return null
}

/**
 * "이거 지금 만들 수 있나".
 *
 * 서버는 부족한 재료와 대체 제안을 원래부터 계산하고 있었는데(substitution_agent) 화면이
 * 부르지 않아 아무도 못 봤다(2026-08-18까지). 추천 카드의 "2개만 더 있으면 돼요"가
 * 여기서 "무엇이" 부족한지로 이어진다.
 *
 * 대체 제안은 세 종류다. 바꿔 쓸 수 있는 것(substitute), 소량이라 빼도 되는 것(omit),
 * 그리고 알 수 없는 것(unknown). 마지막을 "사야 한다"고 단정하지 않는다 - 대체재 정보가
 * 없다는 뜻이지 반드시 사야 한다는 뜻이 아니다.
 *
 * 냉장고에 저장된 재료가 기준이다. 추천 화면에서 그때그때 고친 재료는 반영되지 않으므로
 * 그 사실을 화면에 적는다 - 안 적으면 "아까 뺐는데 왜 아직 있지"가 된다.
 */
export default function MissingIngredientsCard({ recipeId }: { recipeId: number }) {
  const { userId, isAuthenticated } = useAuth()
  if (!isAuthenticated || userId === null) {
    return (
      <section className={styles.card} aria-labelledby="missing-heading">
        <h2 id="missing-heading">지금 만들 수 있나요?</h2>
        <p className={styles.guide}>
          로그인하고 냉장고에 재료를 넣으면 무엇이 부족한지 알려드려요.
        </p>
      </section>
    )
  }
  // 계정이나 레시피를 바꾸면 이전 가격·재료·구매 링크가 남지 않게 함께 초기화한다.
  return <SignedInCard key={`${userId}:${recipeId}`} recipeId={recipeId} userId={userId} />
}

function SignedInCard({ recipeId, userId }: { recipeId: number; userId: number }) {
  const [data, setData] = useState<Substitution | null>(null)
  const [failed, setFailed] = useState(false)
  const [links, setLinks] = useState<ShoppingLinks | null>(null)
  const [price, setPrice] = useState<RecipePrice | null>(null)
  const [priceFailed, setPriceFailed] = useState(false)
  const [priceAttempt, setPriceAttempt] = useState(0)
  const hasMissing = Boolean(data?.missing_ingredients.length)

  useEffect(() => {
    const controller = new AbortController()
    getSubstitution(recipeId, userId, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setData(result) })
      .catch(() => {
        // 레시피 본문은 이미 보인다. 이 카드 하나 때문에 화면을 망치지 않는다.
        if (!controller.signal.aborted) setFailed(true)
      })
    // 구매 링크는 따로 받는다. 이쪽이 실패해도 부족한 재료 목록은 그대로 보여야 한다 -
    // 무료 티어에서 쿠팡 변환이 느리거나 실패하는 일이 정상 범위에 있다.
    getShoppingLinks(recipeId, userId, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setLinks(result) })
      .catch(() => {})
    return () => controller.abort()
  }, [recipeId, userId])

  useEffect(() => {
    if (!hasMissing) return
    const controller = new AbortController()
    getRecipePrice(recipeId, userId, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setPrice(result) })
      .catch(() => { if (!controller.signal.aborted) setPriceFailed(true) })
    return () => controller.abort()
  }, [recipeId, userId, hasMissing, priceAttempt])

  if (failed || data === null) return null

  const { coverage, missing_ingredients: missing } = data

  return (
    <section className={styles.card} aria-labelledby="missing-heading">
      <div className={styles.head}>
        <h2 id="missing-heading">지금 만들 수 있나요?</h2>
        {coverage.coverage_pct !== null && (
          <span className={styles.pct}>재료 {coverage.coverage_pct}% 보유</span>
        )}
      </div>

      {missing.length === 0 ? (
        <p className={styles.ready}>
          <span aria-hidden="true">✅</span> 필요한 재료가 냉장고에 다 있어요.
        </p>
      ) : (
        <>
          <p className={styles.summary}>
            {coverage.total}개 중 <strong>{missing.length}개</strong>가 없어요.
          </p>
          <div className={styles.priceGuide}>
            <strong>KAMIS 참고 가격</strong>
            {priceFailed ? (
              <p role="status">
                가격을 불러오지 못했어요.{' '}
                <button type="button" onClick={() => {
                  setPriceFailed(false)
                  setPrice(null)
                  setPriceAttempt((attempt) => attempt + 1)
                }}>가격 다시 조회</button>
              </p>
            ) : price === null ? (
              <p role="status">가격을 확인하고 있어요…</p>
            ) : null}
            <p>
              식단 정보의 인원수에 맞춘 사용량 기준이에요.{' '}
              <a href="https://www.kamis.or.kr/" target="_blank" rel="noopener noreferrer">KAMIS</a>{' '}
              전국 소매 시세를 우선하고, 대응 소매 시세가 없으면 도매 시세를 참고해요.
            </p>
            <p>실제 구매 금액은 판매처와 포장 크기에 따라 달라요. 가격마다 표시된 조사 기준일을 확인해 주세요.</p>
            <p>가공식품 등 대응 시세가 없는 재료는 가격을 표시하지 않아요.</p>
          </div>
          <ul className={styles.list}>
            {missing.map((item) => {
              // links.links를 그냥 믿지 않는다. 응답 모양이 예상과 다르면 부족한 재료
              // 목록 전체가 안 보이게 되는데, 구매 버튼 하나 때문에 그러면 안 된다.
              // (홈 화면이 영상 분류 때문에 통째로 빈 화면이 됐던 것과 같은 종류다.)
              const buy = Array.isArray(links?.links)
                ? links.links.find((l) => l?.ingredient === item.ingredient)
                : undefined
              return (
                <li key={item.ingredient} className={styles[item.type] ?? styles.unknown}>
                  <span className={styles.name}>{item.ingredient}</span>
                  <span className={styles.suggestion}>{item.suggestion}</span>
                  {price && <IngredientCost ingredient={item.ingredient} price={price} />}
                  {buy && (
                    <span className={styles.buy}>
                      {/* 외부 사이트로 나간다. noreferrer까지 붙이는 이유는 어느 화면에서
                          왔는지를 상대 사이트에 넘기지 않기 위해서다. */}
                      <a href={buy.coupang} target="_blank" rel="noopener noreferrer">
                        쿠팡<span className="sr-only">에서 {item.ingredient} 검색</span>
                      </a>
                      <a href={buy.naver} target="_blank" rel="noopener noreferrer">
                        네이버<span className="sr-only">에서 {item.ingredient} 검색</span>
                      </a>
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
          {disclosure(links) && <p className={styles.disclosure}>{disclosure(links)}</p>}
        </>
      )}

      {/* 안 적으면 "추천 화면에서 뺐는데 왜 아직 있지"가 된다. */}
      <p className={styles.basis}>
        냉장고에 저장된 재료 기준이에요. <Link to="/pantry">냉장고 고치기</Link>
      </p>
    </section>
  )
}

const won = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 })
const grams = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 })

function IngredientCost({ ingredient, price }: { ingredient: string; price: RecipePrice }) {
  // 서버가 매칭한 재료명만 사용한다. 다른 재료나 보유 재료의 비용을 합산하지 않는다.
  const rows = price.included.filter((row) => row?.ingredient === ingredient)
  const hasExcludedPart = price.excluded.some((row) => row?.ingredient === ingredient)
  const valid = rows.length > 0 && !hasExcludedPart && rows.every((row) =>
    Number.isFinite(row.cost) && row.cost > 0 && Number.isFinite(row.amount_g) && row.amount_g > 0,
  )
  const market = price.matched.find((row) => row?.ingredient === ingredient)
  const source = market && [
    market.source_region, market.source_market,
    market.price_date ? `${market.price_date} 기준` : (market.source_market ? '기준일 미확인' : ''),
  ].filter(Boolean).join(' · ')
  const detail = market && [market.kind_name, market.rank].filter(Boolean).join(' · ')
  if (valid) {
    const amount = rows.reduce((sum, row) => sum + row.amount_g, 0)
    const cost = rows.reduce((sum, row) => sum + row.cost, 0)
    const costText = cost < 1 ? '1원 미만' : `약 ${won.format(cost)}원`
    const matchedNames = [...new Set(rows.map((row) => row.matched_name))]
    const conversionNotes = [...new Set(rows.map((row) => row.conversion_note).filter(Boolean))]
    const sourceAmount = rows.reduce((sum, row) => sum + (row.source_amount_g ?? 0), 0)
    return (
      <span className={styles.price}>
        필요량 {grams.format(amount)}g · {conversionNotes.length ? '쌀 가격으로 환산한 추정 비용' : '예상 비용'} <strong>{costText}</strong>
        {conversionNotes.length > 0 && Number.isFinite(sourceAmount) && sourceAmount > 0 && (
          <small>취사 전 쌀 약 {grams.format(sourceAmount)}g으로 환산</small>
        )}
        {conversionNotes.map((note) => <small key={note}>{note}</small>)}
        {matchedNames.some((name) => name !== ingredient) && <small>시세 기준 품목: {matchedNames.join(', ')}</small>}
        {detail && <small>조사 품목: {detail}</small>}
        {source && <small>{source}</small>}
        {rows.some((row) => row.is_estimated && !row.conversion_note) && <small>평균 중량으로 환산한 추정치</small>}
      </span>
    )
  }
  if (market && Number.isFinite(market.price) && market.price > 0 && market.unit) {
    return (
      <span className={styles.price}>
        참고 시세 {market.unit}당 <strong>약 {won.format(market.price)}원</strong>
        {market.item_name !== ingredient && <small>시세 기준 품목: {market.item_name}</small>}
        {market.conversion_note && <small>{market.conversion_note}</small>}
        {detail && <small>조사 품목: {detail}</small>}
        {source && <small>{source}</small>}
        <small>필요한 양의 비용은 계산할 수 없어요.</small>
      </span>
    )
  }
  return <span className={styles.noPrice}>연결 가능한 KAMIS 시세 없음 · 비용 계산에서 제외</span>
}
