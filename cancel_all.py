from exchange_client import ExchangeClient
import time

client = ExchangeClient()
orders = client._delta_client.get_live_orders()
print(f"Found {len(orders)} live orders.")

for o in orders:
    try:
        print(f"Canceling order {o['id']} for product {o['product_id']}...")
        client.cancel_order(o['id'], o['product_id'])
        time.sleep(0.5)
    except Exception as e:
        print(f"Failed to cancel {o['id']}: {e}")

print("Done.")
