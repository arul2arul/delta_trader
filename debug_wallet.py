
import os
import json
import logging
from dotenv import load_dotenv
from exchange_client import ExchangeClient

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load env variables
load_dotenv()

def debug_wallet():
    print("Initializing Exchange Client...")
    client = ExchangeClient()
    
    print("\nFetching Raw Wallet Balances...")
    # direct request to see everything
    try:
        resp = client.client.request("GET", "/v2/wallet/balances", auth=True)
        print(f"\nRaw Response Type: {type(resp)}")
        if hasattr(resp, "json"):
            print(json.dumps(resp.json(), indent=2))
        elif isinstance(resp, dict) or isinstance(resp, list):
            print(json.dumps(resp, indent=2))
        else:
            print(resp)
            
    except Exception as e:
        print(f"Error fetching balances: {e}")

if __name__ == "__main__":
    debug_wallet()
