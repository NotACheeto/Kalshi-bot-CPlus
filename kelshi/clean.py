import re

with open('fast_main_remote.py', 'r', encoding='utf-8') as f:
    code = f.read()

# Fix the broken sed replacements
code = code.replace("return await resp.orjson.loads=ororjson.loads)", "return await resp.json(loads=orjson.loads)")
code = code.replace("orjson.loads=orjson.loads", "loads=orjson.loads")
code = code.replace("ororjson", "orjson")
code = code.replace("resp.orjson", "resp.json")

# Ensure import orjson is there, and NO import json
code = code.replace("import json\n", "")
if "import orjson" not in code:
    code = "import orjson\n" + code

with open('fast_main_remote.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("File cleaned up")
