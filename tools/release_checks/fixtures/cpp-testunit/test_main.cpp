#include "tests.hpp"

#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace rqg::tests;

struct PublicCounter {
    int value = 0;
    int change(int amount) { return value += amount; }
    int reject() { throw std::invalid_argument("invalid transition"); }
};

int main() {
    PublicCounter counter;
    const Step addition{
        "add", Outcome::Return, []() -> std::any { return 3 + 7; },
        [](const std::any& actual) { return std::any_cast<int>(actual) == 10; }, {}};
    const Step wrong_output{
        "add", Outcome::Return, []() -> std::any { return 3 + 7; },
        [](const std::any& actual) { return std::any_cast<int>(actual) == 11; }, {}};
    // Instance semantics must exercise an actual public constructor, not a
    // fixture which only checks the enum or runner implementation shape.
    const Step instance_constructor{
        "construct", Outcome::Return, []() -> std::any { return PublicCounter{}; },
        [](const std::any& actual) {
            return std::any_cast<PublicCounter>(actual).value == 0;
        }, {}};
    const Step expected_exception_from_return{
        "construct", Outcome::Return, []() -> std::any { return PublicCounter{}; },
        [](const std::any& actual) {
            return std::any_cast<PublicCounter>(actual).value == 0;
        }, {}};
    const Step valid_exception{
        "parse", Outcome::Exception,
        []() -> std::any { throw std::invalid_argument("invalid payload"); }, {},
        [](const std::exception& caught) {
            return dynamic_cast<const std::invalid_argument*>(&caught) != nullptr;
        }};
    const Step wrong_exception{
        "parse", Outcome::Exception,
        []() -> std::any { throw std::invalid_argument("invalid payload"); }, {},
        [](const std::exception& caught) {
            return dynamic_cast<const std::out_of_range*>(&caught) != nullptr;
        }};
    const Case stateful{
        "public-sequence", Kind::Stateful, {}, [&counter] { counter.value = 0; },
        {{"change", Outcome::Return, [&counter]() -> std::any { return counter.change(2); },
          [](const std::any& actual) { return std::any_cast<int>(actual) == 2; }, {}},
         {"reject", Outcome::Exception, [&counter]() -> std::any { return counter.reject(); }, {},
          [](const std::exception& caught) {
              return dynamic_cast<const std::invalid_argument*>(&caught) != nullptr;
          }},
         {"change", Outcome::Return, [&counter]() -> std::any { return counter.change(3); },
          [](const std::any& actual) { return std::any_cast<int>(actual) == 5; }, {}}}};
    const auto all = execute({{"addition", Kind::Return, addition, {}, {}},
                              {"constructor-instance", Kind::Instance, instance_constructor, {}, {}},
                              {"exception-kind-mismatch", Kind::Exception, expected_exception_from_return, {}, {}},
                              {"bad-answer", Kind::Return, wrong_output, {}, {}},
                              {"expected-error", Kind::Exception, valid_exception, {}, {}},
                              {"wrong-error", Kind::Exception, wrong_exception, {}, {}},
                              stateful,
                              {"addition", Kind::Return, addition, {}, {}}});
    std::cout << all.to_json() << '\n';
    const std::vector<std::string> expected{"PASS", "PASS", "INVALID_TEST", "FAIL", "PASS", "FAIL", "PASS", "INVALID_TEST"};
    if (all.cases.size() != expected.size()) return 1;
    for (std::size_t i = 0; i < expected.size(); ++i) {
        if (all.cases[i].status != expected[i]) return 2;
    }
    if (all.cases[6].steps_executed != 3 || all.cases[6].steps.size() != 3) return 3;
    try {
        run({{"bad-answer", Kind::Return, wrong_output, {}, {}},
             {"addition", Kind::Return, addition, {}, {}}});
        return 4;
    } catch (const TestRunFailure& caught) {
        if (caught.report.cases.size() != 2 || caught.report.cases[1].status != "PASS") return 5;
    }
    return 0;
}
