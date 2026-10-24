package testunit

import "testing"

func TestRequiredMethodsAndCaseIdentity(t *testing.T) {
	step := Step{Method: "add", Outcome: ReturnOutcome,
		Invoke: func() (any, error) { return 7, nil },
		Equals: func(v any) bool { return v == 7 },
	}
	var testcase ITestCase = &Case{CaseID: "sum-7", Type: Return, Invocation: step}
	if testcase.GetCaseID() != "sum-7" || testcase.GetKind() != Return {
		t.Fatal("interface accessors lost Case identity or kind")
	}
	actual, err := testcase.GetInvocation().Invoke()
	if err != nil || !testcase.GetInvocation().Equals(actual) {
		t.Fatal("native provider lost independent expected result")
	}
	if testcase.GetConstructor() != nil || len(testcase.GetSteps()) != 0 {
		t.Fatal("unexpected stateful construction")
	}
}
