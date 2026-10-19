#include "rpc/server.h"
#include <atomic>
#include <iostream>
#include <stdexcept>
#include <string>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    rpc::server server("127.0.0.1", static_cast<uint16_t>(std::stoi(argv[1])));
    std::atomic<int> notifications{0};
    server.bind("ping", []() { return 42; });
    server.bind("echo", [](int value) { return value; });
    server.bind("note", [&notifications]() { ++notifications; });
    server.bind("count", [&notifications]() { return notifications.load(); });
    server.bind("fail", []() -> int { throw std::runtime_error("injected handler error"); });
    server.async_run(4);
    std::cout << "READY" << std::endl;
    std::string stop;
    std::getline(std::cin, stop);
    server.stop();
    return 0;
}
