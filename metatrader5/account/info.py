class AccountInfo:
    def __init__(self,mt5_connection):
        self.mt = mt5_connection

    def get_info(self):
        if not self.mt.connected:
            print("Not connected to MetaTrader 5")
            return None
        info = self.mt.get_account_info()
        if info is None:
            print("Failed to get account info, error code =", self.mt.last_error())
            return None
        return info


    def get_balance(self):
        return self.mt.get_account_info().get("balance")

    def get_equity(self):
        return self.mt.get_account_info().get("equity")

    def get_margin(self):
        return self.mt.get_account_info().get("margin")

    def get_free_margin(self):
        return self.mt.get_account_info().get("free_margin")

    def get_drawdown(self):
        info = self.mt.get_account_info()
        balance = info.get("balance")
        equity = info.get("equity")

        if balance == 0:
            return 0.0

        drawdown = ((balance - equity) / balance) * 100
        return round(drawdown, 2)


    def get_risk_level(self):
        drawdown = self.get_drawdown()
        if drawdown < 5:
            return "Low"
        elif 5 <= drawdown < 20:
            return "Medium"
        else:
            return "High"