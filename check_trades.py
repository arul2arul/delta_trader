import json
from exchange_client import ExchangeClient
import pytz
from datetime import datetime

client = ExchangeClient()

def get_recent_orders():
    # Attempt to fetch orders via REST
    ist = pytz.timezone("Asia/Kolkata")
    now = datetime.now(ist)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    payload = {
        "start": int(start_of_day.timestamp()),
        "end": int(now.timestamp()),
        "page_size": 200
    }
    
    try:
        # Instead of generic /v2/orders which has query param issues sometimes, we'll fetch fills
        resp = client._delta_client.request("GET", "/v2/fills", payload, auth=True)
        print("Fills:", json.dumps(resp, indent=2))
    except Exception as e:
        print("Error fetching fills:", e)
        
    try:
        resp = client._delta_client.request("GET", "/v2/orders/history", payload, auth=True)
        with open("orders.json", "w") as f:
            json.dump(resp, f, indent=2)
        print("\nOrders saved to orders.json")
    except Exception as e:
        print("Error fetching orders:", e)

get_recent_orders()
