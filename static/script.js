// 전역 변수
let portfolio = { stocks: [], cash: 0, cash_usd: 0 };
let isLoading = false;
let currentExchangeRate = 1320;

// 자동완성 상태
let acIndex = -1;
let acItems = [];
let acDropdown = null;
let acInput = null;

// DOM 로드 완료 시 실행
document.addEventListener('DOMContentLoaded', function() {
    loadUserInfo();
    loadPortfolio();
    loadAssetSummary();
    loadExchangeRate();
    initAutocomplete();
});

// 로딩 오버레이 표시/숨김
function showLoading(show = true) {
    const overlay = document.getElementById('loading-overlay');
    if (show) {
        overlay.classList.add('show');
        isLoading = true;
    } else {
        overlay.classList.remove('show');
        isLoading = false;
    }
}

// 알림 메시지 표시
function showNotification(message, type = 'info') {
    const notification = document.getElementById('notification');
    notification.textContent = message;
    notification.className = `notification ${type}`;
    notification.classList.add('show');
    setTimeout(() => notification.classList.remove('show'), 3000);
}

// 숫자를 한국 원화 형식으로 포맷
function formatCurrency(amount) {
    return new Intl.NumberFormat('ko-KR', {
        style: 'currency',
        currency: 'KRW',
        maximumFractionDigits: 0
    }).format(amount);
}

// 숫자를 미국 달러 형식으로 포맷 (소수점 최대 4자리)
function formatUSD(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 4
    }).format(amount);
}

// 수익률을 퍼센트 형식으로 포맷
function formatPercent(rate) {
    const sign = rate >= 0 ? '+' : '';
    return `${sign}${rate.toFixed(2)}%`;
}

// 인증 오류 처리 (401 응답 시 로그인 페이지로)
function handleAuthError(response) {
    if (response.status === 401) {
        window.location.href = '/login';
        return true;
    }
    return false;
}

// 사용자 정보 로드
async function loadUserInfo() {
    try {
        const response = await fetch('/api/auth/status');
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (data.authenticated) {
            document.getElementById('user-name').textContent = data.user.name;
            document.getElementById('user-email').textContent = data.user.email;
            const avatar = document.getElementById('user-avatar');
            if (data.user.picture) {
                avatar.src = data.user.picture;
                avatar.style.display = 'block';
            }
        }
    } catch (error) {
        console.error('사용자 정보 로드 오류:', error);
    }
}

// 포트폴리오 로드
async function loadPortfolio() {
    try {
        const response = await fetch('/api/portfolio');
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            portfolio = data;
            portfolio.cash_usd = data.cash_usd || 0;
            displayPortfolio(data);
            updateCashDisplay(data.cash, data.cash_usd || 0);
        } else {
            showNotification('포트폴리오를 불러오는데 실패했습니다.', 'error');
        }
    } catch (error) {
        console.error('포트폴리오 로드 오류:', error);
        showNotification('네트워크 오류가 발생했습니다.', 'error');
    }
}

