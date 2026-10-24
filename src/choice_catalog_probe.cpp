#include "macro_choice.hpp"
#include "plateau_tracker.hpp"
#include <iomanip>
#include <iostream>
int main(int argc, char **argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--plateau") {
            lia::PlateauTracker p(false);
            double t, g;
            uint64_t i;
            unsigned mode;
            std::string origin;
            uint64_t last = 0;
            double end = 0;
            std::cin >> end;
            while (std::cin >> t >> i >> g >> mode >> origin) {
                p.output(t, i, g, mode, origin);
                last = i;
            }
            p.finish(end, last);
            std::cout << p.json() << '\n';
            return 0;
        }
        if (argc != 2)
            throw std::runtime_error("choice_catalog_probe FORMULA | --plateau");
        lia::Formula f(argv[1]);
        lia::Budget b;
        b.seconds = .1;
        lia::ChoiceCatalog c(f, b);
        std::cout << c.json() << '\n';
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
