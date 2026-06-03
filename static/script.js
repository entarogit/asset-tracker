// 전역 변수
let portfolio = { stocks: [], cash: 0 };
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

// 숫자를 미국 달러 형식으로 포맷
function formatUSD(amount) {
    return new Intl.NumberFormat('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
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
            displayPortfolio(data);
            updateCashDisplay(data.cash);
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
        const evaluationKRW = isUS
            ? (stock.current_price_krw * stock.quantity)
            : (currentPrice * stock.quantity);

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
                <div class="stock-details">
                    <div class="detail-item">
                        <div class="detail-label">현재가</div>
                        <div class="detail-value">${isUS ? formatUSD(currentPrice) : formatCurrency(currentPrice)}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">평단가</div>
                        <div class="detail-value">${isUS ? formatUSD(avgPrice) : formatCurrency(avgPrice)}</div>
                    </div>
                    <div class="detail-item">
                        <div class="detail-label">보유수량</div>
                        <div class="detail-value">${stock.quantity.toLocaleString()}주</div>
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

// 현금 표시 업데이트
function updateCashDisplay(cash) {
    document.getElementById('cash-amount').textContent = formatCurrency(cash);
    document.getElementById('cash-input').value = cash;
}

// 자산 요약 로드
async function loadAssetSummary() {
    try {
        const response = await fetch('/api/asset-summary');
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            portfolio.cash = data.cash;
            document.getElementById('total-asset').textContent = formatCurrency(data.total_asset);
            document.getElementById('stock-value').textContent = formatCurrency(data.total_stock_value);
            document.getElementById('cash-amount').textContent = formatCurrency(data.cash);
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

// 주식 추가
async function addStock() {
    if (isLoading) return;

    const market = document.getElementById('stock-market').value;
    const symbol = document.getElementById('stock-symbol').value.trim();
    const quantity = parseFloat(document.getElementById('stock-quantity').value);
    const avgPrice = parseFloat(document.getElementById('stock-avg-price').value);

    if (!symbol) { showNotification('종목명/티커를 입력해주세요.', 'error'); return; }
    if (!quantity || quantity <= 0) { showNotification('올바른 수량을 입력해주세요.', 'error'); return; }
    if (!avgPrice || avgPrice <= 0) { showNotification('올바른 평단가를 입력해주세요.', 'error'); return; }

    showLoading();
    try {
        const response = await fetch('/api/portfolio', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, market, quantity, avg_price: avgPrice })
        });
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            showNotification(data.merged ? data.message : '주식이 성공적으로 추가되었습니다.', 'success');
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

// 예수금 서버 업데이트 공통 함수
async function setCash(amount) {
    if (isLoading) return;
    showLoading();
    try {
        const response = await fetch('/api/cash', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cash: amount })
        });
        if (handleAuthError(response)) return;
        const data = await response.json();
        if (response.ok) {
            portfolio.cash = amount;
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
    await setCash(Math.round(next));
    document.getElementById('cash-input-krw').value = '';
}

// 달러 예수금 업데이트 (환율 자동 변환)
async function updateCashUSD() {
    const parsed = parseCashInput(document.getElementById('cash-input-usd').value);
    if (!parsed) { showNotification('+금액, -금액 또는 숫자를 입력해주세요.', 'error'); return; }
    const krwAmount = Math.round(parsed.amount * currentExchangeRate);
    const current = portfolio.cash || 0;
    const next = parsed.mode === 'add' ? current + krwAmount
               : parsed.mode === 'sub' ? current - krwAmount
               : krwAmount;
    if (next < 0) { showNotification('예수금은 0원 미만이 될 수 없습니다.', 'error'); return; }
    const msg = `$${parsed.amount.toLocaleString()} → ${formatCurrency(krwAmount)} 변환 적용`;
    await setCash(next);
    if (next >= 0) showNotification(msg, 'info');
    document.getElementById('cash-input-usd').value = '';
}

// 예수금 초기화
async function resetCash() {
    if (!confirm('예수금을 0원으로 초기화하시겠습니까?')) return;
    await setCash(0);
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
        if (event.target.closest('.add-stock-form')) {
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
