#include "testunit.h"
#include <stdio.h>

struct counter { int value; };
static int add_seven(void *context) { return ((struct counter *)context)->value + 7; }
static int equal_int(int expected, int observed) { return expected == observed; }
static int init_counter(void *context) { ((struct counter *)context)->value = 10; return 10; }

int main(void) {
    struct counter counter = {3};
    const rqg_case cases[] = {
        {"return", RQG_RETURN, {"add", RQG_RETURN, add_seven, equal_int, 10}, NULL, NULL, 0, &counter},
        {"bad-oracle", RQG_RETURN, {"add", RQG_RETURN, add_seven, equal_int, 11}, NULL, NULL, 0, &counter},
        {"exception-unsupported", RQG_EXCEPTION, {"throw", RQG_EXCEPTION, NULL, NULL, 0}, NULL, NULL, 0, &counter},
        {"stateful-unsupported", RQG_STATEFUL, {"add", RQG_RETURN, add_seven, equal_int, 10}, NULL, NULL, 0, &counter},
        {"return", RQG_RETURN, {"add", RQG_RETURN, add_seven, equal_int, 10}, NULL, NULL, 0, &counter},
        {"instance", RQG_INSTANCE, {"init", RQG_RETURN, init_counter, equal_int, 10}, NULL, NULL, 0, &counter}
    };
    rqg_summary result = rqg_execute(cases, sizeof(cases)/sizeof(cases[0]), stdout);
    putchar('\n');
    return (result.passed == 2 && result.inconclusive == 2 && result.total == 6) ? 0 : 1;
}
