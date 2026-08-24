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

./gradlew --no-daemon connectedDebugAndroidTest
status=$?

# Screenshots the UI tests captured. Pulled whether or not the run passed:
# a failed render is exactly when you want to look at one.
#
# getExternalFilesDir, not filesDir — adb can read the former without root.
# `|| true` throughout because a build that failed before any test ran has no
# screenshots to pull, and that is not a second failure.
SHOTS=app/build/outputs/screenshots
PKG=com.halo.serdes.probe
mkdir -p "$SHOTS"
# One file at a time through `run-as`, rather than `adb pull`.
#
# The first version pulled from /sdcard/Android/data/$PKG/files. Four
# screenshots were written there and none came back: on API 30+ that path goes
# through the scoped-storage FUSE layer, where adb pull does not reliably work.
# `run-as` reaches a debuggable app's own directory with no such layer in the
# way, and `cat` avoids depending on tar being present in that environment.
for f in $(adb exec-out run-as "$PKG" ls files/screenshots 2>/dev/null | tr -d '\r'); do
    adb exec-out run-as "$PKG" cat "files/screenshots/$f" > "$SHOTS/$f" 2>/dev/null \
        || rm -f "$SHOTS/$f"
done
n_shots=$(find "$SHOTS" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
echo "screenshots captured: $n_shots"
if [ "$n_shots" = "0" ]; then
    # Zero is ambiguous on its own — never written, or written somewhere this
    # could not reach. One listing settles it, and the first run without it
    # cost a round trip.
    echo "no screenshots pulled; what the device has under the app's dir:"
    adb exec-out run-as "$PKG" ls -lR files 2>&1 | head -20
fi

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
count_status=$?

[ "$status" -ne 0 ] && exit "$status"
exit "$count_status"
