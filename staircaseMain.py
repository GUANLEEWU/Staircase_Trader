import json
import csv
import numpy as np
from bybitTrader import BybitTrader
import time
import os
import napilib as na
import signal
import sys
import random, socket
import logging, threading
import requests as requests
from logging.handlers import RotatingFileHandler
import traceback
import pytz
from datetime import datetime

def setup_logger(log_file_name):
    tz_Taiwan = pytz.timezone('Asia/Taipei')
    
    def time_in_taiwan(*args):
        return datetime.now(tz_Taiwan).timetuple()

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # File handler
    file_handler = RotatingFileHandler(log_file_name, maxBytes=20*1024*1024, backupCount=2)
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # Adjust time converter
    logging.Formatter.converter = time_in_taiwan

    return logger

def get_latest_logs(file_name, num_lines=30):
    try:
        with open(file_name, 'r') as f:
            lines = f.readlines()
            return lines[-num_lines:]  # Get the last num_lines entries
    except Exception as e:
        logging.error(f"Error reading log file {file_name}: {e}")
        return []

class StateManager:
    def __init__(self):
        pass

    def load_state(self, key, default_value):
        return self.load_json_file(key, default_value)

    def save_state(self, key, data):
        self.save_json_file(key, data)

    def load_json_file(self, file_name, default_value):
        if os.path.exists(file_name):
            try:
                with open(file_name, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
                    else:
                        logging.warning(f"Data in {file_name} is not a valid dictionary. Loading default value.")
                        return default_value
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding JSON from {file_name}: {e}. Loading default value.")
                return default_value
        else:
            return default_value
    def save_json_file(self, file_name, data):
        try:
            with open(file_name, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logging.critical(f"Failed to save file {file_name}: {e}")

class GridTrader:
    def __init__(self, api_key, secret_key,naDB,grid_size, buy_size, initial_price, symbol, polling_interval=5, testnet=True,session='not set'):
        setup_logger(f'trader_log_{symbol}.log')
        self.trader = BybitTrader(api_key, secret_key, testnet=testnet)
        self.db = naDB
        self.logDB = na.db(naDB.secret,'36458b82ef9740b68eb401b732136476')
        self.ActionDB = na.db(naDB.secret,'18b3e4c0c19746e8b114702f6e310846')
        self.OpenOrderDB = na.db(naDB.secret,'06fd76415bf4441f81aeaeb1f8fd12b2')
        self.grid_size = grid_size
        self.buy_size = buy_size
        self.initial_price = initial_price
        self.symbol = symbol
        self.lock = threading.Lock()

        # Initialize state manager and load states
        self.state_manager = StateManager()
        self.buy_orders = self.state_manager.load_state(f'buy_orders_{self.symbol}.json', {})
        self.sell_orders = self.state_manager.load_state(f'sell_orders_{self.symbol}.json', {})
        self.order_tracking = self.state_manager.load_state(f'order_tracking_{self.symbol}.json', {})
        portfolio_data = self.state_manager.load_state(f'portfolio_{self.symbol}.json', {'cumulative_income': 0.0, 'balance': 0.0, 'crypto_holdings': 0.0})
        self.openOrders = self.state_manager.load_state(f'open_orders_{self.symbol}.json', {})
        self.variables = self.state_manager.load_state(f'variables_{self.symbol}.json', {'last_checked_time': None})
        
        self.cumulative_income = portfolio_data['cumulative_income']
        self.balance = portfolio_data['balance']
        self.crypto_holdings = portfolio_data['crypto_holdings']
        self.portfolio_value = self.get_portfolio_value()

        self.csv_file = f'trades_record_{self.symbol}.csv'
        self.batch_size = 5  # How often to batch save
        self.pending_updates = []
        self.polling_interval = polling_interval
        self.session = session
        
        # Initialize CSV if it doesn't exist
        if not os.path.exists(self.csv_file):
            with open(self.csv_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Time','Buy Price', 'Sell Price', 'Quantity', 'Pair Profit', 'Cumulative Income', 'Portfolio Value', 'Balance', 'Crypto Holdings', 'session'])

        # Signal handling for graceful shutdown
        signal.signal(signal.SIGINT, self.graceful_shutdown)
        signal.signal(signal.SIGTERM, self.graceful_shutdown)



    def place_buy_order(self, price):
        attempt = 0
        max_retries = 60
        steady_wait_time = 0.5
        order_id = None
        temp_price = round(price,2)
        while True:
            try:
                if self.tokey(temp_price) not in self.buy_orders:
                    order_id = self.trader.create_order("spot", self.symbol, "Buy", "limit", self.buy_size, price=temp_price)
                    self.openOrders.update({order_id:'buy'})
                    break
                else:
                    logging.info(f"{temp_price} buy order already exists")
                    return None
            except Exception as e:
                if 'price too high' in str(e):
                    logging.error(f'price too high, aborting')
                    return None
                if 'insufficient balance' in str(e):
                    logging.info(f'insufficient balance, wait 5 secs and retry')
                    time.sleep(5)
                    return None
                if attempt < max_retries:
                    wait_time = steady_wait_time * (attempt + 1)
                else:
                    wait_time = steady_wait_time * max_retries
                logging.error(f"Error placing buy order at {temp_price}: {e}. Retrying in {wait_time} seconds...")
                logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
                self.upload_logs('place_buy_failure')
                time.sleep(wait_time)
                attempt += 1
        try:
            self.buy_orders[self.tokey(temp_price)] = order_id
            logging.info(f"Placed buy order at {temp_price}, Order ID: {order_id}")
        except Exception as e:
            logging.error(f"Exception occurred while placing buy order at {temp_price}: {e}")
            logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
            self.upload_logs('buy_orders')
        return order_id
            
            

    def place_sell_order(self, sell_price, qty):
        attempt = 0
        max_retries = 60
        steady_wait_time = 0.5
        temp_price = round(sell_price,2)
        sell_order_id = None
        while True:
            try:
                sell_order_id = self.trader.create_order("spot", self.symbol, "Sell", "limit", qty, price=temp_price)
                logging.info(f"Placed sell order at {temp_price}, Order ID: {sell_order_id}")
                break
            except Exception as e:
                if 'price too low' in str(e):
                    time.sleep(0.05)
                    cur_price = self.get_index_price()
                    temp_price = round(cur_price+0.01,2)
                    logging.error(f'price too low, set to {temp_price}')
                    continue
                if attempt < max_retries:
                    wait_time = steady_wait_time * (attempt + 1)
                else:
                    wait_time = steady_wait_time * max_retries
                logging.error(f"Error placing sell order at {temp_price}: {e}. Retrying in {wait_time} seconds...")
                logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
                self.upload_logs('sell_order_failure')
                time.sleep(wait_time)
                attempt += 1
        try:
            self.sell_orders[self.tokey(temp_price)] = sell_order_id
            temp = na.row()
            temp.set('Name', "open", 'title')
            temp.set('side', 'Sell', 'select')
            temp.set('session', self.session, 'select')
            temp.set('price', temp_price, 'number')
            temp.set('qty', qty, 'number')
            temp.set('status','open','select')
            temp.set('symbol',self.symbol,'select')
            notionRowID = self.OpenOrderDB.add(temp)
            self.openOrders.update({sell_order_id:notionRowID})
            logging.debug(f'Added sell order to Open Order DB: {notionRowID}')
        except Exception as e:
            logging.error(f"Exception occurred while placing sell order at {temp_price}: {e}")
            logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
            if sell_order_id not in self.openOrders:
                self.openOrders.update({sell_order_id:None})
            self.upload_logs('sell_orders')
        return sell_order_id

    def update_portfolio(self, price, qty, fee, side):
        if side == 'Buy':
            self.balance -= (price * qty) + fee
            self.crypto_holdings += qty
        elif side == 'Sell':
            self.balance += (price * qty) - fee
            self.crypto_holdings -= qty
        self.get_portfolio_value()

    def get_portfolio_value(self):
        current_eth_price = self.trader.get_index_price(self.symbol)
        portfolio_value = self.balance + (self.crypto_holdings * current_eth_price)
        self.portfolio_value = portfolio_value
        return portfolio_value

    def record_trade(self, buy_price, sell_price, qty, pair_profit):
        self.cumulative_income += pair_profit
        # portfolio_value = self.get_portfolio_value()
        cur_time = datetime.now(pytz.timezone('Asia/Taipei')).strftime('%Y-%m-%d %H:%M:%S')
        self.pending_updates.append([cur_time,buy_price, sell_price, qty, pair_profit, self.cumulative_income, self.portfolio_value, self.balance, self.crypto_holdings, self.session])
        if len(self.pending_updates) >= self.batch_size:
            self.flush_updates()

    def flush_updates(self):
        try:
            with open(self.csv_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerows(self.pending_updates)  # Write all pending updates at once
            self.pending_updates.clear()
            logging.info("Flushed pending updates to CSV.")
        except Exception as e:
            logging.error(f"Error flushing updates: {e}")
            
    def process_filled_buy_order(self, order, filled_price, qty, fee, order_time):
        try:
            fee_in_usdt = fee * filled_price
            contribution = -(filled_price * qty) - fee_in_usdt
            logging.info(f"popped {self.openOrders.pop(order['orderId'],None)} from openOrders")
            sell_order_id = self.place_sell_order(round(float(order['price']) + self.grid_size, 2), qty)
            
            if self.variables['last_checked_time'] is None or order_time > self.variables['last_checked_time']:
                self.variables['last_checked_time'] = order_time

            self.order_tracking[sell_order_id] = {
                'filled_price': filled_price,
                'buy-price': round(float(order['price']), 2),
                'qty': qty,
                'fee': fee_in_usdt,
                'contribution': contribution
            }

            portfolio_change = -self.portfolio_value
            self.update_portfolio(filled_price, qty, fee_in_usdt, 'Buy')
            portfolio_change += self.portfolio_value

            logging.info(f"Placed corresponding sell order with ID: {sell_order_id}")

            temp = na.row()
            temp.set('Name', "filled", 'title')
            temp.set('side', order['side'], 'select')
            temp.set('contribution', contribution, 'number')
            temp.set('session', self.session, 'select')
            temp.set('price', round(filled_price, 2), 'number')
            temp.set('qty', qty, 'number')
            temp.set('crypto_holding', self.crypto_holdings, 'number')
            temp.set('portfolio_value', self.portfolio_value, 'number')
            temp.set('symbol', self.symbol, 'select')
            temp.set('portfolio_change', portfolio_change, 'number')

            try:
                self.db.add(temp)
                logging.debug(f"Logged filled buy order to database")
            except Exception as e:
                logging.error(f"Failed to log filled buy order to database: {e}")
                logging.error(f"Traceback: {traceback.format_exc()}")

        except Exception as e:
            logging.error(f"Error processing filled buy order: {e}")
            logging.error(f"Traceback: {traceback.format_exc()}")
    
    def process_filled_sell_order(self, order, filled_price, qty, fee, order_time):
        try:
            contribution = filled_price * qty - fee
            buy_order_details = self.order_tracking.pop(order.get('orderId'), None)
            
            if buy_order_details:
                if self.variables['last_checked_time'] is None or order_time > self.variables['last_checked_time']:
                    self.variables['last_checked_time'] = order_time
                self.buy_orders.pop(self.tokey(buy_order_details["buy-price"]), None)
                pair_profit = contribution + buy_order_details['contribution']
                portfolio_change = -self.portfolio_value
                self.update_portfolio(filled_price, qty, fee, 'Sell')
                portfolio_change += self.portfolio_value

                self.record_trade(
                    buy_order_details['filled_price'],
                    filled_price,
                    qty,
                    pair_profit
                )
                
                logging.info(f"Processed filled sell order - Pair Profit: {pair_profit}")

                temp = na.row()
                temp.set('Name', "filled", 'title')
                temp.set('side', order['side'], 'select')
                temp.set('contribution', contribution, 'number')
                temp.set('pair_profit', pair_profit, 'number')
                temp.set('price', round(filled_price, 2), 'number')
                temp.set('session', self.session, 'select')
                temp.set('pair', f"buy price: {buy_order_details['buy-price']}", 'rich_text')
                temp.set('qty', qty, 'number')
                temp.set('crypto_holding', self.crypto_holdings, 'number')
                temp.set('portfolio_value', self.portfolio_value, 'number')
                temp.set('symbol', self.symbol, 'select')
                temp.set('portfolio_change', portfolio_change, 'number')

                try:
                    self.db.add(temp)
                    logging.debug(f"Logged filled sell order to database")
                except Exception as e:
                    logging.error(f"Failed to log filled sell order to database: {e}")
                    logging.error(f"Traceback: {traceback.format_exc()}")

                try:
                    openRowID = self.openOrders.pop(order.get('orderId'))
                    openSellOrder = na.row(id=openRowID, secret=self.OpenOrderDB.secret)
                    openSellOrder.data_d['properties'] = {}
                    openSellOrder.set('status', 'filled', 'select')
                    openSellOrder.update()
                    logging.info(f"Marked sell order {openRowID} as filled")
                except KeyError as e:
                    logging.warning(f"Order ID {order.get('orderId')} not found in openOrders: {e}")
                except Exception as e:
                    logging.error(f"Failed to mark sell order as closed: {e}")
                    logging.error(f"Traceback: {traceback.format_exc()}")

            else:
                logging.warning('Caught sell order with no matching buy pair.')

        except Exception as e:
            logging.error(f"Error processing filled sell order: {e}")
            logging.error(f"Traceback: {traceback.format_exc()}")

    def handle_filled_order_callback(self, message):
        with self.lock:
            if not message:
                logging.error('no message')
                return
            logging.debug(f'received order callback: {json.dumps(message,indent=2)}')
            
            for order in message['data']:
                try:
                    if order.get('symbol') != self.symbol:
                        continue
                    order_status = order.get('orderStatus')
                    order_id = order.get('orderId')
                    logging.info(f"Processing order with ID: {order_id}, side: {order['side']}, Status: {order_status}")
                    
                    order_time = int(order['updatedTime'])
                    if order_status == 'Cancelled':
                        try:
                            result = self.openOrders.pop(order_id, None)
                            logging.debug(f"Order cancelled - popped openOrders: {result} with key {order_id}")
                            if order['side'] == 'Buy':
                                result = self.buy_orders.pop(self.tokey(float(order['price'])), None)
                                logging.debug(f"Order cancelled - popped buy_orders: {result} with key {self.tokey(float(order['price']))}")
                            else:
                                result = self.sell_orders.pop(self.tokey(float(order['price'])), None)
                                logging.debug(f"Order cancelled - popped sell_orders: {result} with key {self.tokey(float(order['price']))}")
                                result = self.order_tracking.pop(order_id, None)
                                logging.debug(f"Order cancelled - popped order_tracking: {result} with key {order_id}")
                            if self.variables['last_checked_time'] is None or order_time > self.variables['last_checked_time']:
                                self.variables['last_checked_time'] = order_time
                            # Attempt to checkpoint state
                            # try:
                            #     self.checkpoint_state()
                            # except Exception as e:
                            #     logging.error(f"Failed to checkpoint state: {e}")
                            #     logging.error(f"Traceback: {traceback.format_exc()}")

                        except Exception as e:
                            logging.error(f"Failed to process cancelled order {order_id}: {e}")
                            logging.error(f"Traceback: {traceback.format_exc()}")

                        continue

                    elif order_status == 'Filled':
                        try:
                            filled_price = order.get('avgPrice', '')
                            qty = float(order.get('cumExecQty', order['qty']))
                            if not filled_price:
                                value = order.get('cumExecValue', '')
                                if value:
                                    filled_price = float(value) / float(qty)
                                else:
                                    filled_price = order['price']
                            filled_price = float(filled_price)
                            fee = float(order['cumExecFee'])
                            logging.info(f"Received Order Filled Price: {filled_price}, Qty: {qty}")

                            if order['side'] == 'Buy':
                                self.process_filled_buy_order(order, filled_price, qty, fee, order_time)
                            elif order['side'] == 'Sell':
                                self.process_filled_sell_order(order, filled_price, qty, fee, order_time)

                        except Exception as e:
                            logging.error(f"Error processing filled order {order_id}: {e}")
                            logging.error(f"Traceback: {traceback.format_exc()}")

                except Exception as e:
                    logging.error(f"Unhandled error in filled order callback: {e}")
                    logging.error(f"Traceback: {traceback.format_exc()}")
                    self.upload_logs('Unhandled error in filled order callback')

    def checkpoint_state(self):
        try:
            self.state_manager.save_state(f'variables_{self.symbol}.json', self.variables)
            self.state_manager.save_state(f'buy_orders_{self.symbol}.json', self.buy_orders)
            self.state_manager.save_state(f'sell_orders_{self.symbol}.json', self.sell_orders)
            self.state_manager.save_state(f'order_tracking_{self.symbol}.json', self.order_tracking)
            self.state_manager.save_state(f'open_orders_{self.symbol}.json', self.openOrders)
            self.state_manager.save_state(f'portfolio_{self.symbol}.json', {
                'cumulative_income': self.cumulative_income,
                'balance': self.balance,
                'crypto_holdings': self.crypto_holdings
            })
            logging.info("State checkpointed successfully.")
        except Exception as e:
            logging.error(f"Failed to checkpoint state: {e}")
            logging.error(f'Error occurred on line {traceback.format_exc().splitlines()[-2]}')
            self.upload_logs('checkpoint_state')
            
    def handle_missed_orders(self):
        last_time = self.variables['last_checked_time']
        logging.info("Processing missed order updates")
        order_ids_to_process = list(self.openOrders.keys())
        missed_order_updates = []

        for order_id in order_ids_to_process:
            order_history = self.trader.get_order_history(symbol=self.symbol, orderId=order_id)
            
            if not order_history:
                continue
            
            for order in order_history:
                if int(order['updatedTime']) > last_time:
                    missed_order_updates.append(order)

        # Sort the collected updates by their update time
        missed_order_updates.sort(key=lambda x: int(x['updatedTime']))

        # Process each update by calling the handle_filled_order_callback function
        for order_update in missed_order_updates:
            self.handle_filled_order_callback({'data': [order_update]})
        logging.info('Done processing missed order updates')
            
            
    def calculate_next_buy_level(self, current_price):
        n = np.floor((current_price - self.initial_price) / self.grid_size)
        next_level = self.initial_price + n * self.grid_size
        return round(next_level, 2)

    def get_param(self):
        try:
            self.ActionDB.grab()
            for x in self.ActionDB.lrows:
                if x.get('state') == 'adjusting':
                    if x.get('Name') == f'Buy Size {self.symbol}':
                        value = x.get('value')
                        if (self.symbol == 'BTCUSDT' and value > 0.005) or (self.symbol == 'ETHUSDT' and value > 0.1):
                            logging.info('too large')
                        else:
                            self.buy_size = value
                            logging.info(f"Buy size changed to {str(self.buy_size)}")
                        try:
                            x.data_d['properties'] = {}
                            x.set('state','is set','select')
                            x.set('note',f"changed to {str(self.buy_size)}",'rich_text')
                            x.secret = self.db.secret
                            x.update()
                            logging.info("param modification info updated")
                        except Exception as e:
                            logging.error(f"Failed to update modification info: {e}")
                            logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
                            self.upload_logs('update_modification_info')
        except Exception as e:
            logging.error(f"Failed to get param: {e}")
            logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
            self.upload_logs('get_param')
    def subscribe_to_websocket(self):
        attempt = 0
        max_retries = 60
        steady_wait_time = 0.5  # Number of seconds to wait between retries

        while True:
            try:
                if self.variables['last_checked_time']:
                    self.temp_update_buffer = []

                    self.trader.websocket.subscribe_to_order_updates(self.symbol, self.temp_update_buffer.append)
                    logging.info(f"Successfully subscribed to WebSocket updates for {self.symbol}")
                    
                    self.handle_missed_orders()
                    for message in self.temp_update_buffer:
                        self.handle_filled_order_callback({'data': [message]})
                    self.temp_update_buffer = []
                    
                self.trader.websocket.subscribe_to_order_updates(self.symbol, self.handle_filled_order_callback)
                break
            except Exception as e:
                if attempt < max_retries:
                    wait_time = steady_wait_time * (attempt + 1)
                else:
                    wait_time = steady_wait_time * max_retries  # Stabilize wait time after max_retries
                logging.error(f"Error during WebSocket subscription: {e}. Retrying in {wait_time} seconds...")
                logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
                time.sleep(wait_time)
                attempt += 1
    def get_index_price(self):
        attempt = 0
        max_retries = 60
        steady_wait_time = 0.5  # Number of seconds to wait between retries

        while True:
            try:
                return self.trader.get_index_price(self.symbol)
            except Exception as e:
                if attempt < max_retries:
                    wait_time = steady_wait_time * (attempt + 1)
                else:
                    wait_time = max_retries * steady_wait_time  # Stabilize wait time after max_retries
                logging.error(f"Error getting index price for {self.symbol}: {e}. Retrying in {wait_time} seconds...")
                logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
                time.sleep(wait_time)
                attempt += 1                
    
    # def get_open_orders(self):
        
    def tokey(self, n):
        if type(n) == str:
            n = float(n)
        return str(f"{round(n, 2):.2f}")
    def run(self):
        count = 0
        self.subscribe_to_websocket()
        while True:
            if not self.trader.websocket.ws.is_connected():
                self.subscribe_to_websocket()
            current_price = self.get_index_price()
            try:
                next_buy_level = self.calculate_next_buy_level(current_price)
                if self.tokey(next_buy_level) not in self.buy_orders:
                    newID = self.place_buy_order(next_buy_level)
                    if newID:
                        logging.debug(f'next buy level:{next_buy_level}, {self.buy_orders}')
                        for level in sorted([float(x) for x in self.buy_orders.keys()]):
                            id = self.buy_orders[self.tokey(level)]
                            if id in self.openOrders:
                                if self.tokey(level) != self.tokey(next_buy_level):
                                    try:
                                        logging.info(f'canceling {id} with level {level}')
                                        self.trader.cancel_order(id=id)
                                        self.buy_orders.pop(self.tokey(level),None)
                                        self.openOrders.pop(id,None)
                                        logging.debug(f"changed: buy order{self.buy_orders.keys()}, open orders{self.openOrders.keys()}")
                                    except Exception as e:
                                        logging.error(f"Failed to cancel order {id}: {e}")
                                        logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
                                        self.upload_logs('order_cancellation_failure')
                                # break
                    else:
                        logging.error(f"Failed to place buy order: {next_buy_level}")
                if count >= 12:
                    self.checkpoint_state()
                    count = 0
                if count % 2 == 0:
                    self.get_param()
                count += 1
                time.sleep(self.polling_interval)
            except Exception as e:
                logging.error(f"Error occurred: {e}")
                logging.error(f"Error occurred on line {traceback.format_exc().splitlines()[-2]}")
                self.graceful_shutdown()

    def upload_logs(self,title='logging'):
        temp = na.row()
        temp.set('Name',title,'title')
        temp.set('detail','\n'.join(get_latest_logs(f'trader_log_{self.symbol}.log',15)),'rich_text')
        self.logDB.add(temp)
    def graceful_shutdown(self, signum=None, frame=None):
        logging.info("Shutting down gracefully...")
        try:
            self.checkpoint_state()
            self.flush_updates()
        except Exception as e:
            logging.error(f"Failed during checkpoint or flush: {e}")
        finally:
            if self.trader.websocket and self.trader.websocket.ws:
                try:
                    self.trader.websocket.close()
                    self.upload_logs('graceful_shutdown')
                    logging.info("WebSocket connection closed.")
                except Exception as e:
                    logging.error(f"Failed to close WebSocket: {e}")
            sys.exit(0)