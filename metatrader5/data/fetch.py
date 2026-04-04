import pandas as pd
import metaTrader5 as mt5
from datetime import datetime

class DataFetcher:
    def __init__(self, mt5_connection):
        self.mt5 = mt5_connection

    
    def get_candles(self, symbol="XAUUSD", timeframe=mt5.TIMEFRAME_M1, count=400):
        if not self.mt5.connected:
            print("Not connected to MetaTrader 5")
            return None

        mt5.symbol_select(symbol, True)
    
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        if rates is None:
            print("Failed to get candles, error code =", self.mt.last_error())
            return None

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        return df



    def get_candles_range(self, symbol="XAUUSD", timeframe=mt5.TIMEFRAME_M1, start_datetime, end_datetime):
        if not self.mt.connected:
            print("Not connected to MetaTrader 5")
            return None

        mt5.symbol_select(symbol, True)

        rates = mt5.copy_rates_range(symbol, timeframe, start_datetime, end_datetime)
        if rates is None:
            print("Failed to get candles, error code =", self.mt5.last_error())
            return None

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        return df