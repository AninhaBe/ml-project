import sys; sys.path.insert(0, '.')
from src.ml_api import MLClient
from src.config import load_ml_config

ml = MLClient(load_ml_config())

# Testa produtos com e sem estoque
for cid in ["MLB36941484", "MLB74381509", "MLB64462243"]:
    p = ml.get(f"/products/{cid}")
    if not p:
        print(f"{cid}: None")
        continue
    status = p.get("status")
    bbw = p.get("buy_box_winner") or {}
    print(f"\n{cid}: status={status}")
    print(f"  buy_box_winner keys: {list(bbw.keys())[:10]}")
    print(f"  bbw item_id: {bbw.get('item_id')}")
    print(f"  bbw status: {bbw.get('status')}")
    
    # Testa GET /items/{item_id} do winner
    item_id = bbw.get("item_id")
    if item_id:
        item = ml.get(f"/items/{item_id}")
        if item:
            print(f"  item status: {item.get('status')}")
            print(f"  item available_quantity: {item.get('available_quantity')}")
            print(f"  item sold_quantity: {item.get('sold_quantity')}")
