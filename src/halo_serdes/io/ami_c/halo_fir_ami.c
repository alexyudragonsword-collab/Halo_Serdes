/* halo_serdes reference IBIS-AMI model: a UI-spaced FIR equalizer.
 *
 * A REAL, compilable IBIS-AMI shared object implementing the three C entry
 * points from the IBIS spec (AMI_Init / AMI_GetWave / AMI_Close). It is the
 * dependency-free counterpart to a vendor .so: the framework loads and *runs*
 * it through the actual AMI C ABI (see io/ami.py::AmiCModel), so the seam is
 * exercised end-to-end without pyibisami or a proprietary model.
 *
 * Model: a FIR with UI-spaced taps expanded onto the oversampled grid
 * (osr = bit_time / sample_interval). Init realizes it as an LTI transform of
 * the channel impulse response; GetWave applies the identical FIR to a
 * time-domain block. Both keep the input length and align the main tap so the
 * cursor stays put (the leading n_pre*osr precursor delay is shifted out) — so
 * GetWave on an impulse reproduces the Init transform.
 *
 * Parameters are read from the AMI input-parameter string, e.g.
 *   (halo_fir (taps -0.1 0.8 -0.1) (n_pre 1))
 *
 * Build:  cc -shared -fPIC -O2 -o halo_fir_ami.so halo_fir_ami.c -lm
 */

#include <stdlib.h>
#include <string.h>
#include <stdio.h>

typedef struct {
    double *gtaps;   /* grid-expanded taps                     */
    long    glen;    /* length of gtaps                        */
    long    pre;     /* grid precursor offset (n_pre * osr)     */
} halo_state;

/* single-threaded model: static out-parameter buffers are fine */
static char g_msg[160];
static char g_out[32];

/* Read whitespace-separated doubles following the first occurrence of `key`,
 * stopping at ')' or end of string. Returns the count written. */
static long parse_doubles(const char *s, const char *key,
                          double *out, long maxn) {
    const char *p = s ? strstr(s, key) : NULL;
    long n = 0;
    if (!p) return 0;
    p += strlen(key);
    while (*p && *p != ')' && n < maxn) {
        char *end;
        double v = strtod(p, &end);
        if (end == p) { p++; continue; }   /* not a number here -> advance */
        out[n++] = v;
        p = end;
    }
    return n;
}

static long parse_int(const char *s, const char *key, long dflt) {
    const char *p = s ? strstr(s, key) : NULL;
    if (!p) return dflt;
    return strtol(p + strlen(key), NULL, 10);
}

/* Build the grid-expanded FIR from the AMI parameter string. */
static halo_state *build_state(const char *params, double sample_interval,
                               double bit_time) {
    double taps[64];
    long ntaps = parse_doubles(params, "taps", taps, 64);
    if (ntaps <= 0) { taps[0] = 1.0; ntaps = 1; }
    long n_pre = parse_int(params, "n_pre", 0);
    long osr = (long)(bit_time / sample_interval + 0.5);
    if (osr < 1) osr = 1;

    long glen = (ntaps - 1) * osr + 1;
    halo_state *st = (halo_state *)calloc(1, sizeof(halo_state));
    st->gtaps = (double *)calloc(glen, sizeof(double));
    st->glen = glen;
    st->pre = n_pre * osr;
    for (long i = 0; i < ntaps; i++) st->gtaps[i * osr] = taps[i];

    snprintf(g_msg, sizeof(g_msg),
             "halo_serdes reference FIR AMI: %ld taps, osr=%ld, n_pre=%ld",
             ntaps, osr, n_pre);
    return st;
}

/* full[i] = sum_k g[k] * x[i-k]; write out[n] = full[n + pre] for n in [0,N),
 * keeping length N. Leading `pre` grid samples (the precursor delay) are shifted
 * out; trailing samples beyond the convolution are zero. */
static void fir_apply(const halo_state *st, const double *x, double *out,
                      long N) {
    long glen = st->glen, pre = st->pre;
    const double *g = st->gtaps;
    for (long n = 0; n < N; n++) {
        double acc = 0.0;
        long m = n + pre;                  /* index into the full convolution */
        long kmax = (m < glen - 1) ? m : (glen - 1);
        for (long k = 0; k <= kmax; k++) {
            long xi = m - k;
            if (xi < N) acc += g[k] * x[xi];
        }
        out[n] = acc;
    }
}

long AMI_Init(double *impulse_matrix, long row_size, long aggressors,
              double sample_interval, double bit_time,
              char *ami_parameters_in, char **ami_parameters_out,
              void **ami_memory_handle, char **msg) {
    (void)aggressors;                      /* victim-only reference model */
    halo_state *st = build_state(ami_parameters_in, sample_interval, bit_time);

    double *in = (double *)malloc(row_size * sizeof(double));
    memcpy(in, impulse_matrix, row_size * sizeof(double));
    fir_apply(st, in, impulse_matrix, row_size);   /* LTI: transform impulse */
    free(in);

    *ami_memory_handle = st;
    strcpy(g_out, "(halo_fir)");
    *ami_parameters_out = g_out;
    *msg = g_msg;
    return 1;                              /* 1 = success (IBIS-AMI) */
}

long AMI_GetWave(double *wave, long wave_size, double *clock_times,
                 char **ami_parameters_out, void *ami_memory_handle) {
    halo_state *st = (halo_state *)ami_memory_handle;
    if (!st) return 0;
    double *in = (double *)malloc(wave_size * sizeof(double));
    memcpy(in, wave, wave_size * sizeof(double));
    fir_apply(st, in, wave, wave_size);    /* same FIR on a time-domain block */
    free(in);
    if (clock_times) clock_times[0] = -1.0;   /* FIR emits no recovered clock */
    if (ami_parameters_out) *ami_parameters_out = g_out;
    return 1;
}

long AMI_Close(void *ami_memory_handle) {
    halo_state *st = (halo_state *)ami_memory_handle;
    if (st) { free(st->gtaps); free(st); }
    return 1;
}
