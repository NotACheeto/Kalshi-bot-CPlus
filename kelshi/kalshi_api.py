import base64
import time
import requests
import json
import logging
import uuid
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key

logger = logging.getLogger(__name__)

class KalshiClient:
    def __init__(self, key_id, private_key_pem, env_mode="paper_prod"):
        self.key_id = key_id
        if not self.key_id or not private_key_pem:
            raise ValueError("Kalshi API Key ID and Private Key must be provided.")
            
        self.private_key = load_pem_private_key(private_key_pem.encode('utf-8'), password=None)
        
        self.env_mode = env_mode
        self.is_paper = (env_mode == "paper_prod")
        
        if env_mode in ["prod", "paper_prod"]:
            self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        else:
            self.base_url = "https://external-api.demo.kalshi.co/trade-api/v2"
            
        self.session = requests.Session()

        # Local paper trading state
        if self.is_paper:
            self.paper_orders = {}
            self.paper_balance = 10000.0
            self.paper_log_file = "paper_trades.log"
            logger.info("Initializing Local Paper Trading Engine on Production Data")

    def _sign(self, method, path, timestamp):
        from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
        
        msg_string = str(timestamp) + method + path
        msg_bytes = msg_string.encode('utf-8')
        
        if isinstance(self.private_key, rsa.RSAPrivateKey):
            signature = self.private_key.sign(
                msg_bytes,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.MAX_LENGTH
                ),
                hashes.SHA256()
            )
        elif isinstance(self.private_key, ed25519.Ed25519PrivateKey):
            signature = self.private_key.sign(msg_bytes)
        else:
            raise ValueError("Unsupported private key type.")
            
        return base64.b64encode(signature).decode('utf-8')

    def request(self, method, path, params=None, data=None):
        timestamp = int(time.time() * 1000)
        signature = self._sign(method.upper(), "/trade-api/v2" + path, timestamp)
        
        headers = {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": str(timestamp),
            "KALSHI-ACCESS-SIGNATURE": signature,
            "Content-Type": "application/json"
        }
        
        url = self.base_url + path
        
        try:
            response = self.session.request(method, url, headers=headers, params=params, json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP Error: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Request Error: {e}")
            raise

    def get_balance(self):
        if self.is_paper:
            return {"balance": self.paper_balance * 100} # Kalshi returns balance in cents
        return self.request("GET", "/portfolio/balance")

    def get_markets(self, limit=100, status="open"):
        return self.request("GET", "/markets", params={"limit": limit, "status": status})

    def get_order_book(self, ticker):
        return self.request("GET", f"/markets/{ticker}/orderbook")

    def place_order(self, ticker, action, count, price, side="yes", expiration_ts=None):
        if self.is_paper:
            order_id = str(uuid.uuid4())
            order = {
                "order_id": order_id,
                "ticker": ticker,
                "action": action,
                "side": side,
                "count": count,
                "price": price,
                "status": "resting"
            }
            self.paper_orders[order_id] = order
            msg = f"PAPER ORDER PLACED: {action} {count} {side} at {price}c for {ticker}"
            logger.info(msg)
            with open(self.paper_log_file, "a") as f:
                f.write(f"{time.ctime()} - {msg}\n")
            return {"order": {"order_id": order_id}}

        # Real execution logic below
        v2_side = "bid" if action == "buy" else "ask"
        if side == "no":
            v2_side = "bid" if action == "sell" else "ask"
            price = 100 - price

        payload = {
            "ticker": ticker,
            "client_order_id": f"bot_{int(time.time()*1000)}",
            "side": v2_side,
            "count": str(count) + ".00",
            "price": f"0.{price:02d}00",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "maker",
            "post_only": False
        }
        logger.info(f"LIVE ORDER PLACED: {action} {count} {side} at {price}c for {ticker}")
        return self.request("POST", "/portfolio/events/orders", data=payload)

    def get_orders(self, ticker=None, status="resting"):
        if self.is_paper:
            orders = list(self.paper_orders.values())
            if ticker:
                orders = [o for o in orders if o["ticker"] == ticker]
            return {"orders": orders}

        params = {"status": status}
        if ticker:
            params["ticker"] = ticker
        return self.request("GET", "/portfolio/orders", params=params)

    def cancel_order(self, order_id):
        if self.is_paper:
            if order_id in self.paper_orders:
                del self.paper_orders[order_id]
            return {"order_id": order_id}

        return self.request("DELETE", f"/portfolio/events/orders/{order_id}")

    def simulate_fills(self, ticker, best_yes_bid, best_yes_ask):
        """ Local matching engine to simulate order execution on live data """
        if not self.is_paper: return
        
        filled_ids = []
        for oid, o in self.paper_orders.items():
            if o['ticker'] != ticker: continue
            
            is_filled = False
            # BUY limit order fills if market sellers hit our bid, or if market drops past our bid
            if o['action'] == 'buy' and o['side'] == 'yes':
                if (best_yes_ask and best_yes_ask <= o['price']) or (best_yes_bid and best_yes_bid < o['price']):
                    is_filled = True
            
            # SELL limit order fills if market buyers hit our ask, or if market rises past our ask
            elif o['action'] == 'sell' and o['side'] == 'yes':
                if (best_yes_bid and best_yes_bid >= o['price']) or (best_yes_ask and best_yes_ask > o['price']):
                    is_filled = True
                    
            if is_filled:
                filled_ids.append(oid)
                # Gross PnL approximation
                if o['action'] == 'buy':
                    self.paper_balance -= (o['price'] / 100.0) * o['count']
                else:
                    self.paper_balance += (o['price'] / 100.0) * o['count']
                
                msg = f"--- PAPER FILL ALERT --- {o['action']} {o['count']} {o['side']} at {o['price']}c on {ticker}. New Balance: ${self.paper_balance:.2f}"
                logger.info(msg)
                with open(self.paper_log_file, "a") as f:
                    f.write(f"{time.ctime()} - {msg}\n")
                    
        for oid in filled_ids:
            del self.paper_orders[oid]
