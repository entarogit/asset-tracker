import os
import re
import time
from datetime import datetime
from urllib.parse import quote
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

# 로컬 HTTP 환경에서 OAuth 허용
if os.environ.get('FLASK_ENV') == 'development' or os.environ.get('OAUTHLIB_INSECURE_TRANSPORT'):
    os.environ['AUTHLIB_INSECURE_TRANSPORT'] = '1'

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth, OAuthError
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey, text
from sqlalchemy.orm import DeclarativeBase, relationship, Session, sessionmaker
from pydantic import BaseModel
import requests as http_requests
from bs4 import BeautifulSoup

try:
    from yfinance.data import YfData as _YfData
    _yf_data = _YfData()
    HAS_YFINANCE = True
except Exception:
    _yf_data = None
    HAS_YFINANCE = False


# ── 앱 설정 ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            with engine.connect() as conn:
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN cash_usd FLOAT DEFAULT 0.0"))
                    conn.commit()
                except Exception:
                    conn.rollback()
            print("DB 테이블 생성 완료")
            break
        except Exception as e:
            print(f"DB 연결 대기 중... ({attempt + 1}/10): {e}")
            await asyncio.sleep(3)
    yield

app = FastAPI(title="자산 트래킹", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get('SECRET_KEY', 'dev-secret-key-change-this')
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(_BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_BASE_DIR, "templates"))


# ── Google OAuth ──────────────────────────────────────────────────────────────

oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)


# ── 데이터베이스 ──────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:///./asset_tracker.db')

# Railway PostgreSQL URL 호환 처리 (psycopg3 드라이버 사용)
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql+psycopg://', 1)
elif DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+psycopg://', 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith('sqlite') else {"connect_timeout": 5}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    google_id = Column(String(100), unique=True, nullable=False)
    email = Column(String(200), nullable=False)
    name = Column(String(200))
    picture = Column(String(500))
    cash = Column(Float, default=0.0)      # KRW 예수금
    cash_usd = Column(Float, default=0.0)  # USD 예수금
    created_at = Column(DateTime, default=datetime.utcnow)
    stocks = relationship('Stock', back_populates='user', cascade='all, delete-orphan')


class Stock(Base):
    __tablename__ = 'stocks'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    symbol = Column(String(100), nullable=False)
    quantity = Column(Float, nullable=False)
    market = Column(String(10), nullable=False, default='kr')
    avg_price = Column(Float)
    avg_price_usd = Column(Float)
    user = relationship('User', back_populates='stocks')


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── 인증 의존성 ───────────────────────────────────────────────────────────────

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get('user_id')
    if not user_id:
        raise HTTPException(
            status_code=401,
            detail={"error": "로그인이 필요합니다", "authenticated": False}
        )
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "사용자를 찾을 수 없습니다", "authenticated": False}
        )
    return user


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


# ── 한국 종목 사전 (폴백용) ───────────────────────────────────────────────────

KR_STOCK_DICT = {
    'SK하이닉스': '000660', '삼성전자': '005930', 'LG에너지솔루션': '373220',
    'NAVER': '035420', '카카오': '035720', '현대차': '005380',
    'POSCO홀딩스': '005490', 'LG화학': '051910', '삼성바이오로직스': '207940',
    'SK텔레콤': '017670', '현대모비스': '012330', '기아': '000270',
    'LG전자': '066570', 'KB금융': '105560', '신한지주': '055550',
    'HLB': '028300', '셀트리온': '068270', '두산에너빌리티': '034020',
    '삼성SDI': '006400', 'LG디스플레이': '034220', '하나금융지주': '086790',
    '삼성화재': '000810', '포스코DX': '022100', '우리금융지주': '316140',
    'SK이노베이션': '096770', '삼성물산': '028260', '현대제철': '004020',
    'LG': '003550', '롯데케미칼': '011170', 'SK': '034730',
    'KT': '030200', '한국전력': '015760', '삼성생명': '032830',
    '한국가스공사': '036460', '삼성중공업': '010140', '현대건설': '000720',
    '포스코': '005490', 'LG유플러스': '032640', '대한항공': '003490',
    'CJ대한통운': '000120', '아모레퍼시픽': '090430', '메리츠금융지주': '138040',
    '하이브': '352820', '카카오뱅크': '323410', '크래프톤': '259960',
    '컴투스': '078340', '넷마블': '251270', 'SK스퀘어': '402340'
}


