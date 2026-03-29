from flask import Flask, jsonify, request
import requests
import os
import json
from datetime import datetime
from functools import wraps

app = Flask(__name__)

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "049ec69948c512fa657468d624b7deef")
ODDS_BASE = "https://api.the-odds-api.com/v4"
POLYMARKET_BASE = "https://gamma-api.polymarket.com"
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
BASE_URL = os.environ.get("BASE_URL", "https://eventalphaoraclecode-production.up.railway.app")
WALLET_ADDRESS = os.environ.get("WALLET_ADDRESS", "0x3278657Fd9013D48692C146Bb7FC730e67EAa192")
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_MONAD = "0x534b2f3A21130d7a60830c2Df862319e593943A3"
USDC_HEDERA = "0x000000000000000000000000000000000006f89a"
HEDERA_WALLET = "0x00000000000000000000000000000000008cd721"  # Hedera 0.0.9230113 (Tallytrades1)
ALGORAND_WALLET = "5DWBO7N5KU3PXQHXLKDCEALRI4TEOLJG3KTBADTQ734TKZRWMFOA25VLKQ"

DISCLAIMER = "\n\nNOT FINANCIAL ADVICE. Prediction markets carry risk."

# x402 PAYMENT MIDDLEWARE
def require_payment(amount_micro, description, example_input=None, example_output=None):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            payment_sig = (
                request.headers.get("x-payment-signature") or
                request.headers.get("X-Payment-Signature") or
                request.args.get("paymentSig")
            )
            if payment_sig:
                return f(*args, **kwargs)
            response = {
                "x402Version": 2,
                "error": "X-PAYMENT-REQUIRED",
                "accepts": [
                    {
                        "scheme": "exact",
                        "network": "eip155:8453",
                        "asset": USDC_BASE,
                        "payTo": WALLET_ADDRESS,
                        "amount": str(amount_micro),
                        "maxTimeoutSeconds": 60
                    },
                    {
                        "scheme": "exact",
                        "network": "eip155:10143",
                        "asset": USDC_MONAD,
                        "payTo": WALLET_ADDRESS,
                        "amount": str(amount_micro),
                        "maxTimeoutSeconds": 60,
                        "extra": {"name": "USDC", "version": "2", "facilitator": "https://x402-facilitator.molandak.org"}
                    },
                    {
                        "scheme": "exact",
                        "network": "eip155:295",
                        "asset": USDC_HEDERA,
                        "payTo": HEDERA_WALLET,
                        "amount": str(amount_micro),
                        "maxTimeoutSeconds": 60,
                        "extra": {"name": "USDC", "version": "1", "facilitator": "https://x402.blockydevs.com"}
                    },
                    {
                        "scheme": "exact",
                        "network": "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73k",
                        "asset": "31566704",
                        "payTo": ALGORAND_WALLET,
                        "amount": str(amount_micro),
                        "maxTimeoutSeconds": 60,
                        "extra": {"name": "USDC", "version": "1", "facilitator": "https://facilitator.goplausible.xyz"}
                    }
                ],
                "resource": {
                    "url": BASE_URL + request.path,
                    "description": description,
                    "mimeType": "application/json"
                }
            }
            if example_input is not None or example_output is not None:
                response["extensions"] = {
                    "bazaar": {"info": {"input": example_input or {}, "output": example_output or {}}}
                }
            return jsonify(response), 402
        return decorated_function
    return decorator

# HELPERS
def decimal_to_implied(odds):
    if not odds or odds <= 0:
        return 0
    return round((1 / odds) * 100, 1)

def arb_score(prob_a, prob_b):
    if not prob_a or not prob_b:
        return None
    gap = abs(prob_a - prob_b)
    if gap >= 20:
        level, note = "HIGH", "Significant disagreement — potential edge"
    elif gap >= 10:
        level, note = "MODERATE", "Moderate divergence — worth watching"
    else:
        level, note = "LOW", "Markets in agreement — low edge"
    return {"gap": round(gap, 1), "level": level, "note": note}

def edge_signal(vegas_prob, crowd_prob, name):
    if not vegas_prob or not crowd_prob:
        return None
    diff = crowd_prob - vegas_prob
    if diff > 10:
        return f"Crowd OVERVALUES {name} vs Vegas by {round(diff,1)}%"
    elif diff < -10:
        return f"Crowd UNDERVALUES {name} vs Vegas by {round(abs(diff),1)}%"
    else:
        return f"Markets aligned on {name} — no significant edge detected"

def get_vegas_odds(sport_key):
    try:
        url = f"{ODDS_BASE}/sports/{sport_key}/odds"
        params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "decimal"}
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return []

def get_polymarket_top(limit=10):
    try:
        url = f"{POLYMARKET_BASE}/markets"
        params = {"active": "true", "limit": limit, "order": "volume24hr", "ascending": "false"}
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            return r.json()
    except:
        pass
    return []

