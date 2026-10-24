#ifndef RQG_TEST_CASES_HPP
#define RQG_TEST_CASES_HPP

// An isolated test runner, not a dependency of the code under test.
// The supplied oracle observes a public result, never an implementation shape.
#include <any>
#include <exception>
#include <functional>
#include <type_traits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace rqg {
namespace tests {

enum class Outcome { Return, Exception };
enum class Kind { Return, Exception, Instance, Stateful };

struct Step {
    std::string method;
    Outcome outcome;
    std::function<std::any()> invoke;
    std::function<bool(const std::any&)> equals;
    std::function<bool(const std::exception&)> exception_matches;
};

/** C++ pure-virtual Case contract: incomplete implementations cannot instantiate. */
class ITestCase {
public:
    virtual ~ITestCase() = default;
    virtual const std::string& get_case_id() const = 0;
    virtual Kind get_kind() const = 0;
    virtual const Step& get_invocation() const = 0;
    virtual const std::function<void()>& get_constructor() const = 0;
    virtual const std::vector<Step>& get_steps() const = 0;
};

class Case final : public ITestCase {
public:
    std::string case_id;
    Kind kind;
    Step invocation;
    std::function<void()> construct;
    std::vector<Step> steps;

    Case(std::string id, Kind test_kind, Step entry,
         std::function<void()> setup, std::vector<Step> sequence)
        : case_id(std::move(id)), kind(test_kind), invocation(std::move(entry)),
          construct(std::move(setup)), steps(std::move(sequence)) {}

    const std::string& get_case_id() const override { return case_id; }
    Kind get_kind() const override { return kind; }
    const Step& get_invocation() const override { return invocation; }
    const std::function<void()>& get_constructor() const override { return construct; }
    const std::vector<Step>& get_steps() const override { return steps; }
};

static_assert(std::is_abstract_v<ITestCase>);
static_assert(!std::is_abstract_v<Case>);

struct StepResult {
    std::size_t index;
    std::string method;
    std::string status;
    std::string detail;
};

struct CaseResult {
    std::string case_id;
    std::string status;
    std::string detail;
    std::size_t steps_executed;
    std::vector<StepResult> steps;
};

inline std::string json_string(const std::string& value) {
    static const char digits[] = "0123456789abcdef";
    std::string escaped = "\"";
    for (unsigned char c : value) {
        switch (c) {
            case '\"': escaped += "\\\""; break;
            case '\\': escaped += "\\\\"; break;
            case '\n': escaped += "\\n"; break;
            case '\r': escaped += "\\r"; break;
            case '\t': escaped += "\\t"; break;
            default:
                if (c < 0x20) {
                    escaped += "\\u00";
                    escaped += digits[c >> 4];
                    escaped += digits[c & 15];
                } else {
                    escaped += static_cast<char>(c);
                }
        }
    }
    return escaped + "\"";
}

struct BatchResult {
    std::vector<CaseResult> cases;

    std::size_t passed() const {
        std::size_t result = 0;
        for (const auto& entry : cases) {
            result += entry.status == "PASS";
        }
        return result;
    }

    bool succeeded() const { return !cases.empty() && passed() == cases.size(); }

