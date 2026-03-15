import json
from exchange_client import ExchangeClient
client = ExchangeClient()
orders = client._delta_client.request("GET", "/v2/orders", {"page_size": 200}, auth=True)
with open("todays_orders.json", "w") as f:
    json.dump(orders, f, indent=4)
