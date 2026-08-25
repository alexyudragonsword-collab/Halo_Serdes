#!/usr/bin/env bash
# Build the Cython-compiled wheels the compiled APK variant installs.
#
#   android/tools/build_compiled_wheels.sh arm64-v8a x86_64
#
# Writes one merged wheel per ABI into android/app/pysrc/, which is where the
# Gradle pip block looks (by --find-links, so pip matches on the wheel *tag*;
# installing a wheel by path skips ABI matching entirely and cheerfully puts
# the arm64 wheel into the x86_64 build, where it fails at import with an ELF
# error that points nowhere near here).
#
# Requires an NDK: $ANDROID_NDK_HOME, or $NDK passed in the environment.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
out="$root/android/app/pysrc"
work="${WORK:-$root/build-android-wheel}"

# The Chaquopy CPython build to compile against. Not a preference: it must be
# the build Chaquopy itself downloads for the pinned pythonVersion, because
# Cython's generated C reaches into CPython's internal headers. CI asserts this
# against ~/.gradle/caches/.../com.chaquo.python/target/ after the APK builds,
# so drift shows up as a red job naming both versions rather than as a crash on
# a device.
target="${CHAQUOPY_TARGET:-$(grep -E '^chaquopyTarget=' "$root/android/gradle.properties" | cut -d= -f2)}"

# The compile set is an assertion: that the suite still passes with these .py
# files deleted. It is verified on a host by installing the merged wheel into a
# venv and running the whole suite -- 356 passed / 1 skipped, matching the
# interpreted baseline. See cairn/android-compiled-variant.md.
#
# Everything not listed is excluded for a stated reason:
#   config/loader.py  -- Cython 3.3.0 crashes on it (TypeError in
#                        generate_keyvalue_args, from the **{k: v} call in
#                        dataclasses.replace). Not this project's bug.
#   the six numba modules (cdr/adc_kernel, cdr/kernels, core/prbs,
#                        dsp/fixed_datapath, dsp/kernels, dsp/mlsd)
#                     -- each wraps numba.njit(...)(_py_fn) in
#                        `try/except ImportError`. numba cannot njit a cython
#                        function and what it raises is not ImportError, so
#                        that guard does not catch it: compiling them turns the
#                        host suite red. Ironclad rule #4 -- numba is the
#                        performance layer, not the correctness layer -- is
#                        exactly this boundary.
CORE_SPEC="__init__,afe,analysis,channel,engine,io,tx,fec"
CORE_SPEC="$CORE_SPEC,config/__init__,config/schema,cdr/__init__"
CORE_SPEC="$CORE_SPEC,core/__init__,core/fixed,core/mapping,core/waveform"
CORE_SPEC="$CORE_SPEC,dsp/__init__,dsp/ffe"

ndk="${ANDROID_NDK_HOME:-${NDK:-}}"
[ -d "$ndk" ] || { echo "no NDK: set ANDROID_NDK_HOME" >&2; exit 2; }

mkdir -p "$out"
for abi in "$@"; do
    echo "=== $abi (target CPython $target) ==="
    # A fresh work tree per ABI. Reusing one leaves the previous ABI's .so
    # beside the sources, and the script's own "no .py left behind" check
    # passes on them -- the wheel would then carry the wrong architecture and
    # only fail at import on a device.
    rm -rf "$work"
    python "$here/android_wheel.py" --project "$root" \
        --package halo_serdes --compile "$CORE_SPEC" \
        --abi "$abi" --ndk "$ndk" --target-version "$target" \
        --work "$work" --outdir "$work/core"
    python "$here/android_wheel.py" --project "$root" \
        --package halo_serdes_app --compile all \
        --abi "$abi" --ndk "$ndk" --target-version "$target" \
        --work "$work" --outdir "$work/app"

    whl=$(basename "$(ls "$work"/core/*.whl)")
    python "$here/merge_wheels.py" --out "$out/$whl" \
        "$work"/core/*.whl "$work"/app/*.whl
done

echo
echo "=== $out ==="
ls -la "$out"
