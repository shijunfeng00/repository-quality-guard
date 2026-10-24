/**
 * TypeScript native Case declaration for RQG TestUnit migration.
 * The TypeScript compiler enforces this interface under --strict.
 * This contract does not yet implement a TypeScript runner or a QG AST rule.
 */
export type Kind = "RETURN" | "EXCEPTION" | "INSTANCE" | "STATEFUL";
export type Outcome = "RETURN" | "EXCEPTION";

/** An invocation and independent expected-output predicate. */
export interface Step {
  readonly method: string;
  readonly outcome: Outcome;
  readonly invoke: () => unknown;
  readonly equals: (observed: unknown) => boolean;
}

/** Compile-time required methods, matching the C++ / Java native Case contract. */
export interface ITestCase {
  get_case_id(): string;
  get_kind(): Kind;
  get_invocation(): Step;
  get_constructor(): (() => unknown) | null;
  get_steps(): readonly Step[];
}

/** Concrete provider of the required contract; no legacy Step runner. */
export class Case implements ITestCase {
  constructor(
    private readonly case_id: string,
    private readonly kind: Kind,
    private readonly invocation: Step,
    private readonly construct: (() => unknown) | null = null,
    private readonly steps: readonly Step[] = [],
  ) {
    if (!case_id.trim()) throw new TypeError("case_id must not be empty");
  }
  get_case_id(): string { return this.case_id; }
  get_kind(): Kind { return this.kind; }
  get_invocation(): Step { return this.invocation; }
  get_constructor(): (() => unknown) | null { return this.construct; }
  get_steps(): readonly Step[] { return this.steps; }
}
