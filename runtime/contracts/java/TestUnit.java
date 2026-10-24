package rqg.contracts;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.function.Predicate;

/** Executes independent public-behavior cases without inspecting the target implementation. */
public final class TestUnit {
    private TestUnit() {}

    public enum Kind { RETURN, EXCEPTION, INSTANCE, STATEFUL }
    public enum Outcome { RETURN, EXCEPTION }

    @FunctionalInterface
    public interface Call { Object invoke() throws Exception; }

    public record Step(String method, Outcome outcome, Call call,
                       Predicate<Object> equals, Predicate<Throwable> matches) {}
    /** Native compile-time Case contract; Java rejects missing methods. */
    public interface ITestCase {
        String case_id();
        Kind kind();
        Step invocation();
        Call construct();
        List<Step> steps();
    }

    public record Case(String case_id, Kind kind, Step invocation, Call construct,
                       List<Step> steps) implements ITestCase {}
    public record StepResult(int index, String method, String status, String detail) {}
    public record CaseResult(String case_id, String status, String detail,
                             int stepsExecuted, List<StepResult> steps) {}

    public record BatchResult(List<CaseResult> cases) {
        public long passed() { return cases.stream().filter(row -> row.status().equals("PASS")).count(); }
        public boolean succeeded() { return !cases.isEmpty() && passed() == cases.size(); }

        public String toJson() {
            StringBuilder out = new StringBuilder("{\"schema_version\":\"rqg.testunit.result.v1\",\"passed\":");
            out.append(passed()).append(",\"succeeded\":").append(succeeded()).append(",\"cases\":[");
            for (int i = 0; i < cases.size(); ++i) {
                if (i != 0) out.append(',');
                CaseResult row = cases.get(i);
                out.append("{\"case_id\":").append(quoted(row.case_id())).append(",\"status\":")
                   .append(quoted(row.status())).append(",\"detail\":")
                   .append(quoted(row.detail())).append(",\"steps_executed\":")
                   .append(row.stepsExecuted()).append(",\"steps\":[");
                for (int k = 0; k < row.steps().size(); ++k) {
                    if (k != 0) out.append(',');
                    StepResult step = row.steps().get(k);
                    out.append("{\"index\":").append(step.index()).append(",\"method\":")
                       .append(quoted(step.method())).append(",\"status\":")
                       .append(quoted(step.status())).append(",\"detail\":")
                       .append(quoted(step.detail())).append('}');
                }
                out.append("]}");
            }
            return out.append("]}").toString();
        }
    }

    public static final class TestRunFailure extends AssertionError {
        public final BatchResult report;
        public TestRunFailure(BatchResult report) {
            super("One or more public behavior cases failed");
            this.report = report;
        }
    }

    private record Observation(boolean matched, String detail) {}

    private static Observation observe(Step step) throws Exception {
        if (step == null || step.call() == null) throw new IllegalArgumentException("missing public invocation");
        if (step.outcome() == Outcome.EXCEPTION) {
            if (step.matches() == null) throw new IllegalArgumentException("missing exception oracle");
            try {
                step.call().invoke();
            } catch (Exception observed) {
                return new Observation(step.matches().test(observed), "Caught a public exception");
            }
            return new Observation(false, "Expected exception was not raised");
        }
        if (step.outcome() != Outcome.RETURN || step.equals() == null)
            throw new IllegalArgumentException("missing return oracle");
        try {
            return new Observation(step.equals().test(step.call().invoke()), "Public result compared against independent oracle");
        } catch (Exception observed) {
            return new Observation(false, "Unexpected public exception: " + observed.getClass().getSimpleName());
        }
    }

    public static BatchResult execute(List<? extends ITestCase> cases) {
        if (cases.isEmpty()) throw new IllegalArgumentException("TestUnit requires nonempty Cases");
        Set<String> seen = new HashSet<>();
        List<CaseResult> results = new ArrayList<>();
        for (ITestCase testcase : cases) {
            String status = "INVALID_TEST", detail = "", case_id = testcase.case_id();
            int count = 0;
            List<StepResult> steps = new ArrayList<>();
            if (case_id == null || case_id.isBlank() || !seen.add(case_id)) {
                detail = "Case identity missing or duplicate";
            } else {
                try {
                    if (testcase.kind() == Kind.STATEFUL) {
                        if (testcase.construct() == null || testcase.steps() == null || testcase.steps().isEmpty())
                            throw new IllegalArgumentException("Stateful Case needs constructor and steps");
                        testcase.construct().invoke();
                        status = "PASS"; detail = "State sequence matched";
                        for (Step step : testcase.steps()) {
                            if (step.method() == null || step.method().isBlank() || step.method().startsWith("_"))
                                throw new IllegalArgumentException("Step must identify public method");
                            Observation observation = observe(step);
                            ++count;
                            steps.add(new StepResult(count, step.method(), observation.matched() ? "PASS" : "FAIL", observation.detail()));
                            if (!observation.matched()) {
                                status = "FAIL"; detail = observation.detail(); break;
                            }
                        }
                    } else {
                        Step invocation = testcase.invocation();
                        if (invocation == null || testcase.kind() == null ||
                            ((testcase.kind() == Kind.EXCEPTION) != (invocation.outcome() == Outcome.EXCEPTION)))
                            throw new IllegalArgumentException("Case kind and outcome do not agree");
                        Observation observation = observe(invocation);
                        count = 1; status = observation.matched() ? "PASS" : "FAIL";
                        detail = observation.detail();
                    }
                } catch (IllegalArgumentException invalid) {
                    status = "INVALID_TEST"; detail = invalid.getMessage();
                } catch (Exception publicFailure) {
                    status = "FAIL"; detail = "Public constructor raised: " + publicFailure.getClass().getSimpleName();
                }
            }
            results.add(new CaseResult(case_id, status, detail, count, List.copyOf(steps)));
        }
        return new BatchResult(List.copyOf(results));
    }

    public static BatchResult run(List<? extends ITestCase> cases) {
        BatchResult result = execute(cases);
        if (!result.succeeded()) throw new TestRunFailure(result);
        return result;
    }

    private static String quoted(String value) {
        Objects.requireNonNull(value, "string field");
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); ++i) {
            char c = value.charAt(i);
            if (c == '"' || c == '\\') out.append('\\').append(c);
            else if (c == '\n') out.append("\\n");
            else if (c == '\r') out.append("\\r");
            else if (c == '\t') out.append("\\t");
            else if (c < 0x20) out.append(String.format("\\u%04x", (int)c));
            else out.append(c);
        }
        return out.append('"').toString();
    }
}
