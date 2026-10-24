import rqg.contracts.TestUnit;
import java.util.List;

public final class EquivalentNativeCases {
    static final class Counter {
        int value;
        int add(int amount) { value += amount; return value; }
        int reject() { throw new IllegalArgumentException("invalid transition"); }
    }

    public static void main(String[] ignored) {
        var returnPass = new TestUnit.Step("sum", TestUnit.Outcome.RETURN, () -> 3 + 7,
            actual -> actual.equals(10), null);
        var returnFail = new TestUnit.Step("sum", TestUnit.Outcome.RETURN, () -> 3 + 7,
            actual -> actual.equals(11), null);
        var exceptionPass = new TestUnit.Step("reject", TestUnit.Outcome.EXCEPTION,
            () -> { throw new IllegalArgumentException("invalid transition"); }, null,
            error -> error instanceof IllegalArgumentException);
        var instancePass = new TestUnit.Step("construct", TestUnit.Outcome.RETURN,
            Counter::new, actual -> actual instanceof Counter && ((Counter) actual).value == 0, null);
        var counter = new Counter();
        var stateful = new TestUnit.Case("stateful-pass", TestUnit.Kind.STATEFUL, null,
            () -> { counter.value = 0; return counter; }, List.of(
                new TestUnit.Step("add", TestUnit.Outcome.RETURN, () -> counter.add(2),
                    actual -> actual.equals(2), null),
                new TestUnit.Step("reject", TestUnit.Outcome.EXCEPTION, counter::reject,
                    null, error -> error instanceof IllegalArgumentException),
                new TestUnit.Step("add", TestUnit.Outcome.RETURN, () -> counter.add(3),
                    actual -> actual.equals(5), null),
                new TestUnit.Step("add", TestUnit.Outcome.RETURN, () -> counter.add(1),
                    actual -> actual.equals(6), null)));
        var duplicate = new TestUnit.Step("value", TestUnit.Outcome.RETURN, () -> 7,
            actual -> actual.equals(7), null);
        var results = TestUnit.execute(List.of(
            new TestUnit.Case("return-pass", TestUnit.Kind.RETURN, returnPass, null, List.of()),
            new TestUnit.Case("return-fail", TestUnit.Kind.RETURN, returnFail, null, List.of()),
            new TestUnit.Case("exception-pass", TestUnit.Kind.EXCEPTION, exceptionPass, null, List.of()),
            new TestUnit.Case("instance-pass", TestUnit.Kind.INSTANCE, instancePass, null, List.of()),
            stateful,
            new TestUnit.Case("duplicate-id", TestUnit.Kind.RETURN, duplicate, null, List.of()),
            new TestUnit.Case("duplicate-id", TestUnit.Kind.RETURN, duplicate, null, List.of())));
        System.out.println(results.toJson());
    }
}