def get_polymarket_markets(keyword):
    try:
        markets = get_polymarket_top(20)
        keyword_lower = keyword.lower()
        return [m for m in markets if any(word in m.get("question", "").lower() for word in keyword_lower.split())]
    except:
        return []

def get_kalshi_markets(keyword=None, limit=10):
    try:
        url = f"{KALSHI_BASE}/markets"
        params = {"limit": limit, "status": "open"}
        if keyword:
            params["search"] = keyword
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            return r.json().get("markets", [])
    except:
        pass
    return []

def parse_polymarket_prob(market):
    try:
        prices = market.get("outcomePrices")
        if isinstance(prices, list) and len(prices) > 0:
            return round(float(prices[0]) * 100, 1)
        if isinstance(prices, str):
            parsed = json.loads(prices)
            if isinstance(parsed, list) and len(parsed) > 0:
                return round(float(parsed[0]) * 100, 1)
    except:
        pass
    return None

def parse_kalshi_prob(market):
    try:
        yes_bid = market.get("yes_bid") or market.get("last_price")
        if yes_bid:
            return round(float(yes_bid) * 100, 1)
    except:
        pass
    return None

SPORT_KEYS = {
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "mma": "mma_mixed_martial_arts",
    "boxing": "boxing_boxing",
}

# DISCOVERY ENDPOINTS
@app.route("/.well-known/x402")
def well_known_x402():
    return jsonify({
        "version": 1,
        "resources": [
            BASE_URL + "/signal/nba",
            BASE_URL + "/signal/nfl",
            BASE_URL + "/signal/mma",
            BASE_URL + "/signal/boxing",
            BASE_URL + "/signal/politics",
            BASE_URL + "/signal/trending",
            BASE_URL + "/signal/arb"
        ],
        "instructions": "# EventAlphaOracle v2.3\n\nReal-time prediction market signals combining Polymarket, Kalshi, and 40+ sportsbooks.\n\n## Endpoints\n- /signal/nba — NBA signals $0.01\n- /signal/nfl — NFL signals $0.01\n- /signal/mma — MMA signals $0.01\n- /signal/boxing — Boxing signals $0.01\n- /signal/politics — Polymarket vs Kalshi $0.02\n- /signal/trending — Top Polymarket markets $0.01\n- /signal/arb — Arbitrage scanner $0.02\n\n## Networks\nBase Mainnet (eip155:8453) | Monad Testnet (eip155:10143) | Hedera Mainnet (eip155:295) | Algorand Mainnet\n\n## Provider\nNurse2Web3 — https://nurse2web3.com"
    })

@app.route("/x402/discovery")
def x402_discovery():
    return jsonify({
        "x402Version": 2,
        "name": "EventAlphaOracle",
        "version": "2.3",
        "description": "Real-time prediction market signals combining Polymarket, Kalshi, and 40+ sportsbooks. Accepts payments on Base, Monad, Hedera, and Algorand.",
        "provider": "Nurse2Web3",
        "url": BASE_URL,
        "discoverable": True,
        "category": "prediction-markets",
        "tags": ["prediction-markets", "polymarket", "kalshi", "sports", "nba", "nfl", "mma", "boxing", "politics", "arbitrage", "monad", "base", "hedera", "algorand", "hbar"],
        "networks": ["base-mainnet", "monad-testnet", "hedera-mainnet", "algorand-mainnet"],
        "endpoints": [
            {"path": "/signal/nba",      "price": "$0.01", "description": "NBA signals — real Vegas odds + Polymarket analysis"},
            {"path": "/signal/nfl",      "price": "$0.01", "description": "NFL signals — real Vegas odds + Polymarket analysis"},
            {"path": "/signal/mma",      "price": "$0.01", "description": "MMA signals — real Vegas odds + edge score"},
            {"path": "/signal/boxing",   "price": "$0.01", "description": "Boxing signals — real Vegas odds + edge score"},
            {"path": "/signal/politics", "price": "$0.02", "description": "Politics — real Polymarket vs Kalshi divergence"},
            {"path": "/signal/trending", "price": "$0.01", "description": "Top trending Polymarket markets by 24hr volume"},
            {"path": "/signal/arb",      "price": "$0.02", "description": "Arbitrage scanner — biggest Polymarket vs Kalshi gaps"}
        ],
        "dataSources": ["Polymarket Gamma API", "Kalshi API", "The Odds API (40+ sportsbooks)"],
        "contact": {"twitter": "https://twitter.com/nurse2web3", "website": "https://nurse2web3.com"}
    })