// 포트폴리오 표시
function displayPortfolio(data) {
    const grid = document.getElementById('portfolio-grid');
    if (!grid) return;

    if (!data.stocks || data.stocks.length === 0) {
        grid.innerHTML = '<div class="loading">보유 주식이 없습니다. 주식을 추가해보세요!</div>';
        return;
    }

    grid.innerHTML = data.stocks.map((stock) => {
        const isUS = stock.market === 'us';
        const hasError = stock.error;

        if (hasError) {
            return `
                <div class="stock-card">
                    <div class="stock-header">
                        <div class="stock-info">
                            <h3>${stock.symbol}</h3>
                            <span class="market-badge ${isUS ? 'market-us' : 'market-kr'}">
                                ${isUS ? '🇺🇸 미국' : '🇰🇷 한국'}
                            </span>
                        </div>
                        <button onclick="deleteStock(${stock.db_id}, '${stock.symbol}')" class="btn btn-danger delete-btn">삭제</button>
                    </div>
                    <div class="error-message">${stock.error}</div>
                </div>
            `;
        }

        const currentPrice = isUS ? stock.current_price_usd : stock.current_price;
        const avgPrice = isUS ? stock.avg_price_usd : stock.avg_price;
        const profitLossKRW = isUS ? stock.profit_loss_krw : stock.profit_loss;
        const profitRate = stock.profit_rate || 0;
        const profitClass = profitRate >= 0 ? 'positive' : 'negative';
        const changeRate = stock.change_rate || 0;
        const changeClass = changeRate >= 0 ? 'positive' : 'negative';
        const evaluationKRW = isUS
            ? (stock.current_price_krw * stock.quantity)
            : (currentPrice * stock.quantity);

        return `
            <div class="stock-card" id="stock-card-${stock.db_id}">
                <div class="stock-header">
                    <div class="stock-info">
                        <h3>${stock.symbol}</h3>
                        <span class="market-badge ${isUS ? 'market-us' : 'market-kr'}">
                            ${isUS ? '🇺🇸 미국' : '🇰🇷 한국'}
                        </span>
                    </div>
                    <div class="card-actions">
                        <button onclick="openChart('${stock.symbol}', '${stock.market}')" class="btn btn-chart">차트</button>
                        <button onclick="toggleEditStock(${stock.db_id})" class="btn btn-secondary edit-btn" id="edit-btn-${stock.db_id}">수정</button>
                        <button onclick="deleteStock(${stock.db_id}, '${stock.symbol}')" class="btn btn-danger delete-btn">삭제</button>
                    </div>
                </div>
                <div class="stock-details">
                    <div class="detail-item">
                        <div class="detail-label">현재가</div>
                        <div class="detail-value">${isUS ? formatUSD(currentPrice) : formatCurrency(currentPrice)}</div>
                        ${changeRate !== 0 ? `<div class="change-rate profit-loss ${changeClass}">${formatPercent(changeRate)}</div>` : ''}
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">평단가</div>
                        <div class="detail-value view-only">${isUS ? formatUSD(avgPrice) : formatCurrency(avgPrice)}</div>
                        <input class="edit-only detail-input" id="edit-avg-${stock.db_id}" type="number" value="${avgPrice}" step="${isUS ? '0.0001' : '1'}" min="0" style="display:none;">
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">보유수량</div>
                        <div class="detail-value view-only">${stock.quantity.toLocaleString()}주</div>
                        <input class="edit-only detail-input" id="edit-qty-${stock.db_id}" type="number" value="${stock.quantity}" step="0.001" min="0.001" style="display:none;">
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">평가금액</div>
                        <div class="detail-value">${formatCurrency(evaluationKRW)}</div>
                    </div>
                </div>
                <div class="stock-summary">
                    <div class="profit-info">
                        <div class="profit-amount profit-loss ${profitClass}">${formatCurrency(profitLossKRW)}</div>
                        <div class="profit-rate profit-loss ${profitClass}">${formatPercent(profitRate)}</div>
                    </div>
                    ${isUS ? `<div class="exchange-info"><small>환율: ${stock.exchange_rate?.toFixed(2) || 'N/A'} 원/달러</small></div>` : ''}
                </div>
            </div>
        `;
    }).join('');
}

// 현금 표시 업데이트 (관리 섹션 현황)
function updateCashDisplay(cashKrw, cashUsd) {
    const krw = cashKrw || 0;
    const usd = cashUsd || 0;
    document.getElementById('current-cash-krw').textContent = formatCurrency(krw);
    document.getElementById('current-cash-usd').textContent = formatUSD(usd);
    const usdInKrw = usd * currentExchangeRate;
    document.getElementById('current-cash-usd-krw').textContent =
        usd > 0 ? `≈ ${formatCurrency(usdInKrw)}` : '';
}

