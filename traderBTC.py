import sys, os
import napilib as na
from staircaseMain import GridTrader
sys.path.append(os.path.expanduser('~/docus'))
import secret0 # type: ignore

api_key = secret0.apiKeyProf
secret_key = secret0.apiSecretProf
grid_size = 220
buy_size = 0.001
initial_price = 13.7
symbol = "BTCUSDT"

targetDB = na.db(secret=secret0.NotionStaticSecret, id=secret0.OrderDBID)
grid_trader = GridTrader(api_key, secret_key, targetDB, grid_size, buy_size, initial_price, symbol, testnet=False, session='simu1')
grid_trader.run()
