#!/usr/bin/env bash
# Run the instrumented tests and, on failure, print each test's XML.
#
# This lives in a file rather than inline in the workflow on purpose:
# reactivecircus/android-emulator-runner executes its `script` input via
# `sh -c "<script>"`, so any double quote inside it closes that wrapper early
# and the command becomes malformed. The inline version of this logic failed
# deterministically in ~90 s with no test report produced at all — the
# emulator never got as far as running anything. Keeping `script:` down to a
# single quote-free invocation avoids the whole class of problem.
#
# Why print the XML at all: Gradle only names the HTML report, which lives in
# a CI artifact. When that artifact cannot be opened, a red build is
# undiagnosable from the log — the assertion messages never reach anyone.
set -u

cd "$(dirname "$0")/.." || exit 1

./gradlew --no-daemon connectedDebugAndroidTest && exit 0

status=$?
echo "::group::Instrumented test detail"
find app/build/outputs/androidTest-results -name '*.xml' -print -exec cat {} \; \
    || echo "no test XML was produced - the tests never ran"
echo "::endgroup::"
exit "$status"
