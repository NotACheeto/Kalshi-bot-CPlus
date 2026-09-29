import sys

with open('fast_main_remote.py', 'r', encoding='utf-8') as f:
    code = f.read()

bad_block = '''    
        connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)'''

good_block = '''    
    connector = aiohttp.TCPConnector(limit=100, force_close=False, enable_cleanup_closed=True)'''

code = code.replace(bad_block, good_block)

code = code.replace('    # Enable TCP_NODELAY', '    # Enable TCP_NODELAY')
code = code.replace('    import socket', '    import socket')
code = code.replace('    async with aiohttp.ClientSession(', '    async with aiohttp.ClientSession(')

# Actually, let's just do a blanket regex fix for the spaces before connector
import re
code = re.sub(r'\n {8}connector = aiohttp.TCPConnector', '\n    connector = aiohttp.TCPConnector', code)
code = re.sub(r'\n {4}# Enable TCP_NODELAY', '\n    # Enable TCP_NODELAY', code)
code = re.sub(r'\n {4}import socket', '\n    import socket', code)
code = re.sub(r'\n {4}async with aiohttp.ClientSession\(', '\n    async with aiohttp.ClientSession(', code)
code = re.sub(r'\n {8}connector=connector,', '\n        connector=connector,', code)
code = re.sub(r'\n {8}json_serialize=lambda x: orjson.dumps\(x\).decode\(\),', '\n        json_serialize=lambda x: orjson.dumps(x).decode(),', code)
code = re.sub(r'\n {4}\) as session:', '\n    ) as session:', code)

with open('fast_main_remote.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("Indentation fixed")
