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
#
# It also pulls the screenshots UiRenderTest captures. Those are evidence for a
# human, not an oracle: the assertions in that class are what fail the build.
set -u

cd "$(dirname "$0")/.." || exit 1
RESULTS=app/build/outputs/androidTest-results/connected

# Everything worth reading is also written here, and the workflow cats this
# file as its very last step.
#
# Why: this script's output lands ahead of ~120 lines of emulator teardown,
# Gradle cache writes and git cleanup, and reading a job log from the outside
# means reading its tail. Four separate attempts to fetch a window containing
# the totals landed beside them. Printing the same few lines again at the end
# of the job costs nothing and ends that whole class of problem.
SUMMARY=app/build/outputs/ci-summary.txt
mkdir -p "$(dirname "$SUMMARY")"
: > "$SUMMARY"
say() { echo "$*"; echo "$*" >> "$SUMMARY"; }

./gradlew --no-daemon connectedDebugAndroidTest
status=$?

# Screenshots the UI tests captured. Pulled whether or not the run passed:
# a failed render is exactly when you want to look at one.
#
# getExternalFilesDir, not filesDir — adb can read the former without root.
# `|| true` throughout because a build that failed before any test ran has no
# screenshots to pull, and that is not a second failure.
# Screenshots come off the device through TestStorage, which AGP drains while
# the app is still installed. Nothing is pulled here.
#
# Two earlier versions did pull, from internal storage via `run-as` and from
# the external files dir via `adb pull`, and both got nothing for the same
# reason: connectedAndroidTest uninstalls both APKs when it finishes, so by
# the time this script ran the package and all its storage were gone. The
# diagnostics said exactly that once they were printed somewhere readable —
# "run-as: unknown package" and "No such file or directory".
SHOTS=app/build/outputs/screenshots
mkdir -p "$SHOTS"
find app/build/outputs -path '*additional_output*' -name '*.png' \
    -exec cp {} "$SHOTS/" \; 2>/dev/null || true
n_shots=$(find "$SHOTS" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
say "screenshots captured: $n_shots"
if [ "$n_shots" = "0" ]; then
    say "no screenshots; additional_output holds: $(find app/build/outputs \
        -path '*additional_output*' 2>/dev/null | head -5 | tr '\n' ' ')"
fi

if [ "$status" -ne 0 ]; then
    echo "::group::Instrumented test detail"
    find "$RESULTS" -name '*.xml' -print -exec cat {} \; 2>/dev/null \
        || echo "no test XML at all - the tests never ran"
    echo "::endgroup::"
fi

python3 - "$RESULTS" <<'XML_SUMMARY' | tee -a "$SUMMARY"
import collections, glob, sys, xml.etree.ElementTree as ET

# Group by each <testcase>'s classname rather than by the <testsuite> root:
# AGP writes ONE xml per device whose root aggregates every class, so reading
# the root's name attribute reports the whole run under a single arbitrary
# class. (Observed once: 9 tests all labelled LinkFacadeTest.) The totals were
# right but the breakdown was a lie, which is worse than no breakdown at all.
per_class = collections.Counter()
bad = collections.Counter()
total = fails = errors = skipped = 0
failures = []

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
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is not None:
            bad[name] += 1
            failures.append((f"{name}#{case.get('name')}", problem.text or ""))

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

# Then the failures themselves, compactly, as the last thing this step says.
#
# The raw XML dump above is thorough and unreachable: it is thousands of lines
# deep in a log whose tail is all emulator teardown, so reading "why did it
# fail" cost several round trips of guessing. What is actually wanted is the
# assertion message, and it fits on a line or two.
if failures:
    print("::group::Failures")
    for name, msg in failures:
        first = " / ".join(l.strip() for l in msg.strip().splitlines()[:3] if l.strip())
        print(f"  FAILED {name}")
        print(f"         {first[:400]}")
    print("::endgroup::")
if total == 0:
    print("NO TESTS RAN - treating as failure; a green tick here would mean "
          "the suite never executed")
    sys.exit(1)
XML_SUMMARY
count_status=${PIPESTATUS[0]}

[ "$status" -ne 0 ] && exit "$status"
exit "$count_status"
