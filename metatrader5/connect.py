import MetaTrader5 as mt5
from datetime import datetime
import pandas as pd

class MT5Connector:
    def __init__(self, login=None, password=None, server=None):
        self.login = login
        self.password = password
        self.server = server
        self.connected = False

    def connect(self):
        if not mt5.initialize(login=self.login, password=self.password, server=self.server):
            print("initialize() failed, error code =", mt5.last_error())
            return False
        self.connected = True
        return True

    def disconnect(self):
        mt5.shutdown()
        self.connected = False

    def get_account_info(self):
        info = mt5.account_info()
        return info._asdict() if info else None


    def last_error(self):
        return mt5.last_error()