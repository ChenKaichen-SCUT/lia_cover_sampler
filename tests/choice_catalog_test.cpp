#include "macro_choice.hpp"
#include "plateau_tracker.hpp"
#include <filesystem>
#include <fstream>
#include <iostream>
using namespace lia;
void check(bool x, const char *why) {
    if (!x)
        throw std::runtime_error(why);
}
std::string file(std::string s) {
    static unsigned n = 0;
    auto p = std::filesystem::temp_directory_path() / ("lia-choice-" + std::to_string(++n) + ".smt2");
    std::ofstream(p) << s;
    return p.string();
}
int main() {
    try {
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (and (= (+ x y) 10)(or (= x "
                           "0)(= x 10))))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog c(f, b);
            check(c.complete && c.groups.size() == 1 && c.groups[0].exclusive, "single choice");
            check(c.groups[0].equality_variables.size() == 2, "necessary equality syntactic link");
            check(c.signature({0, 10}) != c.signature({10, 0}), "real pattern");
            c.commit({0, 10});
            check(c.groups[0].alternatives[0].visits == 1, "formal visit");
        }
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (or (and (= x 0)(= y 0))(and "
                           "(= x 7)(= y 7))))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog c(f, b);
            check(c.groups.size() == 1 && c.groups[0].variables.size() == 2 &&
                      c.groups[0].alternatives[1].assignments.size() == 2,
                  "grouped alternative kept whole");
        }
        {
            Formula f(file("(declare-const x Int)(declare-const y Int)(assert (or (= x 0)(= y 0)))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog c(f, b);
            check(!c.groups[0].exclusive && c.satisfied(c.groups[0], {0, 0}).size() == 2,
                  "non XOR actual set");
            check(c.signature({0, 0}) == c.signature({0, 0}), "requested label cannot change signature");
        }
        {
            Formula f(file("(declare-const b Bool)(declare-const x Int)(assert (or b (or (= x 0)(= x 1))))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog c(f, b);
            check(c.groups.empty(), "conditional nested OR skipped");
        }
        {
            Formula f(file("(declare-const x Int)(assert (or (>= x 0)(<= x 10)))"));
            Budget b;
            b.seconds = 10;
            ChoiceCatalog c(f, b);
            check(c.groups.empty() && c.skipped == 1, "unsupported general OR");
        }
        {
            Formula f(file("(declare-const x Int)(assert (or (= x 0)(= x 1)))"));
            Budget b;
            b.seconds = 0;
            ChoiceCatalog c(f, b);
            check(!c.complete && c.groups.empty(), "bounded analysis");
        }
        {
            PlateauTracker p(false);
            p.output(1, 1, 0, false, "initial");
            p.output(2, 2, 0, true, "macro");
            p.output(5, 3, 7, true, "macro");
            p.output(6, 4, 0, false, "micro");
            p.finish(30, 4);
            check(p.longest_zero_run == 2 && p.longest_no_growth == 25, "plateau time and sample distinct");
            check(p.segments.size() == 2 && !p.segments[0].censored && p.segments[1].censored,
                  "right censoring");
            check(p.segments[0].zero_outputs == 2 && p.segments[0].recovery_origin == "macro", "recovery");
            check(p.json().find("\"failures\":null") != std::string::npos, "unknown historical internals");
        }
        std::cout << "choice/plateau tests passed\n";
        return 0;
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
