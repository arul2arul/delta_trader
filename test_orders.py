import os
from dotenv import load_dotenv
from delta_rest_client import DeltaRestClient
import json

load_dotenv()
client = DeltaRestClient(
    base_url='https://api.india.delta.exchange',
    api_key=os.getenv('DELTA_API_KEY'),
    api_secret=os.getenv('DELTA_API_SECRET')
)
resp = client.request('GET', '/v2/orders', auth=True)
if hasattr(resp, 'json'):
    data = resp.json()
    open_orders = [o for o in data.get('result', []) if o.get('state') in ('open', 'pending')]
    print(f'Open Orders: {len(open_orders)}')
    for o in open_orders:
        print(f"ID: {o.get('id')} Product: {o.get('product_id')} Type: {o.get('order_type')} State: {o.get('state')} Stop: {o.get('stop_price')} Limit: {o.get('limit_price')}")
elif isinstance(resp, dict):
    open_orders = [o for o in resp.get('result', []) if o.get('state') in ('open', 'pending')]
    print(f'Open Orders: {len(open_orders)}')
    for o in open_orders:
        print(f"ID: {o.get('id')} Product: {o.get('product_id')} Type: {o.get('order_type')} State: {o.get('state')} Stop: {o.get('stop_price')} Limit: {o.get('limit_price')}")
else:
    print('Failed to parse orders:', resp)