// 자산 요약 로드
async function loadAssetSummary() {
    try {
        const response = await fetch('/api/asset-summary');
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            portfolio.cash = data.cash_krw ?? data.cash ?? 0;
            portfolio.cash_usd = data.cash_usd || 0;
            document.getElementById('total-asset').textContent = formatCurrency(data.total_asset);
            document.getElementById('stock-value').textContent = formatCurrency(data.total_stock_value);
            document.getElementById('cash-amount').textContent = formatCurrency(data.cash);
            // 예수금 카드 원화/달러 구성 표시
            document.getElementById('cash-krw-display').textContent = formatCurrency(data.cash_krw || 0);
            document.getElementById('cash-usd-display').textContent = formatUSD(data.cash_usd || 0);
            // 관리 섹션 현황 갱신
            updateCashDisplay(data.cash_krw || 0, data.cash_usd || 0);
            const totalProfitElement = document.getElementById('total-profit-loss');
            totalProfitElement.textContent = formatCurrency(data.total_profit_loss);
            totalProfitElement.className = `amount profit-loss ${data.total_profit_loss >= 0 ? 'positive' : 'negative'}`;
            const totalRateElement = document.getElementById('total-profit-rate');
            const totalRate = data.total_profit_rate ?? 0;
            totalRateElement.textContent = formatPercent(totalRate);
            totalRateElement.className = `profit-rate profit-loss ${totalRate >= 0 ? 'positive' : 'negative'}`;
            if (data.exchange_rate) updateExchangeRateDisplay(data.exchange_rate);
        }
    } catch (error) {
        console.error('자산 요약 로드 오류:', error);
    }
}

// 환율 정보 표시 업데이트
function updateExchangeRateDisplay(rate, cached = false, cacheAge = 0) {
    currentExchangeRate = rate;
    const rateElement = document.getElementById('exchange-rate');
    const statusElement = document.getElementById('exchange-status');
    rateElement.textContent = `₩${rate.toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    if (cached && cacheAge < 300) {
        statusElement.textContent = `(캐시됨 - ${Math.floor(cacheAge / 60)}분 ${Math.floor(cacheAge % 60)}초 전)`;
        statusElement.className = 'exchange-status cached';
    } else {
        statusElement.textContent = '(실시간)';
        statusElement.className = 'exchange-status live';
    }
}

// 환율 정보 로드
async function loadExchangeRate() {
    try {
        const response = await fetch('/api/exchange-rate');
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) updateExchangeRateDisplay(data.exchange_rate, data.cached, data.cache_age);
    } catch (error) {
        console.error('환율 로드 오류:', error);
    }
}

// 주식 추가 (deductCash=true면 매입금액만큼 예수금에서 차감)
async function addStock(deductCash = false) {
    if (isLoading) return;

    const market = document.getElementById('stock-market').value;
    const symbol = document.getElementById('stock-symbol').value.trim();
    const quantity = parseFloat(document.getElementById('stock-quantity').value);
    const avgPrice = parseFloat(document.getElementById('stock-avg-price').value);

    if (!symbol) { showNotification('종목명/티커를 입력해주세요.', 'error'); return; }
    if (!quantity || quantity <= 0) { showNotification('올바른 수량을 입력해주세요.', 'error'); return; }
    if (!avgPrice || avgPrice <= 0) { showNotification('올바른 평단가를 입력해주세요.', 'error'); return; }

    if (deductCash) {
        const isUS = market === 'us';
        const cost = isUS ? quantity * avgPrice : Math.round(quantity * avgPrice);
        const balance = (isUS ? portfolio.cash_usd : portfolio.cash) || 0;
        const fmt = isUS ? formatUSD : formatCurrency;
        if (cost > balance) {
            showNotification(
                `${isUS ? '달러' : '원화'} 예수금이 부족합니다. 필요 ${fmt(cost)} / 보유 ${fmt(balance)}`,
                'error'
            );
            return;
        }
        const ok = confirm(
            `${symbol} ${quantity}주를 추가하고, 매입금액 ${fmt(cost)}을(를) ` +
            `${isUS ? '달러' : '원화'} 예수금에서 차감합니다.\n\n` +
            `현재 예수금: ${fmt(balance)}\n차감 후 예수금: ${fmt(balance - cost)}\n\n계속하시겠습니까?`
        );
        if (!ok) return;
    }

    showLoading();
    try {
        const response = await fetch('/api/portfolio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, market, quantity, avg_price: avgPrice, deduct_cash: deductCash })
        });
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            showNotification(
                (deductCash || data.merged) ? data.message : '주식이 성공적으로 추가되었습니다.',
                'success'
            );
            document.getElementById('stock-symbol').value = '';
            document.getElementById('stock-quantity').value = '';
            document.getElementById('stock-avg-price').value = '';
            await Promise.all([loadPortfolio(), loadAssetSummary(), loadExchangeRate()]);
        } else {
            showNotification(data.error || '주식 추가에 실패했습니다.', 'error');
        }
    } catch (error) {
        showNotification('네트워크 오류가 발생했습니다.', 'error');
    } finally {
        showLoading(false);
    }
}

// 차트 페이지 열기
function openChart(symbol, market) {
    let url;
    if (market === 'us') {
        url = `https://finance.yahoo.com/chart/${encodeURIComponent(symbol)}`;
    } else if (/^\d{6}$/.test(symbol)) {
        url = `https://finance.naver.com/item/main.naver?code=${symbol}`;
    } else {
        url = `https://search.naver.com/search.naver?query=${encodeURIComponent(symbol + ' 주가차트')}`;
    }
    window.open(url, '_blank');
}

