import sys

with open('fast_main.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Swap import json to import orjson
if 'import json' in code:
    code = code.replace('import json', 'import orjson')

# Wait, if we use orjson.dumps, it returns bytes. aiohttp session.post(json=data) uses standard json.
# To use orjson with aiohttp, we must pass data=orjson.dumps(data) instead of json=data.
# But wait, modifying all aiohttp calls is risky. We can pass a custom json_serialize argument to ClientSession!

old_session = 'async with aiohttp.ClientSession() as session:'
new_session = '''    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)
    # Enable TCP_NODELAY at the socket level
    import socket
    async with aiohttp.ClientSession(
        connector=connector,
        json_serialize=lambda x: orjson.dumps(x).decode(),
    ) as session:
        # Patch sockets to disable Nagle's algorithm
        for s in connector._conns.values():
            for conn in s:
                try:
                    conn[0].transport.get_extra_info('socket').setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except:
                    pass'''

code = code.replace(old_session, new_session)

# In kalshi_async_request:
# elif method == "POST":
#     async with session.post(url, headers=headers, json=data) as resp:

# wait, decoding json from websocket:
old_ws = 'payload = json.loads(message)'
new_ws = 'payload = orjson.loads(message)'
code = code.replace(old_ws, new_ws)

# Fix response parsing in kalshi_async_request
# return await resp.json()
# actually aiohttp's resp.json(loads=orjson.loads) can be used, but default is fine.
code = code.replace('return await resp.json()', 'return await resp.json(loads=orjson.loads)')

with open('fast_main.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("TCP and orjson patched")