# ROOT + HEALTH
@app.route("/")
def index():
    return jsonify({
        "api": "EventAlphaOracle",
        "version": "2.3",
        "description": "Real-time prediction market signals combining Polymarket, Kalshi, and 40+ sportsbooks",
        "endpoints": {
            "/signal/nba":      "$0.01 — NBA Vegas + Polymarket signals",
            "/signal/nfl":      "$0.01 — NFL Vegas + Polymarket signals",
            "/signal/mma":      "$0.01 — MMA Vegas odds + edge score",
            "/signal/boxing":   "$0.01 — Boxing Vegas odds + edge score",
            "/signal/politics": "$0.02 — Polymarket vs Kalshi divergence",
            "/signal/trending": "$0.01 — Top Polymarket markets by volume",
            "/signal/arb":      "$0.02 — Cross-platform arbitrage scanner"
        },
        "networks": {"base": "eip155:8453 (mainnet)", "monad": "eip155:10143 (testnet)", "hedera": "eip155:295 (mainnet)", "algorand": "algorand (mainnet)"},
        "wallet": WALLET_ADDRESS,
        "dataSources": ["Polymarket Gamma API", "Kalshi API", "The Odds API (40+ sportsbooks)"]
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "OK", "service": "EventAlphaOracle", "version": "2.3",
        "wallet": WALLET_ADDRESS, "x402": "enabled", "bazaar": "discoverable",
        "networks": {
            "base": "eip155:8453 (mainnet) — active",
            "monad": "eip155:10143 (testnet) — active",
            "hedera": "eip155:295 (mainnet) — active",
            "algorand": "algorand (mainnet) — active"
        },
        "pricing": {"nba": "$0.01", "nfl": "$0.01", "mma": "$0.01", "boxing": "$0.01", "trending": "$0.01", "politics": "$0.02", "arb": "$0.02"}
    })

# SIGNAL ROUTES
@app.route("/signal/trending")
@require_payment(10000, "Top trending Polymarket prediction markets sorted by 24hr volume.", {}, {"count": 10, "markets": []})
def signal_trending():
    markets = get_polymarket_top(10)
    results = []
    for m in markets:
        prob = parse_polymarket_prob(m)
        results.append({
            "question": m.get("question", ""),
            "yes_probability": prob,
            "volume_24h": round(float(m.get("volume24hr", 0) or 0), 2),
            "liquidity": round(float(m.get("liquidity", 0) or 0), 2),
            "end_date": m.get("endDate", ""),
            "source": "Polymarket"
        })
    return jsonify({
        "signal_type": "TRENDING MARKETS",
        "source": "Polymarket — sorted by 24hr volume",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "count": len(results),
        "markets": results
    })

@app.route("/signal/arb")
@require_payment(20000, "Cross-platform arbitrage scanner. Finds biggest gaps between Polymarket and Kalshi.", {}, {"opportunities_found": 3, "opportunities": []})
def signal_arb():
    poly_markets = get_polymarket_top(20)
    kalshi_markets = get_kalshi_markets(limit=20)
    arb_opportunities = []

    for pm in poly_markets:
        question = pm.get("question", "").lower()
        poly_prob = parse_polymarket_prob(pm)
        if not poly_prob:
            continue
        for km in kalshi_markets:
            k_title = (km.get("title", "") + " " + km.get("subtitle", "")).lower()
            poly_words = set(q for q in question.split() if len(q) > 3)
            kalshi_words = set(k for k in k_title.split() if len(k) > 3)
            if len(poly_words & kalshi_words) >= 3:
                kalshi_prob = parse_kalshi_prob(km)
                if kalshi_prob:
                    gap = abs(poly_prob - kalshi_prob)
                    if gap >= 5:
                        arb_opportunities.append({
                            "event": pm.get("question", ""),
                            "polymarket_yes_prob": poly_prob,
                            "kalshi_yes_prob": kalshi_prob,
                            "gap": round(gap, 1),
                            "arb_level": "HIGH" if gap >= 15 else "MODERATE" if gap >= 8 else "LOW",
                            "signal": f"Polymarket: {poly_prob}% vs Kalshi: {kalshi_prob}% — gap of {round(gap,1)}%",
                            "poly_volume_24h": round(float(pm.get("volume24hr", 0) or 0), 2)
                        })

    arb_opportunities.sort(key=lambda x: x["gap"], reverse=True)
    return jsonify({
        "signal_type": "ARBITRAGE SCANNER",
        "description": "Markets where Polymarket and Kalshi disagree most",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "opportunities_found": len(arb_opportunities),
        "opportunities": arb_opportunities[:10]
    })

