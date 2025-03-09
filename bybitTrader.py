import requests
import time
import hashlib
import hmac
import json
import uuid
import urllib.parse
import os
import datetime
from pybit.unified_trading import WebSocket
import pytz

class BybitTrader:
    class bbSocket:
        def __init__(self, parent, testnet=True):
            self.parent = parent
            self.base_url = None
            self.ws = None
            self.testnet = testnet
            self.running = False
            self.thread = None
            self.channel = None
            self.symbol = None
            self.streamType = None
            self.target = None
            self.callback = None
            self.base_dir = parent.base_dir
            self.data_folder = 'records'
            self.metrics_file = 'metrics.json'
            self.set_testnet(testnet)
            
        def set_testnet(self, isTest):
            self.testnet = isTest
            self.base_url = "stream-testnet.bybit.com" if isTest else "stream.bybit.com"
                
        def handle_message(self, message):
            """Handle incoming WebSocket messages."""
            self.callback(message)
            data = message
            for entries in data['data']:
                self.append_data_to_file([entries])
            # self.append_data_to_file([data['data']])

        def append_data_to_file(self, data):
            filepath = os.path.join(self.base_dir, f'{self.target}_records.json')
            try:
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    with open(filepath, 'r+') as file:  # Open the file in read/write mode
                        file.seek(0, os.SEEK_END)       # Move the cursor to the end of the file
                        file.seek(file.tell() - 1, os.SEEK_SET)  # Move back one character from the end (over the last ']')
                        if file.tell() > 1:
                            file.write(',')             # If not the first element, write a comma
                        file.write(json.dumps(data)[1:-1])  # Write the new data slicing off the square brackets
                        file.write(']')                # Close the array with a bracket
                else:
                    with open(filepath, 'w') as file:  # File is empty or does not exist, create and write as new JSON array
                        json.dump(data, file)        # Dump data as a JSON array
            except IOError as e:
                print(f"Error writing to file {filepath}: {e}")

        def switchTarget(self, channel, symbol, streamType):
            self.close()
            self.channel = channel
            self.symbol = symbol
            self.streamType = streamType
            self.target = f'{symbol}_{channel}_{streamType}'
            self.ws = WebSocket(
                api_key=self.parent.api_key,
                api_secret=self.parent.secret_key,
                testnet=self.testnet,
                channel_type=channel
            )
            self.running = True

        def close(self):
            """Close the WebSocket connection and thread cleanly."""
            if self.ws and self.ws.ws:
                self.ws.ws.close()
                self.running = False

        def subscribe_to_trades(self, channel, symbol, callback=None):
            self.callback = callback
            self.switchTarget(channel, symbol, 'trade')
            self.ws.trade_stream(symbol, callback=self.handle_message)
            
        def subscribe_to_order_updates(self, symbol, callback=None):
            self.switchTarget("private", symbol, 'update')
            self.ws.order_stream(callback=callback)

        def parseTrade(self,message):
            for data in message['data']:
                side = data.get('S', 'N/A')  # S is for side, could be 'Buy' or 'Sell'
                qty = data.get('v', 'N/A')  # v is for volume/quantity of the trade
                price = data.get('p', 'N/A')  # p is for price at which the trade occurred
                timestamp = data.get('T', None)  # T is for timestamp in milliseconds

                # Convert timestamp to readable time format
                if timestamp:
                    time_format = datetime.datetime.fromtimestamp(timestamp / 1000.0).strftime('%H:%M:%S.%f')[:-3]
                else:
                    time_format = 'N/A'

                # Printing the details in a formatted single line for clear visibility
                # Aligning fields using string formatting
                print(f"Price: {price:>8} | Side: {side:<4} | Qty: {qty:>9} | Time: {time_format}")


        def retrieve_recent_data(self, target=None, seconds=3600):
            """Retrieve records based on the timestamp for the last n seconds."""
            if not target:
                target = self.target
            current_time = time.time() * 1000  # Current time in milliseconds
            threshold_time = current_time - (seconds * 1000)
            file_path = os.path.join(self.base_dir, f'{target}_records.json')
            recent_data = []
            try:
                with open(file_path, 'r') as file:
                    data = json.load(file)
                    recent_data = [item for item in data if item['T'] >= threshold_time]
                return recent_data
            except Exception as e:
                return []

        def calculate_moving_averages(self, target=None, seconds=3600):
            """Calculate moving average based on time."""
            data = self.retrieve_recent_data(target, seconds)
            if not data:
                return None
            total = sum(float(item['p']) for item in data)
            count = len(data)
            return total / count if count > 0 else None

    def __init__(self, api_key, secret_key,base_dir = './', testnet=True):
        self.api_key = api_key
        self.secret_key = secret_key
        self.url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        self.orders = {}
        self.order_index = 1
        self.base_dir = base_dir
        self.websocket = self.bbSocket(self, testnet)

    def http_request(self, endpoint, method, params="", info="", verbose=True):
        timestamp = str(int(time.time() * 1000))
        params_encoded = urllib.parse.urlencode(params) if method == "GET" else json.dumps(params)
        signature = self.generate_signature(params_encoded, timestamp)
        headers = {
            'X-BAPI-API-KEY': self.api_key,
            'X-BAPI-SIGN': signature,
            'X-BAPI-TIMESTAMP': timestamp,
            'X-BAPI-RECV-WINDOW': '5000',
            'Content-Type': 'application/json'
        }
        url = f"{self.url}{endpoint}"
        if method == "GET":
            url += f"?{params_encoded}"
            response = requests.get(url, headers=headers)
        else:
            response = requests.post(url, headers=headers, data=params_encoded)
        return response.json()

    def generate_signature(self, payload, timestamp):
        query_string = timestamp + self.api_key + '5000' + payload
        signature = hmac.new(bytes(self.secret_key, "utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()
        return signature
    def get_index_price(self, symbol, category="spot"):
        endpoint = "/v5/market/tickers"
        params = {
            "symbol": symbol,
            "category": category
        }
        response = self.http_request(endpoint, "GET", params=params, info="Get Index Price")
        if response.get('retCode') == 0:
            index_price = response['result']['list'][0]['lastPrice']
            return float(index_price)
        else:
            print(f"Error fetching index price: {response['retMsg']}")
            raise Exception(f"Error fetching index price: {response['retMsg']}") 
        
    def get_order_history(self, symbol, category="spot", **kwargs):
        endpoint = "/v5/order/history"
        params = {
            "category": category,
            "symbol": symbol,
            "limit": 40
        }
        
        for key, value in kwargs.items():
            params[key] = value

        response = self.http_request(endpoint, "GET", params=params, info="Get Order History")
        if response.get('retCode') == 0:
            res = response['result']['list']
            print(f"Returned Order history")
            return res
        else:
            print(f"Error fetching order history: {response['retMsg']}")
            return []

    def get_trade_history(self, symbol, start_time, end_time, category="spot"):
        """
        Function to get the trade history for a given symbol within a specific time range.

        Args:
        - symbol (str): The trading pair symbol (e.g., 'BTCUSDT').
        - start_time (int): The start time in milliseconds.
        - end_time (int): The end time in milliseconds.
        - category (str): The product type, default is 'spot'. Other options: 'linear', 'inverse', 'option'.

        Returns:
        - List[dict]: A list of trade history records.
        """
        endpoint = "/v5/execution/list"
        params = {
            "category": category,
            "symbol": symbol,
            "startTime": start_time,
            "endTime": end_time,
            "limit": 50  # Adjust the limit according to your needs
        }
        response = self.http_request(endpoint, "GET", params=params, info="Get Trade History")
        if response.get('retCode') == 0:
            trades = response['result']['list']
            print(f"Returned trade history for {symbol} from {start_time} to {end_time}:")
            return trades
        else:
            print(f"Error fetching trade history: {response['retMsg']}")
            return []

    def create_order(self, category, symbol, side, order_type, qty, price=None, time_in_force="GTC", reduce_only=False, verbose=True):
        order_link_id = uuid.uuid4().hex
        order_payload = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(qty),
            "orderLinkId": order_link_id,
            "timeInForce": time_in_force,
            "reduceOnly": reduce_only
        }
        if price:
            order_payload["price"] = str(price)
        response = self.http_request("/v5/order/create", "POST", order_payload, "Create Order")
        if response['retCode'] == 0:
            order_name = f"{symbol}-{self.order_index:04d}"
            self.order_index += 1
            order_id = response['result']['orderId']
            self.orders[order_link_id] = {
                "name": order_name,
                "details": order_payload,
                "status": 'open',
                "order_id": order_id
            }
            if verbose:
                print(f"Order {order_name} created: Type={order_type}, Price={price if price else 'Market'}, Category={category}")
            return order_id
        else:
            if verbose:
                print(f"Failed to create order: {response['retMsg']}{response}")
                print(response)
            if response['retCode'] == 170193:
                raise Exception('price too high')
            elif response['retCode'] == 170194:
                raise Exception('price too low')
            elif response['retCode'] == 170131:
                raise Exception('insufficient balance')
            raise Exception(response['retCode'])

    def cancel_order(self, order_name=None,id=None,category="spot",symbol=None,verbose=True):
        if (not order_name) and (not id):
            if verbose:
                print("No order name or id provided")
            return {"error": "No order name or id provided"}
        if id:
            payload = {
                "category": category,
                "orderId": id
            }
            response = self.http_request("/v5/order/cancel", "POST", payload, "Cancel Order")
            if response['retCode'] == 0:
                if verbose:
                    print(f"Order {id} cancelled successfully.")
            else:
                if verbose:
                    print(f"Failed to cancel order {id}: {response['retMsg']}")
                raise Exception(response['retMsg'])
            return response
        
        for link_id, order_info in self.orders.items():
            if order_info["name"] == order_name:
                payload = {
                    "category": order_info['details']['category'],
                    "symbol": order_info['details']['symbol'],
                    "orderLinkId": link_id
                }
                response = self.http_request("/v5/order/cancel", "POST", payload, "Cancel Order")
                if response['retCode'] == 0:
                    self.orders[link_id]['status'] = 'cancelled'
                    if verbose:
                        print(f"Order {order_name} cancelled successfully.")
                else:
                    if verbose:
                        print(f"Failed to cancel order {order_name}: {response['retMsg']}")
                return response
        if verbose:
            print(f"Order {order_name} not found.")
        return {"error": "Order not found"}

    def get_open_orders(self, category, verbose=True):
        if category not in ['spot', 'linear', 'inverse', 'option']:
            if verbose:
                print("Invalid category specified")
            return {"error": "Invalid category specified"}
        payload = {
            "category": category,
            "openOnly": 0,
            "limit": 50
        }
        response = self.http_request("/v5/order/realtime", "GET", payload, "Get Open Orders")
        if response.get("retCode") == 0:
            open_orders = response.get("result", {}).get("list", [])
            existing_link_ids = set(self.orders.keys())
            for order in open_orders:
                order_link_id = order.get("orderLinkId")
                if order_link_id in self.orders:
                    self.orders[order_link_id]['details'].update(order)
                else:
                    order_name = f"{order['symbol']}-{self.order_index:04d}"
                    self.order_index += 1
                    order.update({'category':category})
                    self.orders[order_link_id] = {'name': order_name, 'details': order, 'status': 'open'}
            for link_id in existing_link_ids - set([tempO.get('orderLinkId') for tempO in open_orders]):
                if self.orders[link_id]['status'] == 'open' and self.orders[link_id]['details']['category'] == category:
                    self.orders[link_id]['status'] = 'fulfilled'
        return response

    def show_orders(self, category=None, verbose=True):
        """Show orders filtered by category."""
        if category:
            self.get_open_orders(category)  # Update the list of orders for the specified category
        if verbose:
            for order in self.orders.values():
                if category is None or order['details']['category'] == category:
                    details = order['details']
                    print(f"{order['name']}: Symbol={details['symbol']}, Qty={details['qty']}, Price={details.get('price', 'N/A')}, Status={order['status']}")

# This script is now set up to conditionally print information based on the `verbose` parameter, focusing only on the critical details needed for clarity and brevity.
# from bybitTrader import BybitTrader
# import secret,json
# from time import sleep
# trader = BybitTrader(secret.api_key,secret.secret_key)
# socket = trader.websocket
# print(json.dumps(socket.retrieve_recent_data('ETHUSDT_spot_trade',seconds=3000)))