// 주식 수정 모드 토글
function toggleEditStock(stockId) {
    const card = document.getElementById(`stock-card-${stockId}`);
    const editBtn = document.getElementById(`edit-btn-${stockId}`);
    if (card.classList.contains('editing')) {
        saveStock(stockId);
    } else {
        card.classList.add('editing');
        editBtn.textContent = '완료';
        editBtn.classList.replace('btn-secondary', 'btn-success');
        card.querySelectorAll('.view-only').forEach(el => el.style.display = 'none');
        card.querySelectorAll('.edit-only').forEach(el => el.style.removeProperty('display'));
        document.getElementById(`edit-avg-${stockId}`).focus();
    }
}

// 주식 평단가/수량 저장
async function saveStock(stockId) {
    const newAvgPrice = parseFloat(document.getElementById(`edit-avg-${stockId}`).value);
    const newQty = parseFloat(document.getElementById(`edit-qty-${stockId}`).value);
    if (isNaN(newAvgPrice) || newAvgPrice <= 0) { showNotification('올바른 평단가를 입력해주세요.', 'error'); return; }
    if (isNaN(newQty) || newQty <= 0) { showNotification('올바른 수량을 입력해주세요.', 'error'); return; }

    showLoading();
    try {
        const response = await fetch(`/api/portfolio/${stockId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ avg_price: newAvgPrice, quantity: newQty })
        });
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            showNotification('주식 정보가 업데이트되었습니다.', 'success');
            await Promise.all([loadPortfolio(), loadAssetSummary()]);
        } else {
            showNotification(data.error || '업데이트에 실패했습니다.', 'error');
        }
    } catch (error) {
        showNotification('네트워크 오류가 발생했습니다.', 'error');
    } finally {
        showLoading(false);
    }
}

// 주식 삭제 (DB ID 기반)
async function deleteStock(stockId, symbol) {
    if (isLoading) return;
    if (!confirm(`${symbol} 주식을 삭제하시겠습니까?`)) return;

    showLoading();
    try {
        const response = await fetch(`/api/portfolio/${stockId}`, { method: 'DELETE' });
        if (handleAuthError(response)) return;
        if (response.ok) {
            showNotification('주식이 삭제되었습니다.', 'success');
            await loadPortfolio();
            await loadAssetSummary();
            await loadExchangeRate();
        } else {
            const data = await response.json().catch(() => ({ error: '응답 파싱 실패' }));
            showNotification(data.error || '주식 삭제에 실패했습니다.', 'error');
        }
    } catch (error) {
        showNotification('네트워크 오류가 발생했습니다.', 'error');
    } finally {
        showLoading(false);
    }
}

// 예수금 입력값 파싱: +금액(추가), -금액(차감), 숫자만(절대값)
function parseCashInput(value) {
    const str = value.trim();
    if (!str) return null;
    let mode = 'set';
    let numStr = str;
    if (str.startsWith('+')) { mode = 'add'; numStr = str.slice(1); }
    else if (str.startsWith('-')) { mode = 'sub'; numStr = str.slice(1); }
    const amount = parseFloat(numStr.replace(/,/g, ''));
    if (isNaN(amount) || amount < 0) return null;
    return { mode, amount };
}

// 예수금 서버 업데이트 공통 함수 (cash_krw, cash_usd 중 하나 또는 둘 다 전달)
async function setCash(cashKrw, cashUsd) {
    if (isLoading) return;
    showLoading();
    try {
        const body = {};
        if (cashKrw !== undefined && cashKrw !== null) body.cash_krw = cashKrw;
        if (cashUsd !== undefined && cashUsd !== null) body.cash_usd = cashUsd;
        const response = await fetch('/api/cash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            if (cashKrw !== undefined && cashKrw !== null) portfolio.cash = cashKrw;
            if (cashUsd !== undefined && cashUsd !== null) portfolio.cash_usd = cashUsd;
            showNotification('예수금이 업데이트되었습니다.', 'success');
            await Promise.all([loadAssetSummary(), loadExchangeRate()]);
        } else {
            showNotification(data.error || '예수금 업데이트에 실패했습니다.', 'error');
        }
    } catch (error) {
        showNotification('네트워크 오류가 발생했습니다.', 'error');
    } finally {
        showLoading(false);
    }
}

// 원화 예수금 업데이트
async function updateCashKRW() {
    const parsed = parseCashInput(document.getElementById('cash-input-krw').value);
    if (!parsed) { showNotification('+금액, -금액 또는 숫자를 입력해주세요.', 'error'); return; }
    const current = portfolio.cash || 0;
    const next = parsed.mode === 'add' ? current + parsed.amount
               : parsed.mode === 'sub' ? current - parsed.amount
               : parsed.amount;
    if (next < 0) { showNotification('예수금은 0원 미만이 될 수 없습니다.', 'error'); return; }
    await setCash(Math.round(next), undefined);
    document.getElementById('cash-input-krw').value = '';
}

// 달러 예수금 업데이트 (USD로 별도 저장, 합산 시 환율 적용)
async function updateCashUSD() {
    const parsed = parseCashInput(document.getElementById('cash-input-usd').value);
    if (!parsed) { showNotification('+금액, -금액 또는 숫자를 입력해주세요.', 'error'); return; }
    const current = portfolio.cash_usd || 0;
    const next = parsed.mode === 'add' ? current + parsed.amount
               : parsed.mode === 'sub' ? current - parsed.amount
               : parsed.amount;
    if (next < 0) { showNotification('달러 예수금은 $0 미만이 될 수 없습니다.', 'error'); return; }
    await setCash(undefined, next);
    document.getElementById('cash-input-usd').value = '';
}

// 원화 예수금 초기화
async function resetCashKRW() {
    if (!confirm('원화 예수금을 0원으로 초기화하시겠습니까?')) return;
    await setCash(0, undefined);
}

// 달러 예수금 초기화
async function resetCashUSD() {
    if (!confirm('달러 예수금을 $0으로 초기화하시겠습니까?')) return;
    await setCash(undefined, 0);
}

// 포트폴리오 새로고침
async function refreshPortfolio() {
    if (isLoading) return;
    showNotification('포트폴리오를 새로고침하고 있습니다...', 'info');
    await loadPortfolio();
    await loadAssetSummary();
    showNotification('새로고침이 완료되었습니다.', 'success');
}

// 엔터 키 이벤트 처리
document.addEventListener('keypress', function(event) {
    if (event.key === 'Enter') {
        // 자동완성 드롭다운이 열려있고 항목이 선택된 경우 폼 제출 방지
        if (event.target.id === 'stock-symbol' &&
            acDropdown && acDropdown.classList.contains('open') && acIndex >= 0) {
            event.preventDefault();
            return;
        }
        // 버튼은 자체 onclick으로 처리되므로 제외 (중복 호출 방지)
        if (event.target.closest('.add-stock-form') && event.target.tagName !== 'BUTTON') {
            event.preventDefault();
            addStock();
        }
        if (event.target.id === 'cash-input-krw') {
            event.preventDefault();
            updateCashKRW();
        }
        if (event.target.id === 'cash-input-usd') {
            event.preventDefault();
            updateCashUSD();
        }
    }
});

// ── 자동완성 ─────────────────────────────────────────────────────────────────

function initAutocomplete() {
    acInput = document.getElementById('stock-symbol');
    acDropdown = document.getElementById('autocomplete-dropdown');

    acInput.addEventListener('input', debounce(onSymbolInput, 280));
    acInput.addEventListener('keydown', onSymbolKeydown);

    // 시장 변경 시 드롭다운 초기화
    document.getElementById('stock-market').addEventListener('change', hideAc);

    // 외부 클릭 시 닫기
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.autocomplete-wrapper')) hideAc();
    });
}

function debounce(fn, delay) {
    let timer;
    return function(...args) {
        clearTimeout(timer);
        timer = setTimeout(() => fn.apply(this, args), delay);
    };
}

async function onSymbolInput() {
    const query = acInput.value.trim();
    if (!query) { hideAc(); return; }

    const market = document.getElementById('stock-market').value;
    try {
        const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}&market=${market}`);
        if (!resp.ok) { hideAc(); return; }
        const results = await resp.json();
        renderAc(results, market);
    } catch {
        hideAc();
    }
}