# ── 환율 조회 ─────────────────────────────────────────────────────────────────

exchange_rate_cache = {'rate': None, 'timestamp': 0}


def get_exchange_rate() -> float:
    global exchange_rate_cache
    current_time = time.time()
    if exchange_rate_cache['rate'] and current_time - exchange_rate_cache['timestamp'] < 300:
        return exchange_rate_cache['rate']

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        resp = http_requests.get(
            'https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X',
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            result = resp.json().get('chart', {}).get('result', [])
            if result:
                rate = float(result[0]['meta']['regularMarketPrice'])
                if 900 <= rate <= 2000:
                    exchange_rate_cache = {'rate': rate, 'timestamp': current_time}
                    return rate
    except Exception as e:
        print(f"[환율] Yahoo Finance 실패: {e}")

    try:
        resp = http_requests.get('https://api.frankfurter.app/latest?from=USD&to=KRW', timeout=10)
        if resp.status_code == 200:
            rate = float(resp.json()['rates']['KRW'])
            if 900 <= rate <= 2000:
                exchange_rate_cache = {'rate': rate, 'timestamp': current_time}
                return rate
    except Exception as e:
        print(f"[환율] Frankfurter 실패: {e}")

    try:
        resp = http_requests.get(
            'https://m.stock.naver.com/api/forex/FX_USDKRW',
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            rate = float(resp.json().get('closePrice', '0').replace(',', ''))
            if 900 <= rate <= 2000:
                exchange_rate_cache = {'rate': rate, 'timestamp': current_time}
                return rate
    except Exception as e:
        print(f"[환율] 네이버 API 실패: {e}")

    if exchange_rate_cache['rate']:
        return exchange_rate_cache['rate']

    exchange_rate_cache = {'rate': 1320.0, 'timestamp': current_time}
    return 1320.0


# ── 주식 가격 조회 ────────────────────────────────────────────────────────────

def get_price_from_naver(stock_code: str) -> Optional[dict]:
    """네이버 실시간/시간외 API에서 한국 주식 가격+등락률 조회"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
        'Accept': 'application/json',
        'Referer': 'https://m.stock.naver.com/'
    }

    def _f(val) -> Optional[float]:
        try:
            return float(str(val).replace(',', '').replace('%', ''))
        except (ValueError, TypeError):
            return None

    # 1차: 폴링 API (시간외 포함)
    try:
        resp = http_requests.get(
            f'https://polling.finance.naver.com/api/realtime/domestic/stock/{stock_code}',
            headers=headers, timeout=8
        )
        if resp.status_code == 200:
            item = resp.json().get('datas', [{}])[0]
            ot = item.get('overTimeClosePriceInfo') or item.get('overTimeInfo') or {}
            ot_price = _f(ot.get('closePrice') or ot.get('price', ''))
            if ot_price:
                rate = _f(ot.get('fluctuationsRatio', item.get('fluctuationsRatio', 0)))
                return {"price": ot_price, "change_rate": rate or 0}
            price = _f(item.get('closePrice', ''))
            if price:
                rate = _f(item.get('fluctuationsRatio', 0))
                return {"price": price, "change_rate": rate or 0}
    except Exception as e:
        print(f"[Naver 폴링] {stock_code}: {e}")

    # 2차: 모바일 기본 API
    try:
        resp = http_requests.get(
            f'https://m.stock.naver.com/api/stock/{stock_code}/basic',
            headers=headers, timeout=8
        )
        if resp.status_code == 200:
            data = resp.json()
            for ot_key in ['overTimeInfo', 'overTimeClosePriceInfo']:
                ot = data.get(ot_key) or {}
                for pk in ['closePrice', 'price', 'overTimePrice']:
                    p = _f(ot.get(pk, ''))
                    if p:
                        rate = _f(ot.get('fluctuationsRatio', data.get('fluctuationsRatio', 0)))
                        return {"price": p, "change_rate": rate or 0}
            price = _f(data.get('closePrice', ''))
            if price:
                rate = _f(data.get('fluctuationsRatio', 0))
                return {"price": price, "change_rate": rate or 0}
    except Exception as e:
        print(f"[Naver 모바일] {stock_code}: {e}")

    return None


def get_price_from_google_finance(stock_code: str) -> Optional[float]:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        resp = http_requests.get(
            f'https://www.google.com/finance/quote/{stock_code}:KRX',
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, 'html.parser')
            for el in soup.select('[data-last-price]'):
                text = el.get('data-last-price', '')
                m = re.search(r'([0-9,]+\.?[0-9]*)', text)
                if m:
                    return float(m.group(1).replace(',', ''))
            for el in soup.select('.YMlKec.fxKbKc'):
                m = re.search(r'₩?([0-9,]+\.?[0-9]*)', el.get_text(strip=True))
                if m:
                    return float(m.group(1).replace(',', ''))
    except Exception as e:
        print(f"구글 파이낸스 조회 오류: {e}")
    return None


def _get_us_price_yf(symbol: str):
    """yfinance v10 quoteSummary로 미국 주식 가격과 등락률 조회"""
    if not HAS_YFINANCE or _yf_data is None:
        return None, 0
    try:
        result = _yf_data.get_raw_json(
            f'https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}',
            params={'modules': 'price'}
        )
        price_data = result.get('quoteSummary', {}).get('result', [{}])[0].get('price', {})
        price = price_data.get('regularMarketPrice', {}).get('raw')
        change_rate_raw = price_data.get('regularMarketChangePercent', {}).get('raw')
        if price and change_rate_raw is not None:
            return float(price), float(change_rate_raw) * 100
    except Exception as e:
        print(f"[yfinance v10] {symbol}: {e}")
    return None, 0


def get_stock_price(symbol: str, is_us: bool = False) -> dict:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    try:
        if is_us:
            price, change_rate = _get_us_price_yf(symbol)
            if price:
                usd_krw = get_exchange_rate()
                return {
                    "symbol": symbol, "price_usd": price,
                    "price_krw": price * usd_krw,
                    "exchange_rate": usd_krw, "currency": "USD",
                    "change_rate": change_rate
                }
            # 폴백: v8 차트 API
            try:
                resp = http_requests.get(
                    f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}',
                    headers=headers, timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data['chart']['result']:
                        meta = data['chart']['result'][0]['meta']
                        price = float(meta['regularMarketPrice'])
                        usd_krw = get_exchange_rate()
                        return {
                            "symbol": symbol, "price_usd": price,
                            "price_krw": price * usd_krw,
                            "exchange_rate": usd_krw, "currency": "USD",
                            "change_rate": 0
                        }
            except Exception:
                pass
            return {"error": f"'{symbol}' 미국 주식 가격 정보를 찾을 수 없습니다."}

        # 한국 주식
        stock_code = None

        if symbol.isdigit() and len(symbol) == 6:
            stock_code = symbol

        if not stock_code:
            stock_code = KR_STOCK_DICT.get(symbol)

        if not stock_code:
            try:
                resp = http_requests.get(
                    'https://ac.stock.naver.com/ac',
                    params={'q': symbol, 'target': 'stock,etf'},
                    headers=headers, timeout=5
                )
                if resp.status_code == 200:
                    items = resp.json().get('items', [])
                    for item in items:
                        if item['name'] == symbol:
                            stock_code = item['code']
                            break
                    if not stock_code and items:
                        stock_code = items[0]['code']
            except Exception:
                pass

        if not stock_code:
            return {"error": f"'{symbol}' 종목을 찾을 수 없습니다."}

        # 1순위: 네이버 API (시간외 포함)
        naver_result = get_price_from_naver(stock_code)
        if naver_result:
            return {"symbol": symbol, "price": naver_result["price"], "currency": "KRW",
                    "change_rate": naver_result.get("change_rate", 0)}

        # 2순위: 구글 파이낸스
        google_price = get_price_from_google_finance(stock_code)
        if google_price:
            return {"symbol": symbol, "price": google_price, "currency": "KRW", "change_rate": 0}

        # 3순위: 다음 파이낸스
        req_headers = {**headers, 'Referer': 'https://finance.daum.net/', 'Accept': 'application/json'}
        resp = http_requests.get(f'https://finance.daum.net/api/quotes/A{stock_code}', headers=req_headers)
        if resp.status_code == 200:
            data = resp.json()
            if 'tradePrice' in data:
                return {"symbol": symbol, "price": float(data['tradePrice']), "currency": "KRW", "change_rate": 0}

        return {"error": f"'{symbol}' 종목의 가격 정보를 가져올 수 없습니다."}

    except Exception as e:
        return {"error": f"가격 조회 중 오류가 발생했습니다: {str(e)}"}


# ── Pydantic 모델 ─────────────────────────────────────────────────────────────

class StockCreate(BaseModel):
    symbol: str
    quantity: float
    market: str = 'kr'
    avg_price: float = 0.0
    deduct_cash: bool = False  # True면 매입금액만큼 예수금에서 차감


class CashUpdate(BaseModel):
    cash_krw: Optional[float] = None
    cash_usd: Optional[float] = None


class StockUpdate(BaseModel):
    quantity: Optional[float] = None
    avg_price: Optional[float] = None


# ── 페이지 라우트 ─────────────────────────────────────────────────────────────

@app.get('/')
async def index(request: Request):
    if not request.session.get('user_id'):
        return templates.TemplateResponse("login.html", {"request": request})
    return templates.TemplateResponse("index.html", {"request": request})


@app.get('/login')
async def login_page():
    return RedirectResponse(url='/', status_code=302)


@app.get('/auth/login')
async def auth_login(request: Request):
    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:8080/auth/callback')
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get('/auth/callback')
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = token.get('userinfo')
        if not userinfo:
            return RedirectResponse(url='/', status_code=302)

        user = db.query(User).filter(User.google_id == userinfo['sub']).first()
        if not user:
            user = User(
                google_id=userinfo['sub'],
                email=userinfo['email'],
                name=userinfo.get('name', ''),
                picture=userinfo.get('picture', '')
            )
            db.add(user)
        else:
            user.name = userinfo.get('name', user.name)
            user.picture = userinfo.get('picture', user.picture)

        db.commit()
        db.refresh(user)
        request.session['user_id'] = user.id
        return RedirectResponse(url='/', status_code=302)
    except Exception as e:
        print(f"OAuth 콜백 오류: {e}")
        return RedirectResponse(url='/', status_code=302)


@app.get('/auth/logout')
async def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url='/', status_code=302)


# ── API 라우트 ────────────────────────────────────────────────────────────────

@app.get('/api/auth/status')
def auth_status(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get('user_id')
    if not user_id:
        return JSONResponse(status_code=401, content={"authenticated": False})
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return JSONResponse(status_code=401, content={"authenticated": False})
    return {"authenticated": True, "user": {
        "name": user.name, "email": user.email, "picture": user.picture
    }}


@app.get('/api/search')
def search_stocks(
    q: str = '',
    market: str = 'kr',
    current_user: User = Depends(get_current_user)
):
    if not q:
        return []

    results = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    if market == 'kr':
        try:
            resp = http_requests.get(
                'https://ac.stock.naver.com/ac',
                params={'q': q, 'target': 'stock,etf'},
                headers=headers, timeout=5
            )
            if resp.status_code == 200:
                for item in resp.json().get('items', []):
                    results.append({
                        'symbol': item['name'],
                        'code': item['code'],
                        'market_type': item.get('typeName', '')
                    })
        except Exception as e:
            print(f"[검색] 네이버 API 실패: {e}")
            query_upper = q.upper()
            for name, code in KR_STOCK_DICT.items():
                if query_upper in name.upper() or query_upper in code:
                    results.append({'symbol': name, 'code': code, 'market_type': ''})
            results.sort(key=lambda x: (not x['symbol'].upper().startswith(query_upper), x['symbol']))
            results = results[:10]
    else:
        try:
            resp = http_requests.get(
                'https://query2.finance.yahoo.com/v1/finance/search',
                params={'q': q, 'quotesCount': 8, 'newsCount': 0, 'listsCount': 0},
                headers=headers, timeout=5
            )
            if resp.status_code == 200:
                for item in resp.json().get('quotes', []):
                    if item.get('quoteType') in ('EQUITY', 'ETF'):
                        results.append({
                            'symbol': item.get('symbol', ''),
                            'name': item.get('shortname', item.get('longname', '')),
                            'exchange': item.get('exchDisp', '')
                        })
        except Exception as e:
            print(f"[검색] Yahoo Finance 실패: {e}")

    return results


@app.get('/api/price/{symbol}')
def get_price(symbol: str, market: str = 'kr', current_user: User = Depends(get_current_user)):
    return get_stock_price(symbol, market.lower() == 'us')


@app.get('/api/portfolio')
def get_portfolio(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stocks_data = []
    user = db.query(User).filter(User.id == current_user.id).first()

    for stock in user.stocks:
        is_us = stock.market == 'us'
        price_info = get_stock_price(stock.symbol, is_us)

        item = {
            'db_id': stock.id,
            'symbol': stock.symbol,
            'quantity': stock.quantity,
            'market': stock.market,
            'avg_price': stock.avg_price,
            'avg_price_usd': stock.avg_price_usd,
        }

        if 'error' not in price_info:
            item['change_rate'] = price_info.get('change_rate', 0)
            if is_us:
                item['current_price_usd'] = price_info['price_usd']
                item['current_price_krw'] = price_info['price_krw']
                item['exchange_rate'] = price_info['exchange_rate']
                total_cost = stock.avg_price_usd * stock.quantity
                current_value = price_info['price_usd'] * stock.quantity
                item['profit_loss_usd'] = current_value - total_cost
                item['profit_loss_krw'] = item['profit_loss_usd'] * price_info['exchange_rate']
                item['profit_rate'] = (item['profit_loss_usd'] / total_cost * 100) if total_cost > 0 else 0
            else:
                item['current_price'] = price_info['price']
                total_cost = stock.avg_price * stock.quantity
                current_value = price_info['price'] * stock.quantity
                item['profit_loss'] = current_value - total_cost
                item['profit_rate'] = (item['profit_loss'] / total_cost * 100) if total_cost > 0 else 0
        else:
            item['error'] = price_info['error']

        stocks_data.append(item)

    return {'stocks': stocks_data, 'cash': user.cash or 0.0, 'cash_usd': user.cash_usd or 0.0}


@app.post('/api/portfolio')
def add_stock(
    data: StockCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    new_symbol = data.symbol.strip().upper()
    new_quantity = data.quantity
    new_market = data.market.lower()
    new_avg_price = data.avg_price
    is_us = new_market == 'us'
    price_unit = '$' if is_us else '₩'

    user = db.query(User).filter(User.id == current_user.id).first()

    # 예수금 차감 요청이면 잔액을 먼저 검증한다 (부족하면 주식도 추가하지 않음)
    cost = new_quantity * new_avg_price
    if not is_us:
        cost = round(cost)  # 원화 예수금은 원 단위로 관리
    if data.deduct_cash:
        if cost <= 0:
            raise HTTPException(status_code=400, detail={
                "error": "수량과 평단가가 모두 0보다 커야 예수금에서 차감할 수 있습니다"
            })
        balance = (user.cash_usd or 0.0) if is_us else (user.cash or 0.0)
        if cost - balance > 1e-9:
            raise HTTPException(status_code=400, detail={
                "error": (f"예수금이 부족합니다. "
                          f"필요 {price_unit}{cost:,.2f} / 보유 {price_unit}{balance:,.2f} / "
                          f"부족 {price_unit}{cost - balance:,.2f}")
            })

    existing = db.query(Stock).filter(
        Stock.user_id == current_user.id,
        Stock.symbol == new_symbol,
        Stock.market == new_market
    ).first()

    if existing:
        total_qty = existing.quantity + new_quantity
        if is_us:
            merged_avg = (existing.quantity * existing.avg_price_usd + new_quantity * new_avg_price) / total_qty
            existing.avg_price_usd = merged_avg
        else:
            merged_avg = (existing.quantity * existing.avg_price + new_quantity * new_avg_price) / total_qty
            existing.avg_price = merged_avg
        existing.quantity = total_qty
        result = {
            "message": f"{new_symbol} 종목이 기존 보유분과 합산되었습니다. (총 {total_qty}주, 평단가 {price_unit}{merged_avg:,.2f})",
            "merged": True
        }
    else:
        db.add(Stock(
            user_id=current_user.id,
            symbol=new_symbol,
            quantity=new_quantity,
            market=new_market,
            avg_price=new_avg_price if not is_us else None,
            avg_price_usd=new_avg_price if is_us else None
        ))
        result = {"message": "주식이 추가되었습니다", "merged": False}

    # 주식 추가와 예수금 차감을 같은 트랜잭션으로 커밋한다
    if data.deduct_cash:
        if is_us:
            user.cash_usd = (user.cash_usd or 0.0) - cost
            remaining = user.cash_usd
        else:
            user.cash = (user.cash or 0.0) - cost
            remaining = user.cash
        result["message"] += (f" 예수금에서 {price_unit}{cost:,.2f}이(가) 차감되었습니다. "
                              f"(잔액 {price_unit}{remaining:,.2f})")
        result["cash_deducted"] = cost
        result["cash_currency"] = 'USD' if is_us else 'KRW'
        result["remaining_cash"] = remaining

    db.commit()
    return result


@app.patch('/api/portfolio/{stock_id}')
def update_stock(
    stock_id: int,
    data: StockUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    stock = db.query(Stock).filter(Stock.id == stock_id, Stock.user_id == current_user.id).first()
    if not stock:
        raise HTTPException(status_code=404, detail={"error": "주식을 찾을 수 없습니다"})
    if data.quantity is not None:
        stock.quantity = data.quantity
    if data.avg_price is not None:
        if stock.market == 'us':
            stock.avg_price_usd = data.avg_price
        else:
            stock.avg_price = data.avg_price
    db.commit()
    return {"message": f"{stock.symbol} 정보가 업데이트되었습니다"}


@app.delete('/api/portfolio/{stock_id}')
def delete_stock(
    stock_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    stock = db.query(Stock).filter(Stock.id == stock_id, Stock.user_id == current_user.id).first()
    if not stock:
        raise HTTPException(status_code=404, detail={"error": "주식을 찾을 수 없습니다"})
    symbol = stock.symbol
    db.delete(stock)
    db.commit()
    return {"message": f"{symbol} 주식이 삭제되었습니다"}


@app.post('/api/cash')
def update_cash(
    data: CashUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if data.cash_krw is not None:
        user.cash = data.cash_krw
    if data.cash_usd is not None:
        user.cash_usd = data.cash_usd
    db.commit()
    return {"message": "예수금이 업데이트되었습니다"}


@app.get('/api/exchange-rate')
def get_exchange_rate_api(current_user: User = Depends(get_current_user)):
    rate = get_exchange_rate()
    return {
        "exchange_rate": rate,
        "cached": exchange_rate_cache['timestamp'] > 0,
        "cache_age": time.time() - exchange_rate_cache['timestamp']
    }


@app.get('/api/asset-summary')
def get_asset_summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == current_user.id).first()
    total_stock_value = 0
    total_profit_loss = 0
    total_cost = 0
    current_rate = get_exchange_rate()

    for stock in user.stocks:
        is_us = stock.market == 'us'
        price_info = get_stock_price(stock.symbol, is_us)
        if 'error' not in price_info:
            if is_us:
                current_value_krw = price_info['price_krw'] * stock.quantity
                cost_krw = stock.avg_price_usd * stock.quantity * price_info['exchange_rate']
            else:
                current_value_krw = price_info['price'] * stock.quantity
                cost_krw = stock.avg_price * stock.quantity
            total_stock_value += current_value_krw
            total_profit_loss += current_value_krw - cost_krw
            total_cost += cost_krw

    total_profit_rate = (total_profit_loss / total_cost * 100) if total_cost > 0 else 0

    cash_krw = user.cash or 0.0
    cash_usd = user.cash_usd or 0.0
    cash_usd_in_krw = cash_usd * current_rate
    total_cash = cash_krw + cash_usd_in_krw

    return {
        "total_stock_value": total_stock_value,
        "cash": total_cash,
        "cash_krw": cash_krw,
        "cash_usd": cash_usd,
        "cash_usd_in_krw": cash_usd_in_krw,
        "total_asset": total_stock_value + total_cash,
        "total_profit_loss": total_profit_loss,
        "total_profit_rate": total_profit_rate,
        "exchange_rate": current_rate
    }


# ── 로컬 실행 ─────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=True)
