import os
from dotenv import load_dotenv

load_dotenv()

KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID")
KALSHI_PRIVATE_KEY = os.getenv("KALSHI_PRIVATE_KEY")
if KALSHI_PRIVATE_KEY:
    # Handle literal string newlines if passed directly in env vars
    KALSHI_PRIVATE_KEY = KALSHI_PRIVATE_KEY.replace('\\n', '\n')

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TRADE_ENV = os.getenv("TRADE_ENV", "demo")
TICK_INTERVAL_SECONDS = int(os.getenv("TICK_INTERVAL_SECONDS", "10"))

import socket
import sys
import logging

_singleton_socket = None

def enforce_single_instance(port):
    """Binds to a local port to ensure only one instance of the bot is running."""
    global _singleton_socket
    _singleton_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _singleton_socket.bind(('127.0.0.1', port))
    except socket.error:
        logging.error(f"Another instance of this bot is already running (port {port} in use). Exiting to prevent ghost processes.")
        sys.exit(1)
