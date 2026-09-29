#include "test_framework.hpp"
#include <iostream>
#include <iomanip>
#include <chrono>
#include <map>
#include <vector>

int main() {
    std::cout << "\n======================================================================\n";
    std::cout << "        KALSHI C++ HFT MARKET MAKER - TEST & BLACK SWAN SUITE        \n";
    std::cout << "======================================================================\n\n";

    const auto& all_tests = test_runner::TestRegistry::instance().get_tests();
    
    // Group tests by suite
    std::map<std::string, std::vector<test_runner::TestCase>> suites;
    for (const auto& t : all_tests) {
        suites[t.suite_name].push_back(t);
    }

    int total_passed = 0;
    int total_failed = 0;
    auto global_start = std::chrono::high_resolution_clock::now();

    for (const auto& [suite_name, test_list] : suites) {
        std::cout << "─── Suite: [" << suite_name << "] (" << test_list.size() << " tests) ───\n";
        
        for (const auto& test_case : test_list) {
            std::cout << "  RUN     " << test_case.suite_name << "." << test_case.test_name << " ... ";
            std::flush(std::cout);

            auto start = std::chrono::high_resolution_clock::now();
            bool passed = true;
            std::string error_msg;

            try {
                test_case.func();
            } catch (const test_runner::AssertionFailureException& e) {
                passed = false;
                error_msg = e.what();
            } catch (const std::exception& e) {
                passed = false;
                error_msg = std::string("Unexpected exception: ") + e.what();
            } catch (...) {
                passed = false;
                error_msg = "Unknown non-standard exception caught.";
            }

            auto end = std::chrono::high_resolution_clock::now();
            auto elapsed_us = std::chrono::duration_cast<std::chrono::microseconds>(end - start).count();

            if (passed) {
                std::cout << "[PASS] (" << std::fixed << std::setprecision(2) << (elapsed_us / 1000.0) << " ms)\n";
                total_passed++;
            } else {
                std::cout << "[FAIL] (" << std::fixed << std::setprecision(2) << (elapsed_us / 1000.0) << " ms)\n";
                std::cout << "    ❌ ERROR: " << error_msg << "\n";
                total_failed++;
            }
        }
        std::cout << "\n";
    }

    auto global_end = std::chrono::high_resolution_clock::now();
    auto total_elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(global_end - global_start).count();

    std::cout << "======================================================================\n";
    std::cout << "                      TEST EXECUTION SUMMARY                          \n";
    std::cout << "======================================================================\n";
    std::cout << " Total Test Suites:  " << suites.size() << "\n";
    std::cout << " Total Test Cases:   " << all_tests.size() << "\n";
    std::cout << " Total Passed:       " << total_passed << " ✅\n";
    std::cout << " Total Failed:       " << total_failed << " ❌\n";
    std::cout << " Total Elapsed Time: " << total_elapsed_ms << " ms\n";
    std::cout << "======================================================================\n\n";

    return (total_failed == 0) ? 0 : 1;
}
