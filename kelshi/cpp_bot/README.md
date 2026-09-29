# Kalshi C++ HFT Bot

This is a high-frequency trading bot for Kalshi written in C++20. It mirrors the exact logic of the Python `fast_main.py` bot but uses native system threads, `websocketpp` (using ASIO) for real-time orderbook feeds, and `OpenSSL` for sub-millisecond API request signing.

## Prerequisites (Windows)

1. **Visual Studio 2022**: Install the "Desktop development with C++" workload (this includes MSVC compiler and CMake).
2. **vcpkg**: C++ Package Manager.
   ```powershell
   git clone https://github.com/microsoft/vcpkg.git
   .\vcpkg\bootstrap-vcpkg.bat
   ```

## Dependencies

The bot relies on several C++ libraries for network and crypto operations. Install them using vcpkg:

```powershell
.\vcpkg\vcpkg install openssl nlohmann-json websocketpp asio cpr --triplet x64-windows
```

## Building

```powershell
mkdir build
cd build
# Tell CMake to use vcpkg to find the installed libraries
cmake .. -DCMAKE_TOOLCHAIN_FILE="C:/path/to/your/vcpkg/scripts/buildsystems/vcpkg.cmake"
cmake --build . --config Release
```

## Running

The bot reads standard environment variables for authentication, identical to the Python script.

```powershell
$env:KALSHI_API_KEY_ID="your_key"
$env:KALSHI_PRIVATE_KEY="-----BEGIN PRIVATE KEY... (or path to .pem file)"
$env:TRADE_ENV="paper"

.\Release\kalshi_bot.exe
```
