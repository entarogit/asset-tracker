import os
import re
import time
from datetime import datetime
from functools import wraps
from urllib.parse import quote

from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from authlib.integrations.flask_client import OAuth
import requests
from bs4 import BeautifulSoup

load_dotenv()

# 로컬 개발 환경에서 HTTP OAuth 허용
if os.environ.get('FLASK_ENV') == 'development' or os.environ.get('OAUTHLIB_INSECURE_TRANSPORT'):
    os.environ['AUTHLIB_INSECURE_TRANSPORT'] = '1'

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-this-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///asset_tracker.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

CORS(app, supports_credentials=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID'),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)


# ── 데이터베이스 모델 ──────────────────────────────────────────────────────────

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    google_id = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(200), nullable=False)
    name = db.Column(db.String(200))
    picture = db.Column(db.String(500))
    cash = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    stocks = db.relationship('Stock', backref='user', lazy=True, cascade='all, delete-orphan')


class Stock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    symbol = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    market = db.Column(db.String(10), nullable=False, default='kr')
    avg_price = db.Column(db.Float)       # KRW 평단가
    avg_price_usd = db.Column(db.Float)   # USD 평단가


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith('/api/'):
        return jsonify({'error': '로그인이 필요합니다', 'authenticated': False}), 401
    return redirect(url_for('index'))


# ── 인증 라우트 ────────────────────────────────────────────────────────────────

@app.route('/login')
def login_page():
    return redirect(url_for('index'))


@app.route('/auth/login')
def auth_login():
    redirect_uri = os.environ.get('GOOGLE_REDIRECT_URI', 'http://localhost:8080/auth/callback')
    return google.authorize_redirect(redirect_uri)


@app.route('/auth/callback')
def auth_callback():
    try:
        token = google.authorize_access_token()
        userinfo = token.get('userinfo')
        if not userinfo:
            return redirect(url_for('index'))

        user = User.query.filter_by(google_id=userinfo['sub']).first()
        if not user:
            user = User(
                google_id=userinfo['sub'],
                email=userinfo['email'],
                name=userinfo.get('name', ''),
                picture=userinfo.get('picture', '')
            )
            db.session.add(user)
        else:
            user.name = userinfo.get('name', user.name)
            user.picture = userinfo.get('picture', user.picture)

        db.session.commit()
        login_user(user, remember=True)
        return redirect(url_for('index'))
    except Exception as e:
        print(f"OAuth 콜백 오류: {e}")
        return redirect(url_for('index'))


@app.route('/auth/logout')
@login_required
def auth_logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/api/auth/status')
def auth_status():
    if current_user.is_authenticated:
        return jsonify({
            'authenticated': True,
            'user': {
                'name': current_user.name,
                'email': current_user.email,
                'picture': current_user.picture
            }
        })
    return jsonify({'authenticated': False}), 401


# ── 메인 페이지 ────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    if not current_user.is_authenticated:
        return render_template('login.html')
    return render_template('index.html')


# ── 주식 가격 조회 (기존 로직 유지) ──────────────────────────────────────────

