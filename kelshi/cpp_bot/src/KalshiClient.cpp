#include "KalshiClient.hpp"
#include "Config.hpp"
#include <iostream>
#include <chrono>
#include <cpr/cpr.h>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <openssl/err.h>
#include <openssl/bio.h>
#include <openssl/buffer.h>
#include <openssl/rsa.h>

using json = nlohmann::json;

struct KalshiClient::EVP_PKEY_Deleter {
    void operator()(EVP_PKEY* p) const {
        EVP_PKEY_free(p);
    }
};

KalshiClient::KalshiClient(const std::string& key_id, const std::string& private_key)
    : key_id_(key_id), private_key_(private_key) {
    loadPrivateKey();
}

void KalshiClient::loadPrivateKey() {
    BIO* bio = BIO_new_mem_buf(private_key_.c_str(), -1);
    if (!bio) {
        throw std::runtime_error("Failed to create BIO for private key");
    }
    
    EVP_PKEY* pkey = PEM_read_bio_PrivateKey(bio, nullptr, nullptr, nullptr);
    BIO_free(bio);
    
    if (!pkey) {
        // Fallback: If it's a file path instead of the raw key string
        FILE* fp = fopen(private_key_.c_str(), "r");
        if (fp) {
            pkey = PEM_read_PrivateKey(fp, nullptr, nullptr, nullptr);
            fclose(fp);
        }
        
        if (!pkey) {
            throw std::runtime_error("Failed to read Kalshi private key. Ensure KALSHI_PRIVATE_KEY is correct.");
        }
    }
    
    pkey_ = std::shared_ptr<void>(pkey, EVP_PKEY_Deleter());
}

std::string KalshiClient::sign(const std::string& method, const std::string& path, long long timestamp) {
    // Message to sign: timestamp + method + path
    std::string message = std::to_string(timestamp) + method + path;
    
    EVP_MD_CTX* mdctx = EVP_MD_CTX_new();
    if (!mdctx) throw std::runtime_error("Failed to create MD CTX");
    
    EVP_PKEY* pkey = static_cast<EVP_PKEY*>(pkey_.get());
    int key_type = EVP_PKEY_base_id(pkey);
    
    EVP_PKEY_CTX* pctx = nullptr;
    if (key_type == EVP_PKEY_ED25519) {
        // Ed25519 requires nullptr for digest type in OpenSSL
        if (EVP_DigestSignInit(mdctx, nullptr, nullptr, nullptr, pkey) <= 0) {
            EVP_MD_CTX_free(mdctx);
            throw std::runtime_error("EVP_DigestSignInit failed for Ed25519 key");
        }
    } else {
        if (EVP_DigestSignInit(mdctx, &pctx, EVP_sha256(), nullptr, pkey) <= 0) {
            EVP_MD_CTX_free(mdctx);
            throw std::runtime_error("EVP_DigestSignInit failed");
        }
        
        if (key_type == EVP_PKEY_RSA) {
            if (EVP_PKEY_CTX_set_rsa_padding(pctx, RSA_PKCS1_PSS_PADDING) <= 0) {
                EVP_MD_CTX_free(mdctx);
                throw std::runtime_error("Failed to set PSS padding");
            }
            if (EVP_PKEY_CTX_set_rsa_pss_saltlen(pctx, RSA_PSS_SALTLEN_MAX) <= 0) {
                EVP_MD_CTX_free(mdctx);
                throw std::runtime_error("Failed to set PSS saltlen");
            }
        }
    }
    
    if (EVP_DigestSignUpdate(mdctx, message.c_str(), message.size()) <= 0) {
        EVP_MD_CTX_free(mdctx);
        throw std::runtime_error("EVP_DigestSignUpdate failed");
    }
    
    size_t siglen = 0;
    if (EVP_DigestSignFinal(mdctx, nullptr, &siglen) <= 0) {
        EVP_MD_CTX_free(mdctx);
        throw std::runtime_error("EVP_DigestSignFinal failed (length)");
    }
    
    std::vector<unsigned char> signature(siglen);
    if (EVP_DigestSignFinal(mdctx, signature.data(), &siglen) <= 0) {
        EVP_MD_CTX_free(mdctx);
        throw std::runtime_error("EVP_DigestSignFinal failed (sign)");
    }
    
    EVP_MD_CTX_free(mdctx);
    
    // Base64 Encode
    BIO* bio = BIO_new(BIO_f_base64());
    BIO* bmem = BIO_new(BIO_s_mem());
    bio = BIO_push(bio, bmem);
    BIO_set_flags(bio, BIO_FLAGS_BASE64_NO_NL); // No newlines
    
    BIO_write(bio, signature.data(), static_cast<int>(siglen));
    BIO_flush(bio);
    
    BUF_MEM* bptr;
    BIO_get_mem_ptr(bio, &bptr);
    
    std::string b64_signature(bptr->data, bptr->length);
    BIO_free_all(bio);
    
    return b64_signature;
}

json KalshiClient::executeRequest(const std::string& method, const std::string& path, const json& payload) {
    auto now = std::chrono::system_clock::now();
    long long timestamp = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
    
    std::string sig = sign(method, "/trade-api/v2" + path, timestamp);
    std::string url = base_url_ + "/trade-api/v2" + path;
    
    cpr::Header headers = {
        {"KALSHI-ACCESS-KEY", key_id_},
        {"KALSHI-ACCESS-TIMESTAMP", std::to_string(timestamp)},
        {"KALSHI-ACCESS-SIGNATURE", sig},
        {"Content-Type", "application/json"}
    };
    
    cpr::Response r;
    if (method == "GET") {
        r = cpr::Get(cpr::Url{url}, headers);
    } else if (method == "POST") {
        r = cpr::Post(cpr::Url{url}, headers, cpr::Body{payload.dump()});
    } else if (method == "DELETE") {
        r = cpr::Delete(cpr::Url{url}, headers);
    }
    
    if (r.status_code != 200 && r.status_code != 201) {
        std::cerr << "HTTP Error " << r.status_code << " on " << method << " " << path << ": " << r.text << "\n";
    }
    
    try {
        return json::parse(r.text);
    } catch (...) {
        return json({});
    }
}

json KalshiClient::get(const std::string& path) { return executeRequest("GET", path); }
json KalshiClient::post(const std::string& path, const json& payload) { return executeRequest("POST", path, payload); }
json KalshiClient::del(const std::string& path) { return executeRequest("DELETE", path); }

json KalshiClient::getPositions() {
    return get("/portfolio/positions");
}

json KalshiClient::getBalance() {
    return get("/portfolio/balance");
}

json KalshiClient::placeOrder(const std::string& ticker, const std::string& action, const std::string& side, int count, int price, const std::string& client_order_id) {
    json payload = {
        {"ticker", ticker},
        {"client_order_id", client_order_id},
        {"action", action},
        {"side", side},
        {"count", count},
        {"yes_price", price},
        {"type", "limit"},
        {"time_in_force", "good_till_canceled"},
        {"self_trade_prevention_type", "maker"},
        {"post_only", true}
    };
    return post("/portfolio/orders", payload);
}

json KalshiClient::cancelOrder(const std::string& order_id) {
    return del("/portfolio/orders/" + order_id);
}
