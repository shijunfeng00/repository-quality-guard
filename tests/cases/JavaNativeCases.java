import rqg.contracts.TestUnit;
import java.util.List;

public final class JavaNativeCases {
    static final class Counter {
        private int total = 0;
        int add(int n) { total += n; return total; }
        int reject() { throw new IllegalArgumentException("invalid transition"); }
    }

    public static void main(String[] ignored) {
        var equal = new TestUnit.Step("sum", TestUnit.Outcome.RETURN, () -> 3 + 7,
            actual -> actual.equals(10), null);
        var unequal = new TestUnit.Step("sum", TestUnit.Outcome.RETURN, () -> 3 + 7,
            actual -> actual.equals(11), null);
        var throwsExpected = new TestUnit.Step("reject", TestUnit.Outcome.EXCEPTION,
            () -> { throw new IllegalArgumentException("invalid"); }, null,
            error -> error instanceof IllegalArgumentException);
        var counter = new Counter();
        var stateful = new TestUnit.Case("stateful", TestUnit.Kind.STATEFUL, null,
            () -> { counter.total = 0; return counter; }, List.of(
                new TestUnit.Step("add", TestUnit.Outcome.RETURN, () -> counter.add(2),
                    actual -> actual.equals(2), null),
                new TestUnit.Step("reject", TestUnit.Outcome.EXCEPTION, counter::reject,
                    null, error -> error instanceof IllegalArgumentException),
                new TestUnit.Step("add", TestUnit.Outcome.RETURN, () -> counter.add(3),
                    actual -> actual.equals(5), null)));
        var all = TestUnit.execute(List.of(
            new TestUnit.Case("return", TestUnit.Kind.RETURN, equal, null, List.of()),
            new TestUnit.Case("bad-oracle", TestUnit.Kind.RETURN, unequal, null, List.of()),
            new TestUnit.Case("exception", TestUnit.Kind.EXCEPTION, throwsExpected, null, List.of()),
            new TestUnit.Case("instance", TestUnit.Kind.INSTANCE,
                new TestUnit.Step("new", TestUnit.Outcome.RETURN, Counter::new,
                    value -> ((Counter)value).total == 0, null), null, List.of()),
            stateful,
            new TestUnit.Case("return", TestUnit.Kind.RETURN, equal, null, List.of())));
        System.out.println(all.toJson());
        if (all.passed() != 4 || all.succeeded()) System.exit(2);
        try {
            TestUnit.run(List.of(new TestUnit.Case("bad-oracle", TestUnit.Kind.RETURN,
                unequal, null, List.of())));
            System.exit(3);
        } catch (TestUnit.TestRunFailure expected) {
            if (expected.report.passed() != 0) System.exit(4);
        }
    }
}
