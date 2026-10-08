import asyncio
import json

from app.websocket_client import WebsocketClient


def _feed(ws, payload):
    asyncio.run(ws._handle_message(json.dumps(payload)))


def test_price_change_updates_each_asset_book():
    ws = WebsocketClient()
    _feed(ws, {"event_type": "book", "asset_id": "A", "bids": [{"price": "0.50", "size": "10"}], "asks": [{"price": "0.52", "size": "10"}]})
    # Real format: no top-level asset_id, one per change
    _feed(ws, {"event_type": "price_change", "market": "0x1", "price_changes": [
        {"asset_id": "A", "price": "0.51", "size": "25", "side": "BUY"},
        {"asset_id": "A", "price": "0.52", "size": "0", "side": "SELL"},
        {"asset_id": "B", "price": "0.48", "size": "7", "side": "SELL"},
    ]})
    book_a = ws.cache["A"]["orderbook"]
    assert {l["price"] for l in book_a["bids"]} == {"0.50", "0.51"}
    assert book_a["asks"] == []
    assert ws.cache["B"]["orderbook"]["asks"] == [{"price": "0.48", "size": "7"}]


def test_last_trade_price_is_recorded_as_trade():
    ws = WebsocketClient()
    _feed(ws, {"event_type": "last_trade_price", "asset_id": "A", "price": "0.456", "size": "219.2", "side": "BUY"})
    assert ws.cache["A"]["price"] == 0.456
    trades = ws.get_trades_since("A", 0)
    assert trades[0]["price"] == 0.456 and trades[0]["size"] == 219.2
