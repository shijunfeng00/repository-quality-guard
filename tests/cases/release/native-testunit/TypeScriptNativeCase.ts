import { Case, ITestCase, Step } from "../../../../runtime/contracts/typescript/testunit";

const step: Step = {
  method: "add",
  outcome: "RETURN",
  invoke: () => 7,
  equals: (observed: unknown) => observed === 7,
};
const testcase: ITestCase = new Case("sum-7", "RETURN", step);
const result = testcase.get_invocation().equals(testcase.get_invocation().invoke());
if (testcase.get_case_id() !== "sum-7" || testcase.get_kind() !== "RETURN" || !result) {
  throw new Error("native TypeScript Case contract violated");
}
console.log(JSON.stringify({ case_id: testcase.get_case_id(), kind: testcase.get_kind(), matched: result }));
