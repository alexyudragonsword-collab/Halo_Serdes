#!/usr/bin/env bash
# Run the instrumented tests, then say in the log what actually ran.
#
# This lives in a file rather than inline in the workflow on purpose:
# reactivecircus/android-emulator-runner executes its `script` input via
# `sh -c "<script>"`, so any double quote inside it closes that wrapper early
# and the command becomes malformed. The inline version failed deterministically
# in ~90 s having produced no test report at all.
#
# Two things the raw Gradle output does not give you:
#
#  * The assertion messages on failure. Gradle names only the HTML report, which
#    lives in a CI artifact; when that artifact cannot be opened, a red build is
#    undiagnosable from the log.
#  * The test count on success. `connectedDebugAndroidTest` is perfectly happy
#    to pass having run nothing at all — a green tick that means "the suite did
#    not execute" is worse than a red one, so zero tests is treated as failure
#    here.
set -u

cd "$(dirname "$0")/.." || exit 1
RESULTS=app/build/outputs/androidTest-results/connected

./gradlew --no-daemon connectedDebugAndroidTest
status=$?

if [ "$status" -ne 0 ]; then
    echo "::group::Instrumented test detail"
    find "$RESULTS" -name '*.xml' -print -exec cat {} \; 2>/dev/null \
        || echo "no test XML at all - the tests never ran"
    echo "::endgroup::"
fi

python3 - "$RESULTS" <<'XML_SUMMARY'
import collections, glob, sys, xml.etree.ElementTree as ET

# Group by each <testcase>'s classname rather than by the <testsuite> root:
# AGP writes ONE xml per device whose root aggregates every class, so reading
# the root's name attribute reports the whole run under a single arbitrary
# class. (Observed once: 9 tests all labelled LinkFacadeTest.) The totals were
# right but the breakdown was a lie, which is worse than no breakdown at all.
per_class = collections.Counter()
bad = collections.Counter()
total = fails = errors = skipped = 0

for path in glob.glob(sys.argv[1] + "/**/*.xml", recursive=True):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        continue
    total += int(root.get("tests", 0))
    fails += int(root.get("failures", 0))
    errors += int(root.get("errors", 0))
    skipped += int(root.get("skipped", 0))
    for case in root.iter("testcase"):
        name = case.get("classname") or "?"
        per_class[name] += 1
        if case.find("failure") is not None or case.find("error") is not None:
            bad[name] += 1

for name, n in sorted(per_class.items()):
    print(f"  {name}: {n} tests, {bad[name]} failed")
summary = (f"instrumented totals: {total} tests, {fails} failures, "
           f"{errors} errors, {skipped} skipped")
print(summary)

# Also as a workflow annotation. The plain line above sits a few hundred lines
# deep in a log dominated by emulator boot messages and Gradle cache chatter,
# which makes "how many tests actually ran?" expensive to answer after the
# fact — and that number is the whole point of this script. An annotation is
# attached to the run itself and readable without the log.
detail = " | ".join(f"{n.rsplit('.', 1)[-1]} {c}/{bad[n]}f"
                    for n, c in sorted(per_class.items()))
level = "error" if (fails or errors or total == 0) else "notice"
print(f"::{level} title=Instrumented tests::{summary}"
      + (f" -- {detail}" if detail else ""))
if total == 0:
    print("NO TESTS RAN - treating as failure; a green tick here would mean "
          "the suite never executed")
    sys.exit(1)
XML_SUMMARY
count_status=$?

[ "$status" -ne 0 ] && exit "$status"
exit "$count_status"