    std::string to_json() const {
        std::ostringstream out;
        out << "{\"schema_version\":\"rqg.testunit.result.v1\",\"passed\":" << passed()
            << ",\"succeeded\":" << (succeeded() ? "true" : "false") << ",\"cases\":[";
        bool first_case = true;
        for (const auto& entry : cases) {
            if (!first_case) out << ',';
            first_case = false;
            out << "{\"case_id\":" << json_string(entry.case_id) << ",\"status\":" << json_string(entry.status)
                << ",\"detail\":" << json_string(entry.detail) << ",\"steps_executed\":"
                << entry.steps_executed << ",\"steps\":[";
            bool first_step = true;
            for (const auto& step : entry.steps) {
                if (!first_step) out << ',';
                first_step = false;
                out << "{\"index\":" << step.index << ",\"method\":" << json_string(step.method)
                    << ",\"status\":" << json_string(step.status) << ",\"detail\":"
                    << json_string(step.detail) << '}';
            }
            out << "]}";
        }
        out << "]}";
        return out.str();
    }
};

class TestRunFailure : public std::runtime_error {
public:
    explicit TestRunFailure(BatchResult result)
        : std::runtime_error("One or more public behavior cases failed"), report(std::move(result)) {}
    BatchResult report;
};

inline std::pair<bool, std::string> observe(const Step& step) {
    if (!step.invoke) throw std::invalid_argument("missing public invocation");
    if (step.outcome == Outcome::Exception) {
        if (!step.exception_matches) throw std::invalid_argument("missing exception oracle");
        try {
            step.invoke();
        } catch (const std::exception& caught) {
            return {step.exception_matches(caught), "Caught a public exception"};
        }
        return {false, "Expected exception was not raised"};
    }
    if (!step.equals) throw std::invalid_argument("missing public result oracle");
    std::any actual;
    try {
        actual = step.invoke();
    } catch (const std::exception&) {
        return {false, "Unexpected public exception"};
    }
    return {step.equals(actual), "Public result compared against independent oracle"};
}

template <typename ConcreteCase = Case>
inline BatchResult execute(const std::vector<ConcreteCase>& cases) {
    static_assert(std::is_base_of_v<ITestCase, ConcreteCase>,
                  "Every executable C++ Case must implement ITestCase");
    if (cases.empty()) throw std::invalid_argument("TestUnit requires nonempty Cases");
    BatchResult report;
    std::unordered_set<std::string> seen;
    for (const ConcreteCase& concrete : cases) {
        const ITestCase& testcase = concrete;
        CaseResult result{testcase.get_case_id(), "INVALID_TEST", "", 0, {}};
        if (testcase.get_case_id().empty() || !seen.insert(testcase.get_case_id()).second) {
            result.detail = "Case identity missing or duplicate";
        } else {
            try {
                if (testcase.get_kind() == Kind::Stateful) {
                    if (!testcase.get_constructor() || testcase.get_steps().empty())
                        throw std::invalid_argument("Stateful Case needs constructor and steps");
                    try {
                        testcase.get_constructor()();
                    } catch (const std::exception&) {
                        result.status = "FAIL";
                        result.detail = "Constructor raised";
                        report.cases.push_back(std::move(result));
                        continue;
                    }
                    result.status = "PASS";
                    result.detail = "State sequence matched";
                    for (const auto& step : testcase.get_steps()) {
                        if (step.method.empty() || step.method[0] == '_')
                            throw std::invalid_argument("Step must identify public method");
                        const auto observation = observe(step);
                        ++result.steps_executed;
                        result.steps.push_back({result.steps_executed, step.method,
                                                observation.first ? "PASS" : "FAIL", observation.second});
                        if (!observation.first) {
                            result.status = "FAIL";
                            result.detail = observation.second;
                            break;
                        }
                    }
                } else {
                    if ((testcase.get_kind() == Kind::Exception) !=
                        (testcase.get_invocation().outcome == Outcome::Exception))
                        throw std::invalid_argument("Case kind and outcome do not agree");
                    const auto observation = observe(testcase.get_invocation());
                    result.steps_executed = 1;
                    result.status = observation.first ? "PASS" : "FAIL";
                    result.detail = observation.second;
                }
            } catch (const std::exception& caught) {
                result.status = "INVALID_TEST";
                result.detail = caught.what();
            }
        }
        report.cases.push_back(std::move(result));
    }
    return report;
}

template <typename ConcreteCase = Case>
inline BatchResult run(const std::vector<ConcreteCase>& cases) {
    BatchResult report = execute(cases);
    if (!report.succeeded()) throw TestRunFailure(report);
    return report;
}

}  // namespace tests
}  // namespace rqg
#endif  // RQG_TEST_CASES_HPP
