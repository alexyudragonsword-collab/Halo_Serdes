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

python3 - "$RESULTS" <<'PY'
import glob, sys, xml.etree.ElementTree as ET

files = glob.glob(sys.argv[1] + "/**/*.xml", recursive=True)
total = fails = errors = skipped = 0
for path in files:
    try:
        r = ET.parse(path).getroot()
    except ET.ParseError:
        continue
    n = int(r.get("tests", 0))
    total += n
    fails += int(r.get("failures", 0))
    errors += int(r.get("errors", 0))
    skipped += int(r.get("skipped", 0))
    print(f"  {r.get('name')}: {n} tests, "
          f"{r.get('failures')} failures, {r.get('errors')} errors")

print(f"instrumented totals: {total} tests, {fails} failures, "
      f"{errors} errors, {skipped} skipped")
if total == 0:
    print("NO TESTS RAN - treating as failure; a green tick here would mean "
          "the suite never executed")
    sys.exit(1)
PY
count_status=$?

[ "$status" -ne 0 ] && exit "$status"
exit "$count_status"
