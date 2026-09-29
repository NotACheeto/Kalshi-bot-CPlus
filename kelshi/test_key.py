import sys
from cryptography.hazmat.primitives.serialization import load_pem_private_key

key_pem = b"""-----BEGIN PRIVATE KEY-----
MC4CAQAwBQYDK2VwBCIEIAp0r2i/8CbSxw0iNTKSZO/EjjwIm/wtmTvDo2Pb6Z99
-----END PRIVATE KEY-----"""

try:
    private_key = load_pem_private_key(key_pem, password=None)
    print(type(private_key))
except Exception as e:
    print("Error:", e)
