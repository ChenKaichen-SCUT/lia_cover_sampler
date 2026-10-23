#include "math.hpp"
#include <iostream>
int main() {
    std::string op;
    while (std::cin >> op) {
        lia::Z a, b, L, U, rank;
        unsigned j;
        int v;
        if (op == "div") {
            std::cin >> a >> b;
            std::cout << lia::floor_div(a, b) << " " << lia::ceil_div(a, b) << "\n";
        } else {
            std::cin >> a >> b >> j >> v >> L >> U >> rank;
            auto w = lia::find_bit_hit(a, b, j, v, L, U, rank);
            std::cout << lia::count_bit_hits(a, b, j, v, L, U) << " " << (w ? w->get_str() : "none") << "\n";
        }
    }
}
