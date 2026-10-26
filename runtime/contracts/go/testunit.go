// Package testunit provides the Go compile-time Case interface for the
// future multi-language IR. It does not implement a TestUnit runner or QG rule.
package testunit

// Kind describes the public-behavior case; unsupported execution is not PASS.
type Kind string

const (
	Return    Kind = "RETURN"
	Exception Kind = "EXCEPTION"
	Instance  Kind = "INSTANCE"
	Stateful  Kind = "STATEFUL"
)

type Outcome string

const (
	ReturnOutcome    Outcome = "RETURN"
	ExceptionOutcome Outcome = "EXCEPTION"
)

// Step binds a public invocation to an independent expectation.
type Step struct {
	Method  string
	Outcome Outcome
	Invoke  func() (any, error)
	Equals  func(any) bool
}

// ITestCase is satisfied only by types implementing every required method.
type ITestCase interface {
	GetCaseID() string
	GetKind() Kind
	GetInvocation() Step
	GetConstructor() func() (any, error)
	GetSteps() []Step
}

// Case is a concrete provider with a language-native execution contract.
type Case struct {
	CaseID      string `json:"case_id"`
	Type        Kind
	Invocation  Step
	Constructor func() (any, error)
	Steps       []Step
}

var _ ITestCase = (*Case)(nil)

func (c *Case) GetCaseID() string                   { return c.CaseID }
func (c *Case) GetKind() Kind                       { return c.Type }
func (c *Case) GetInvocation() Step                 { return c.Invocation }
func (c *Case) GetConstructor() func() (any, error) { return c.Constructor }
func (c *Case) GetSteps() []Step                    { return c.Steps }