@app.route("/signal/politics")
@require_payment(20000, "Political prediction signals. Real Polymarket vs Kalshi divergence analysis.", {"keyword": "election"}, {})
def signal_politics():
    poly_markets = get_polymarket_markets("election president senate congress vote")
    kalshi_markets = get_kalshi_markets(limit=15)

    poly_results = []
    for m in poly_markets[:8]:
        prob = parse_polymarket_prob(m)
        poly_results.append({
            "question": m.get("question", ""),
            "yes_probability": prob,
            "volume_24h": round(float(m.get("volume24hr", 0) or 0), 2),
            "source": "Polymarket"
        })

    kalshi_results = []
    for m in kalshi_markets[:8]:
        prob = parse_kalshi_prob(m)
        kalshi_results.append({
            "event": m.get("title", ""),
            "yes_probability": prob,
            "volume": m.get("volume", 0),
            "source": "Kalshi"
        })

    return jsonify({
        "signal_type": "POLITICS",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "polymarket_markets": poly_results,
        "kalshi_markets": kalshi_results,
        "tip": "Compare probabilities across platforms — divergence signals potential edge"
    })

@app.route("/signal/<sport>")
@require_payment(10000, "Sports prediction market signals. Real Vegas odds from 40+ sportsbooks cross-referenced with Polymarket.", {"sport": "nba"}, {"sport": "NBA", "signal_count": 5, "signals": []})
def signal_sport(sport):
    sport = sport.lower()
    if sport == "politics":
        return signal_politics()
    if sport == "trending":
        return signal_trending()
    if sport == "arb":
        return signal_arb()

    sport_key = SPORT_KEYS.get(sport)
    if not sport_key:
        return jsonify({"error": f"Sport '{sport}' not supported.", "available": ["nba", "nfl", "mma", "boxing", "politics", "trending", "arb"]}), 404

    games = get_vegas_odds(sport_key)
    poly_markets = get_polymarket_markets(sport)

    if not games:
        return jsonify({"sport": sport.upper(), "signal_count": 0, "note": "No upcoming games found — season may be inactive", "signals": []})

    signals = []
    for game in games[:8]:
        home = game.get("home_team", "Home")
        away = game.get("away_team", "Away")
        commence = game.get("commence_time", "")
        bookmakers = game.get("bookmakers", [])

        home_odds_list, away_odds_list = [], []
        for bk in bookmakers:
            for market in bk.get("markets", []):
                if market.get("key") == "h2h":
                    for outcome in market.get("outcomes", []):
                        if outcome["name"] == home:
                            home_odds_list.append(outcome["price"])
                        elif outcome["name"] == away:
                            away_odds_list.append(outcome["price"])

        if not home_odds_list or not away_odds_list:
            continue

        avg_home_odds = round(sum(home_odds_list) / len(home_odds_list), 3)
        avg_away_odds = round(sum(away_odds_list) / len(away_odds_list), 3)
        vegas_home_prob = decimal_to_implied(avg_home_odds)
        vegas_away_prob = decimal_to_implied(avg_away_odds)
        vig = round((vegas_home_prob + vegas_away_prob) - 100, 2)

        poly_home_prob, poly_question = None, None
        for pm in poly_markets:
            q = pm.get("question", "").lower()
            if home.lower().split()[-1] in q or away.lower().split()[-1] in q:
                poly_home_prob = parse_polymarket_prob(pm)
                poly_question = pm.get("question")
                break

        arb = arb_score(vegas_home_prob, poly_home_prob) if poly_home_prob else None
        edge = edge_signal(vegas_home_prob, poly_home_prob, home) if poly_home_prob else None
        edge_gap = abs(vegas_home_prob - vegas_away_prob)
        favorite = home if vegas_home_prob > vegas_away_prob else away

        if edge_gap > 30:
            recommendation, rec_note = "STRONG FAVORITE", f"{favorite} is heavily favored across all books"
        elif edge_gap > 15:
            recommendation, rec_note = "MODERATE EDGE", f"{favorite} has a meaningful edge — monitor line movement"
        else:
            recommendation, rec_note = "TOSS UP", "Close match — sharp bettors look elsewhere"

        signal = {
            "event": f"{away} @ {home}",
            "commence_time": commence,
            "favorite": favorite,
            "recommendation": recommendation,
            "rec_note": rec_note,
            "vegas": {
                "home_win_prob": vegas_home_prob,
                "away_win_prob": vegas_away_prob,
                "avg_home_odds": avg_home_odds,
                "avg_away_odds": avg_away_odds,
                "vig_pct": vig,
                "bookmakers_sampled": len(bookmakers)
            }
        }

        if poly_home_prob:
            signal["polymarket"] = {"question": poly_question, "yes_probability": poly_home_prob}
            signal["cross_platform_analysis"] = {"arb_score": arb, "edge_signal": edge}
        else:
            signal["polymarket"] = "No matching Polymarket market found for this event"

        signals.append(signal)

    return jsonify({
        "sport": sport.upper(),
        "signal_count": len(signals),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data_sources": ["The Odds API (40+ sportsbooks)", "Polymarket Gamma API"],
        "signals": signals
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
