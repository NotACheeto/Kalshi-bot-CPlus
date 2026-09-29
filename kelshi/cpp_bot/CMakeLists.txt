cmake_minimum_required(VERSION 3.15)
project(KalshiHFTBot CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Find necessary packages
# We assume the user is using vcpkg to manage dependencies:
# vcpkg install openssl nlohmann-json websocketpp asio cpr

find_package(OpenSSL REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)
find_package(cpr CONFIG REQUIRED)
find_package(Threads REQUIRED)

add_executable(kalshi_bot
    src/main.cpp
    src/KalshiClient.cpp
    src/TradingEngine.cpp
)

include(FetchContent)
FetchContent_Declare(
    websocketpp
    GIT_REPOSITORY https://github.com/zaphoyd/websocketpp.git
    GIT_TAG        0.8.2
)
FetchContent_MakeAvailable(websocketpp)

target_include_directories(kalshi_bot PRIVATE include ${websocketpp_SOURCE_DIR})
target_compile_definitions(kalshi_bot PRIVATE ASIO_STANDALONE _WEBSOCKETPP_CPP11_STL_)

target_link_libraries(kalshi_bot PRIVATE
    OpenSSL::SSL
    OpenSSL::Crypto
    nlohmann_json::nlohmann_json
    cpr::cpr
    Threads::Threads
)

# If Windows, link ws2_32 for sockets
if (WIN32)
    target_link_libraries(kalshi_bot PRIVATE ws2_32)
endif()

# Test Executable (Standalone zero-external-dependency suite for testing edge cases & black swans)
add_executable(kalshi_tests
    tests/test_orderbook_edge_cases.cpp
    tests/test_volatility_circuit_breaker.cpp
    tests/test_inventory_skew_and_risk.cpp
    tests/test_collateral_and_solvency.cpp
    tests/test_arbitrage_and_mispricing.cpp
    tests/test_black_swan_scenarios.cpp
    tests/run_all_tests.cpp
)
target_include_directories(kalshi_tests PRIVATE include tests)

