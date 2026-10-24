#ifndef RQG_C_TESTUNIT_H
#define RQG_C_TESTUNIT_H

/* A native C11 adapter for return and instance effects. C has no language
 * exceptions or reflection; unsupported Case kinds are INCONCLUSIVE. */
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { RQG_RETURN, RQG_EXCEPTION, RQG_INSTANCE, RQG_STATEFUL } rqg_kind;
typedef int (*rqg_call)(void *context);
typedef int (*rqg_oracle)(int expected, int actual);

typedef struct {
    const char *method;
    rqg_kind outcome;
    rqg_call call;
    rqg_oracle equals;
    int expected;
} rqg_step;

typedef struct {
    const char *case_id;
    rqg_kind kind;
    rqg_step invocation;
    rqg_call construct;
    const rqg_step *steps;
    size_t step_count;
    void *context;
} rqg_case;

typedef struct {
    size_t passed;
    size_t total;
    size_t inconclusive;
} rqg_summary;

static void rqg_print_string(FILE *stream, const char *value) {
    const unsigned char *p = (const unsigned char *)value;
    static const char hex[] = "0123456789abcdef";
    fputc('"', stream);
    for (; *p; ++p) {
        if (*p == '"' || *p == '\\') { fputc('\\', stream); fputc(*p, stream); }
        else if (*p == '\n') fputs("\\n", stream);
        else if (*p == '\r') fputs("\\r", stream);
        else if (*p == '\t') fputs("\\t", stream);
        else if (*p < 0x20) {
            fputs("\\u00", stream);
            fputc(hex[*p >> 4], stream);
            fputc(hex[*p & 15], stream);
        } else fputc(*p, stream);
    }
    fputc('"', stream);
}

static const char *rqg_observe(const rqg_step *step, void *context) {
    if (step->outcome == RQG_EXCEPTION) return "INCONCLUSIVE";
    if (step->outcome != RQG_RETURN || !step->call || !step->equals)
        return "INVALID_TEST";
    return step->equals(step->expected, step->call(context)) ? "PASS" : "FAIL";
}

static rqg_summary rqg_execute(const rqg_case *cases, size_t count, FILE *stream) {
    rqg_summary result = {0, count, 0};
    fputs("{\"schema_version\":\"rqg.testunit.result.v1\",\"cases\":[", stream);
    for (size_t i = 0; i < count; ++i) {
        const rqg_case *testcase = &cases[i];
        const char *status = "INVALID_TEST", *detail = "Invalid public Case contract";
        size_t executed = 0;
        int duplicate = !testcase->case_id || !testcase->case_id[0];
        for (size_t j = 0; j < i && !duplicate; ++j) {
            const char *older = cases[j].case_id;
            if (older && strcmp(older, testcase->case_id) == 0) duplicate = 1;
        }
        if (duplicate) detail = "Case identity missing or duplicate";
        else if (testcase->kind == RQG_EXCEPTION || testcase->invocation.outcome == RQG_EXCEPTION) {
            status = "INCONCLUSIVE"; detail = "C does not support language exceptions";
        } else if (testcase->kind == RQG_STATEFUL) {
            status = "INCONCLUSIVE";
            detail = "C Stateful Case requires an explicit sequence provider";
        } else if (testcase->kind == RQG_RETURN || testcase->kind == RQG_INSTANCE) {
            status = rqg_observe(&testcase->invocation, testcase->context);
            detail = "Public return compared against independent oracle";
            executed = strcmp(status, "INVALID_TEST") != 0;
        }
        if (i) fputc(',', stream);
        fputs("{\"case_id\":", stream);
        rqg_print_string(stream, testcase->case_id ? testcase->case_id : "");
        fputs(",\"status\":", stream); rqg_print_string(stream, status);
        fputs(",\"detail\":", stream); rqg_print_string(stream, detail);
        fprintf(stream, ",\"steps_executed\":%zu,\"steps\":[]}", executed);
        if (strcmp(status, "PASS") == 0) ++result.passed;
        if (strcmp(status, "INCONCLUSIVE") == 0) ++result.inconclusive;
    }
    fprintf(stream, "],\"passed\":%zu,\"succeeded\":%s}", result.passed,
            count && result.passed == count ? "true" : "false");
    return result;
}

#ifdef __cplusplus
}
#endif
#endif
