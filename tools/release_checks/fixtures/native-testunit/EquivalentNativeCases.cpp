#include "tests.hpp"

#include <any>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

struct Counter {
    int value = 0;
};

static bool equals_int(const std::any& actual, int expected) {
    const auto* value = std::any_cast<int>(&actual);
    return value != nullptr && *value == expected;
}

int main() {
    using rqg::tests::Case;
    using rqg::tests::Kind;
    using rqg::tests::Outcome;
    using rqg::tests::Step;
    std::vector<Case> cases;

    cases.push_back(Case{"return-pass", Kind::Return,
        Step{"sum", Outcome::Return, []() -> std::any { return 3 + 7; },
             [](const std::any& actual) { return equals_int(actual, 10); }, {}}, {}, {}});
    cases.push_back(Case{"return-fail", Kind::Return,
        Step{"sum", Outcome::Return, []() -> std::any { return 3 + 7; },
             [](const std::any& actual) { return equals_int(actual, 11); }, {}}, {}, {}});
    cases.push_back(Case{"exception-pass", Kind::Exception,
        Step{"reject", Outcome::Exception,
             []() -> std::any { throw std::runtime_error("invalid transition"); }, {},
             [](const std::exception& error) {
                 return dynamic_cast<const std::runtime_error*>(&error) != nullptr;
             }}, {}, {}});
    cases.push_back(Case{"instance-pass", Kind::Instance,
        Step{"construct", Outcome::Return, []() -> std::any { return Counter{0}; },
             [](const std::any& actual) {
                 const auto* value = std::any_cast<Counter>(&actual);
                 return value != nullptr && value->value == 0;
             }, {}}, {}, {}});

    auto counter = std::make_shared<Counter>();
    Case stateful{"stateful-pass", Kind::Stateful, {},
                  [counter] { counter->value = 0; }, {}};
    stateful.steps.push_back(Step{"add", Outcome::Return,
        [counter]() -> std::any { counter->value += 2; return counter->value; },
        [](const std::any& actual) { return equals_int(actual, 2); }, {}});
    stateful.steps.push_back(Step{"reject", Outcome::Exception,
        []() -> std::any { throw std::runtime_error("invalid transition"); }, {},
        [](const std::exception& error) {
            return dynamic_cast<const std::runtime_error*>(&error) != nullptr;
        }});
    stateful.steps.push_back(Step{"add", Outcome::Return,
        [counter]() -> std::any { counter->value += 3; return counter->value; },
        [](const std::any& actual) { return equals_int(actual, 5); }, {}});
    stateful.steps.push_back(Step{"add", Outcome::Return,
        [counter]() -> std::any { counter->value += 1; return counter->value; },
        [](const std::any& actual) { return equals_int(actual, 6); }, {}});
    cases.push_back(std::move(stateful));

    const Step duplicate_step{"value", Outcome::Return,
        []() -> std::any { return 7; },
        [](const std::any& actual) { return equals_int(actual, 7); }, {}};
    cases.push_back(Case{"duplicate-id", Kind::Return, duplicate_step, {}, {}});
    cases.push_back(Case{"duplicate-id", Kind::Return, duplicate_step, {}, {}});

    const auto result = rqg::tests::execute(cases);
    std::cout << result.to_json() << '\n';
    return 0;
}
