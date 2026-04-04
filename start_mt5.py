from metatrader5 import MT5Connector
from metatrader5.account import AccountInfo

mt = MT5Connector(login=57386954, password="Mxo@0781045677", server="HFMarketsSA-Demo2")
if mt.connect():
    account_info = AccountInfo(mt)
    info = account_info.get_info()
    if info:
        print("Account Balance:", account_info.get_balance())
        print("Account Equity:", account_info.get_equity())
        print("Account Margin:", account_info.get_margin())
        print("Account Free Margin:", account_info.get_free_margin())
        print("Account Drawdown:", account_info.get_drawdown(), "%")
        print("Account Risk Level:", account_info.get_risk_level())

    mt.disconnect()
else:
    print("Failed to connect to MetaTrader 5")
