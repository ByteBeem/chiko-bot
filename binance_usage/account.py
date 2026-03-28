from .client import client


def get_balances() -> list[dict]:
    """
    Return all non-zero asset balances from the Binance account.

    Returns:
        List of dicts with keys: asset, free, locked, total
    """
    account = client.get_account()
    balances = []
    for asset in account["balances"]:
        free = float(asset["free"])
        locked = float(asset["locked"])
        if free > 0 or locked > 0:
            balances.append({
                "asset": asset["asset"],
                "free": free,
                "locked": locked,
                "total": free + locked,
            })
    return balances