def get_price_from_google_finance(stock_code):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    google_url = f'https://www.google.com/finance/quote/{stock_code}:KRX'
    try:
        response = requests.get(google_url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            price_element = soup.select('[data-last-price]')
            if price_element:
                price_text = price_element[0].get('data-last-price', '')
                if price_text:
                    price_match = re.search(r'([0-9,]+\.?[0-9]*)', price_text)
                    if price_match:
                        price = float(price_match.group(1).replace(',', ''))
                        return price
            price_display = soup.select('.YMlKec.fxKbKc')
            if price_display:
                price_text = price_display[0].get_text(strip=True)
                price_match = re.search(r'₩?([0-9,]+\.?[0-9]*)', price_text)
                if price_match:
                    return float(price_match.group(1).replace(',', ''))
    except Exception as e:
        print(f"구글 파이낸스 조회 오류: {e}")
    return None


# ── 한국 종목 사전 (모듈 레벨) ───────────────────────────────────────────────
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

exchange_rate_cache = {'rate': None, 'timestamp': 0}


def get_exchange_rate():
    global exchange_rate_cache
    current_time = time.time()
    if (exchange_rate_cache['rate'] is not None and
            current_time - exchange_rate_cache['timestamp'] < 5 * 60):
        return exchange_rate_cache['rate']

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    # 방법 1: Yahoo Finance API (USDKRW=X)
    try:
        resp = requests.get(
            'https://query1.finance.yahoo.com/v8/finance/chart/USDKRW=X',
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            result = data.get('chart', {}).get('result', [])
            if result:
                rate = float(result[0]['meta']['regularMarketPrice'])
                if 900 <= rate <= 2000:
                    exchange_rate_cache = {'rate': rate, 'timestamp': current_time}
                    print(f"[환율] Yahoo Finance: {rate}")
                    return rate
    except Exception as e:
        print(f"[환율] Yahoo Finance 실패: {e}")

    # 방법 2: Frankfurter API (무료, 키 불필요)
    try:
        resp = requests.get(
            'https://api.frankfurter.app/latest?from=USD&to=KRW',
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            rate = float(data['rates']['KRW'])
            if 900 <= rate <= 2000:
                exchange_rate_cache = {'rate': rate, 'timestamp': current_time}
                print(f"[환율] Frankfurter API: {rate}")
                return rate
    except Exception as e:
        print(f"[환율] Frankfurter 실패: {e}")

    # 방법 3: 네이버 금융 API (JSON 엔드포인트)
    try:
        resp = requests.get(
            'https://m.stock.naver.com/api/forex/FX_USDKRW',
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            rate = float(data.get('closePrice', '0').replace(',', ''))
            if 900 <= rate <= 2000:
                exchange_rate_cache = {'rate': rate, 'timestamp': current_time}
                print(f"[환율] 네이버 API: {rate}")
                return rate
    except Exception as e:
        print(f"[환율] 네이버 API 실패: {e}")

    # 이전 캐시값 유지 (소스 전체 실패 시)
    if exchange_rate_cache['rate'] is not None:
        print(f"[환율] 모든 소스 실패 - 캐시값 유지: {exchange_rate_cache['rate']}")
        return exchange_rate_cache['rate']

    print("[환율] 모든 소스 실패 - 기본값 1320 사용")
    exchange_rate_cache = {'rate': 1320.0, 'timestamp': current_time}
    return 1320.0


def get_stock_price_naver(symbol, is_us=False):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

        if is_us:
            try:
                api_url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
                api_response = requests.get(api_url, headers=headers, timeout=10)
                if api_response.status_code == 200:
                    data = api_response.json()
                    if 'chart' in data and data['chart']['result']:
                        result = data['chart']['result'][0]
                        if 'meta' in result and 'regularMarketPrice' in result['meta']:
                            price = float(result['meta']['regularMarketPrice'])
                            usd_krw = get_exchange_rate()
                            return {
                                "symbol": symbol,
                                "price_usd": price,
                                "price_krw": price * usd_krw,
                                "exchange_rate": usd_krw,
                                "currency": "USD"
                            }
            except Exception:
                pass
            return {"error": f"'{symbol}' 미국 주식 가격 정보를 찾을 수 없습니다."}

        # 한국 주식
        stock_code = None

        # 방법 1: 6자리 숫자
        if symbol.isdigit() and len(symbol) == 6:
            stock_code = symbol

        # 방법 2: 종목명 사전
        if not stock_code:
            stock_code = KR_STOCK_DICT.get(symbol)

        # 방법 3: 네이버 자동완성 API로 종목코드 검색
        if not stock_code:
            try:
                resp = requests.get(
                    'https://ac.stock.naver.com/ac',
                    params={'q': symbol, 'target': 'stock,etf'},
                    headers=headers,
                    timeout=5
                )
                if resp.status_code == 200:
                    items = resp.json().get('items', [])
                    for item in items:
                        if item['name'] == symbol:  # 정확 일치 우선
                            stock_code = item['code']
                            break
                    if not stock_code and items:    # 첫 번째 결과로 폴백
                        stock_code = items[0]['code']
            except Exception:
                pass

        if not stock_code:
            return {"error": f"'{symbol}' 종목을 찾을 수 없습니다. 정확한 종목명이나 6자리 종목코드를 입력해주세요."}

        # 구글 파이낸스 시도
        google_price = get_price_from_google_finance(stock_code)
        if google_price:
            return {"symbol": symbol, "price": google_price, "currency": "KRW", "name": symbol, "source": "Google Finance"}

        # 다음 금융 API
        headers.update({'Referer': 'https://finance.daum.net/', 'Accept': 'application/json'})
        url = f'https://finance.daum.net/api/quotes/A{stock_code}'
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if 'tradePrice' in data:
                return {
                    "symbol": symbol,
                    "price": float(data['tradePrice']),
                    "currency": "KRW",
                    "name": data.get('name', symbol),
                    "change": data.get('change', 'EVEN'),
                    "changePrice": data.get('changePrice', 0),
                    "changeRate": data.get('changeRate', 0)
                }
        return {"error": f"'{symbol}' 종목의 가격 정보를 가져올 수 없습니다."}

    except Exception as e:
        return {"error": f"가격 조회 중 오류가 발생했습니다: {str(e)}"}


# ── API 라우트 (사용자별) ──────────────────────────────────────────────────────

@app.route('/api/search')
@login_required
def search_stocks():
    query = request.args.get('q', '').strip()
    market = request.args.get('market', 'kr').lower()

    if not query:
        return jsonify([])

    results = []

    if market == 'kr':
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        try:
            resp = requests.get(
                'https://ac.stock.naver.com/ac',
                params={'q': query, 'target': 'stock,etf'},
                headers=headers,
                timeout=5
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
            # 로컬 딕셔너리 폴백
            query_upper = query.upper()
            for name, code in KR_STOCK_DICT.items():
                if query_upper in name.upper() or query_upper in code:
                    results.append({'symbol': name, 'code': code, 'market_type': ''})
            results.sort(key=lambda x: (not x['symbol'].upper().startswith(query_upper), x['symbol']))
            results = results[:10]
    else:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        try:
            resp = requests.get(
                'https://query2.finance.yahoo.com/v1/finance/search',
                params={'q': query, 'quotesCount': 8, 'newsCount': 0, 'listsCount': 0},
                headers=headers,
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get('quotes', []):
                    if item.get('quoteType') in ('EQUITY', 'ETF'):
                        results.append({
                            'symbol': item.get('symbol', ''),
                            'name': item.get('shortname', item.get('longname', '')),
                            'exchange': item.get('exchDisp', '')
                        })
        except Exception as e:
            print(f"[검색] Yahoo Finance 실패: {e}")

    return jsonify(results)


@app.route('/api/price/<symbol>')
@login_required
def get_price(symbol):
    is_us = request.args.get('market', '').lower() == 'us'
    return jsonify(get_stock_price_naver(symbol, is_us))


@app.route('/api/portfolio', methods=['GET'])
@login_required
def get_portfolio():
    stocks_data = []
    for stock in current_user.stocks:
        is_us = stock.market == 'us'
        price_info = get_stock_price_naver(stock.symbol, is_us)

        stock_dict = {
            'db_id': stock.id,
            'symbol': stock.symbol,
            'quantity': stock.quantity,
            'market': stock.market,
            'avg_price': stock.avg_price,
            'avg_price_usd': stock.avg_price_usd,
        }

        if 'error' not in price_info:
            if is_us:
                stock_dict['current_price_usd'] = price_info['price_usd']
                stock_dict['current_price_krw'] = price_info['price_krw']
                stock_dict['exchange_rate'] = price_info['exchange_rate']
                total_cost = stock.avg_price_usd * stock.quantity
                current_value = price_info['price_usd'] * stock.quantity
                stock_dict['profit_loss_usd'] = current_value - total_cost
                stock_dict['profit_loss_krw'] = stock_dict['profit_loss_usd'] * price_info['exchange_rate']
                stock_dict['profit_rate'] = (stock_dict['profit_loss_usd'] / total_cost * 100) if total_cost > 0 else 0
            else:
                stock_dict['current_price'] = price_info['price']
                total_cost = stock.avg_price * stock.quantity
                current_value = price_info['price'] * stock.quantity
                stock_dict['profit_loss'] = current_value - total_cost
                stock_dict['profit_rate'] = (stock_dict['profit_loss'] / total_cost * 100) if total_cost > 0 else 0
        else:
            stock_dict['error'] = price_info['error']

        stocks_data.append(stock_dict)

    return jsonify({'stocks': stocks_data, 'cash': current_user.cash})


@app.route('/api/portfolio', methods=['POST'])
@login_required
def add_stock():
    data = request.json
    if not data or not all(field in data for field in ['symbol', 'quantity']):
        return jsonify({"error": "필수 필드가 누락되었습니다"}), 400

    new_symbol = data['symbol'].strip().upper()
    new_quantity = float(data['quantity'])
    new_market = data.get('market', 'kr').lower()
    new_avg_price = float(data.get('avg_price', 0))

    # 동일 종목 중복 체크
    existing = Stock.query.filter_by(
        user_id=current_user.id,
        symbol=new_symbol,
        market=new_market
    ).first()

    if existing:
        total_qty = existing.quantity + new_quantity
        if new_market == 'us':
            merged_avg = (existing.quantity * existing.avg_price_usd + new_quantity * new_avg_price) / total_qty
            existing.avg_price_usd = merged_avg
        else:
            merged_avg = (existing.quantity * existing.avg_price + new_quantity * new_avg_price) / total_qty
            existing.avg_price = merged_avg
        existing.quantity = total_qty
        db.session.commit()
        price_unit = '$' if new_market == 'us' else '₩'
        return jsonify({
            "message": f"{new_symbol} 종목이 기존 보유분과 합산되었습니다. (총 {total_qty}주, 평단가 {price_unit}{merged_avg:,.2f})",
            "merged": True
        })

    new_stock = Stock(
        user_id=current_user.id,
        symbol=new_symbol,
        quantity=new_quantity,
        market=new_market,
        avg_price=new_avg_price if new_market == 'kr' else None,
        avg_price_usd=new_avg_price if new_market == 'us' else None
    )
    db.session.add(new_stock)
    db.session.commit()
    return jsonify({"message": "주식이 추가되었습니다", "merged": False})


@app.route('/api/portfolio/<int:stock_id>', methods=['DELETE'])
@login_required
def delete_stock(stock_id):
    stock = Stock.query.filter_by(id=stock_id, user_id=current_user.id).first()
    if not stock:
        return jsonify({"error": "주식을 찾을 수 없습니다"}), 404
    symbol = stock.symbol
    db.session.delete(stock)
    db.session.commit()
    return jsonify({"message": f"{symbol} 주식이 삭제되었습니다"})


@app.route('/api/cash', methods=['POST'])
@login_required
def update_cash():
    data = request.json
    current_user.cash = float(data.get('cash', 0))
    db.session.commit()
    return jsonify({"message": "예수금이 업데이트되었습니다"})


@app.route('/api/exchange-rate')
@login_required
def get_current_exchange_rate():
    exchange_rate = get_exchange_rate()
    return jsonify({
        "exchange_rate": exchange_rate,
        "cached": exchange_rate_cache['timestamp'] > 0,
        "cache_age": time.time() - exchange_rate_cache['timestamp'] if exchange_rate_cache['timestamp'] > 0 else 0
    })


@app.route('/api/asset-summary')
@login_required
def get_asset_summary():
    total_stock_value_krw = 0
    total_profit_loss_krw = 0
    current_exchange_rate = get_exchange_rate()

    for stock in current_user.stocks:
        is_us = stock.market == 'us'
        price_info = get_stock_price_naver(stock.symbol, is_us)
        if 'error' not in price_info:
            if is_us:
                current_value_krw = price_info['price_krw'] * stock.quantity
                cost_krw = stock.avg_price_usd * stock.quantity * price_info['exchange_rate']
            else:
                current_value_krw = price_info['price'] * stock.quantity
                cost_krw = stock.avg_price * stock.quantity
            total_stock_value_krw += current_value_krw
            total_profit_loss_krw += (current_value_krw - cost_krw)

    return jsonify({
        "total_stock_value": total_stock_value_krw,
        "cash": current_user.cash,
        "total_asset": total_stock_value_krw + current_user.cash,
        "total_profit_loss": total_profit_loss_krw,
        "exchange_rate": current_exchange_rate
    })


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=8080)
