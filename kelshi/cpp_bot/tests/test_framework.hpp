#pragma once

#include <iostream>
#include <string>
#include <vector>
#include <functional>
#include <chrono>
#include <cmath>
#include <sstream>
#include <iomanip>
#include <exception>

namespace test_runner {

    struct TestCase {
        std::string suite_name;
        std::string test_name;
        std::function<void()> func;
    };

    class TestRegistry {
    public:
        static TestRegistry& instance() {
            static TestRegistry reg;
            return reg;
        }

        void register_test(const std::string& suite, const std::string& name, std::function<void()> f) {
            tests_.push_back({suite, name, std::move(f)});
        }

        const std::vector<TestCase>& get_tests() const {
            return tests_;
        }

    private:
        std::vector<TestCase> tests_;
    };

    struct TestRegistrar {
        TestRegistrar(const std::string& suite, const std::string& name, std::function<void()> f) {
            TestRegistry::instance().register_test(suite, name, std::move(f));
        }
    };

    class AssertionFailureException : public std::runtime_error {
    public:
        AssertionFailureException(const std::string& msg, const char* file, int line)
            : std::runtime_error(msg), file_(file), line_(line) {}

        const char* file() const noexcept { return file_; }
        int line() const noexcept { return line_; }
    private:
        const char* file_;
        int line_;
    };

    inline void fail_assert(const std::string& msg, const char* file, int line) {
        std::ostringstream ss;
        ss << file << ":" << line << " -> " << msg;
        throw AssertionFailureException(ss.str(), file, line);
    }
}

#define TEST(suite_name, test_name) \
    void suite_name##_##test_name(); \
    static test_runner::TestRegistrar registrar_##suite_name##_##test_name( \
        #suite_name, #test_name, suite_name##_##test_name); \
    void suite_name##_##test_name()

#define ASSERT_TRUE(condition) \
    do { \
        if (!(condition)) { \
            test_runner::fail_assert("ASSERT_TRUE failed: " #condition, __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_FALSE(condition) \
    do { \
        if ((condition)) { \
            test_runner::fail_assert("ASSERT_FALSE failed: " #condition " was true", __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_EQ(val1, val2) \
    do { \
        auto _v1 = (val1); \
        auto _v2 = (val2); \
        if (!(_v1 == _v2)) { \
            std::ostringstream _ss; \
            _ss << "ASSERT_EQ failed: (" #val1 " = " << _v1 << ") != (" #val2 " = " << _v2 << ")"; \
            test_runner::fail_assert(_ss.str(), __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_NE(val1, val2) \
    do { \
        auto _v1 = (val1); \
        auto _v2 = (val2); \
        if (_v1 == _v2) { \
            std::ostringstream _ss; \
            _ss << "ASSERT_NE failed: (" #val1 " = " << _v1 << ") == (" #val2 " = " << _v2 << ")"; \
            test_runner::fail_assert(_ss.str(), __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_LT(val1, val2) \
    do { \
        auto _v1 = (val1); \
        auto _v2 = (val2); \
        if (!(_v1 < _v2)) { \
            std::ostringstream _ss; \
            _ss << "ASSERT_LT failed: (" #val1 " = " << _v1 << ") >= (" #val2 " = " << _v2 << ")"; \
            test_runner::fail_assert(_ss.str(), __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_LE(val1, val2) \
    do { \
        auto _v1 = (val1); \
        auto _v2 = (val2); \
        if (!(_v1 <= _v2)) { \
            std::ostringstream _ss; \
            _ss << "ASSERT_LE failed: (" #val1 " = " << _v1 << ") > (" #val2 " = " << _v2 << ")"; \
            test_runner::fail_assert(_ss.str(), __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_GT(val1, val2) \
    do { \
        auto _v1 = (val1); \
        auto _v2 = (val2); \
        if (!(_v1 > _v2)) { \
            std::ostringstream _ss; \
            _ss << "ASSERT_GT failed: (" #val1 " = " << _v1 << ") <= (" #val2 " = " << _v2 << ")"; \
            test_runner::fail_assert(_ss.str(), __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_GE(val1, val2) \
    do { \
        auto _v1 = (val1); \
        auto _v2 = (val2); \
        if (!(_v1 >= _v2)) { \
            std::ostringstream _ss; \
            _ss << "ASSERT_GE failed: (" #val1 " = " << _v1 << ") < (" #val2 " = " << _v2 << ")"; \
            test_runner::fail_assert(_ss.str(), __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_NEAR(val1, val2, epsilon) \
    do { \
        auto _v1 = (val1); \
        auto _v2 = (val2); \
        auto _diff = std::abs(_v1 - _v2); \
        if (_diff > (epsilon)) { \
            std::ostringstream _ss; \
            _ss << "ASSERT_NEAR failed: |" #val1 " (" << _v1 << ") - " #val2 " (" << _v2 << ")| = " << _diff << " > " << (epsilon); \
            test_runner::fail_assert(_ss.str(), __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_THROW(statement, expected_exception) \
    do { \
        bool _threw = false; \
        try { \
            statement; \
        } catch (const expected_exception&) { \
            _threw = true; \
        } catch (...) { \
            test_runner::fail_assert("ASSERT_THROW failed: caught unexpected exception type for: " #statement, __FILE__, __LINE__); \
        } \
        if (!_threw) { \
            test_runner::fail_assert("ASSERT_THROW failed: no exception thrown for: " #statement, __FILE__, __LINE__); \
        } \
    } while(0)

#define ASSERT_NO_THROW(statement) \
    do { \
        try { \
            statement; \
        } catch (const std::exception& _e) { \
            test_runner::fail_assert(std::string("ASSERT_NO_THROW failed: exception thrown: ") + _e.what(), __FILE__, __LINE__); \
        } catch (...) { \
            test_runner::fail_assert("ASSERT_NO_THROW failed: unknown exception thrown for: " #statement, __FILE__, __LINE__); \
        } \
    } while(0)