function renderAc(results, market) {
    if (!results.length) { hideAc(); return; }

    acItems = results;
    acIndex = -1;

    acDropdown.innerHTML = results.map((item, i) => {
        const isKr = market === 'kr';
        const badge = isKr
            ? `<span class="ac-code">${item.code}</span>`
            : `<span class="ac-exchange">${item.exchange || ''}</span>`;
        const sub = !isKr && item.name
            ? `<span class="ac-name">${item.name}</span>`
            : '';
        const marketType = isKr && item.market_type
            ? `<span class="ac-market-type">${item.market_type}</span>`
            : '';
        return `
            <div class="ac-item" data-index="${i}">
                <span class="ac-symbol">${item.symbol}</span>
                ${marketType}
                ${sub}
                ${badge}
            </div>`;
    }).join('');

    // 클릭 이벤트 위임
    acDropdown.querySelectorAll('.ac-item').forEach(el => {
        el.addEventListener('mousedown', (e) => {
            e.preventDefault(); // input blur 방지
            selectAc(parseInt(el.dataset.index));
        });
    });

    acDropdown.classList.add('open');
}

function hideAc() {
    acDropdown.classList.remove('open');
    acIndex = -1;
    acItems = [];
}

function selectAc(index) {
    const item = acItems[index];
    if (!item) return;
    acInput.value = item.symbol;
    hideAc();
}

