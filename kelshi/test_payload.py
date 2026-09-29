import asyncio
import websockets
import json
async def test():
    async with websockets.connect('wss://api.elections.kalshi.com/trade-api/ws/v2') as ws:
        await ws.send(json.dumps({'id': 1, 'cmd': 'subscribe', 'params': {'channels': ['ticker']}}))
        for _ in range(50):
            msg = await ws.recv()
            data = json.loads(msg)
            if data.get('type') == 'ticker':
                print(data.get('msg', {}))
                break
asyncio.run(test())
