
import logging
from exchange_client import ExchangeClient

logging.basicConfig(level=logging.INFO)

def debug_api():
    client = ExchangeClient()
    print("Testing ticker endpoints...")
    
    # 1. Try to find BTCUSD product ID
    try:
        products = client.get_products()
        btcusd = next((p for p in products if p['symbol'] == 'BTCUSD'), None)
        if not btcusd:
            print("BTCUSD product not found")
            return
            
        pid = btcusd['id']
        print(f"BTCUSD Product ID: {pid}")
        
        # 2. Try various ticker endpoints
        endpoints = [
            f"/v2/products/{pid}/ticker",
            f"/v2/tickers/{btcusd['symbol']}",
            "/v2/tickers"
        ]
        
        for ep in endpoints:
            print(f"\nTesting {ep}...")
            try:
                resp = client._delta_client.request("GET", ep)
                if hasattr(resp, "status_code"):
                     print(f"Status: {resp.status_code}")
                if hasattr(resp, "json"):
                    data = resp.json()
                    success = data.get("success", False) if isinstance(data, dict) else False
                    print(f"Success: {success}")
                    if success:
                        print("Got valid response!")
                        if isinstance(data, dict):
                            res = data.get("result", [])
                            if res:
                                print(f"Sample ticker: {res[0]}")
                        elif isinstance(data, list):
                             print(f"Sample ticker: {data[0]}")
            except Exception as e:
                print(f"Error: {e}")
                
    except Exception as e:
        print(f"Error getting products: {e}")

if __name__ == "__main__":
    debug_api()