function onSymbolKeydown(e) {
    if (!acDropdown.classList.contains('open')) return;

    const items = acDropdown.querySelectorAll('.ac-item');
    if (!items.length) return;

    if (e.key === 'ArrowDown') {
        e.preventDefault();
        acIndex = Math.min(acIndex + 1, items.length - 1);
        highlightAc(items);
    } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        acIndex = Math.max(acIndex - 1, -1);
        highlightAc(items);
    } else if (e.key === 'Enter' && acIndex >= 0) {
        e.preventDefault();
        e.stopImmediatePropagation();
        selectAc(acIndex);
    } else if (e.key === 'Escape') {
        hideAc();
    }
}

function highlightAc(items) {
    items.forEach((el, i) => el.classList.toggle('ac-highlighted', i === acIndex));
    if (acIndex >= 0) items[acIndex].scrollIntoView({ block: 'nearest' });
}

// ─────────────────────────────────────────────────────────────────────────────

// 자동 새로고침 (5분마다)
setInterval(async () => {
    if (!isLoading) {
        await loadPortfolio();
        await loadAssetSummary();
        await loadExchangeRate();
    }
}, 5 * 60 * 1000);

// 환율만 더 자주 업데이트 (1분마다)
setInterval(async () => {
    if (!isLoading) await loadExchangeRate();
}, 60 * 1000);
