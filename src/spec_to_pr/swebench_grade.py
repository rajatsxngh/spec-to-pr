"""Grades one SWE-bench Live instance from its test log, the way the official harness does.

This file is copied into each imported task's hidden/ directory and runs inside the instance's
Docker image, so it uses only the standard library and works on any Python 3.

    python swebench_grade.py <testlog> <tests.json>

tests.json holds the instance's FAIL_TO_PASS and PASS_TO_PASS lists. The log comes from the
instance's own test_cmds (which use `pytest -rA`). The parser and the resolve rule follow
microsoft/SWE-bench-Live evaluation/evaluation.py: `parse_log_pytest` + `default_pytest_parser`,
and resolved = every FAIL_TO_PASS test passes and no PASS_TO_PASS test fails. As in the official
harness, a PASS_TO_PASS test missing from the log does not count as a failure.

Exit code 0 when resolved, 1 when not.
"""

import json
import sys

STATUSES = ("FAILED", "PASSED", "SKIPPED", "ERROR", "XFAIL")


def parse_log(log):
    """Map test id -> 'pass' | 'fail' | 'skip' from `pytest -rA` short-summary lines."""
    raw = {}
    for line in log.split("\n"):
        if any(line.startswith(s) for s in STATUSES):
            if line.startswith("FAILED"):
                line = line.replace(" - ", " ")
            parts = line.split()
            if len(parts) <= 1:
                continue
            raw[parts[1]] = parts[0]
    status = {}
    for test, s in raw.items():
        if s.upper() == "XFAIL":
            status[test] = "pass"
        elif "pass" in s.lower():
            status[test] = "pass"
        elif "skip" in s.lower():
            status[test] = "skip"
        else:
            status[test] = "fail"
    return status


def evaluate(status, fail_to_pass, pass_to_pass):
    passed = {t for t, s in status.items() if s == "pass"}
    failed = {t for t, s in status.items() if s == "fail"}
    f2p_ok = sorted(passed & set(fail_to_pass))
    f2p_bad = sorted(failed & set(fail_to_pass))
    p2p_bad = sorted(failed & set(pass_to_pass))
    all_f2p = set(fail_to_pass) <= set(f2p_ok) or len(f2p_ok) == len(fail_to_pass)
    return {
        "resolved": all_f2p and not f2p_bad and not p2p_bad,
        "fail_to_pass_passed": len(f2p_ok),
        "fail_to_pass_total": len(fail_to_pass),
        "fail_to_pass_not_passing": sorted(set(fail_to_pass) - set(f2p_ok)),
        "pass_to_pass_failed": p2p_bad,
        "pass_to_pass_total": len(pass_to_pass),
        "tests_in_log": len(status),
    }


def main(argv):
    with open(argv[1], encoding="utf-8", errors="replace") as f:
        log = f.read()
    with open(argv[2], encoding="utf-8") as f:
        tests = json.load(f)
    report = evaluate(parse_log(log), tests["FAIL_TO_PASS"], tests["PASS_TO_PASS"])
    print("SWE-bench Live grade: " + ("RESOLVED" if report["resolved"] else "NOT RESOLVED"))
    print("FAIL_TO_PASS passing: %d/%d" % (report["fail_to_pass_passed"], report["fail_to_pass_total"]))
    print("PASS_TO_PASS failing: %d of %d" % (len(report["pass_to_pass_failed"]), report["pass_to_pass_total"]))
    print("tests found in log: %d" % report["tests_in_log"])
    for t in report["fail_to_pass_not_passing"][:20]:
        print("  FAIL_TO_PASS not passing: " + t)
    for t in report["pass_to_pass_failed"][:20]:
        print("  PASS_TO_PASS failed: " + t)
    return 0 if report["resolved"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
