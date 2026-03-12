import os
from dotenv import load_dotenv
from delta_rest_client import DeltaRestClient

load_dotenv()
client = DeltaRestClient(
    base_url='https://api.india.delta.exchange',
    api_key=os.getenv('DELTA_API_KEY'),
    api_secret=os.getenv('DELTA_API_SECRET')
)

def print_result(path):
    print(f"\n--- Checking {path} ---")
    resp = client.request('GET', path, auth=True)
    if isinstance(resp, dict):
        result = resp.get('result', [])
        print(f"Count: {len(result)}")
        for r in result:
            print(f"Product: {r.get('product_id')} Symbol: {r.get('symbol')} Size: {r.get('size')}")
    else:
        print("Response:", resp)

print_result('/v2/positions/margined')
print_result('/v2/positions')
