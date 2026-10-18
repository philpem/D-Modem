/* Optional observation wrappers; reconstructed DSP translation units are unchanged.
 * The real functions are renamed in COPIES of their object files at link time.
 * Files contain little-endian float32 rows: tx, raw input, corrected input,
 * residual, flags. Corrected input is immediately before the echo filter sum.
 * V.34 is 9600 samples/s. V.92 reports sample counts, with no assumed rate.
 */
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <stdint.h>
#include <string.h>
#include "dsplib/v34fsk.h"
#include "dsplib/v34rx.h"
#include "dsplib/V92EchoCanceller.h"

extern "C" int diag_real_modem_serrint(void *);
extern "C" int diag_real_adaptecho(void *);
extern "C" void diag_real_v92_process(V92EchoCanceller *, float *, float *, unsigned);

struct stream {
    FILE *audio, *stats;
    char *audio_buffer, *stats_buffer;
    unsigned long total, count;
    double input, output, estimate;
    float buffer[4096][5];
    unsigned buffered;
};
static stream v34, v92;
static int enabled = -1;

static void flush(stream *s)
{
    if (s->audio && s->buffered)
        fwrite(s->buffer, sizeof(s->buffer[0]), s->buffered, s->audio);
    s->buffered = 0;
}
static void finish()
{
    flush(&v34); flush(&v92);
    if (v34.audio) fclose(v34.audio);
    if (v92.audio) fclose(v92.audio);
    if (v34.stats) fclose(v34.stats);
    if (v92.stats) fclose(v92.stats);
    free(v34.audio_buffer); free(v34.stats_buffer);
    free(v92.audio_buffer); free(v92.stats_buffer);
}
static int on()
{
    if (enabled < 0) {
        const char *e = getenv("SLMODEMD_EC_PROBE");
        enabled = e && atoi(e) != 0;
        if (enabled) atexit(finish);
    }
    return enabled;
}
static void open_stream(stream *s, const char *name)
{
    char path[4096];
    const char *dir = getenv("SLMODEMD_CAPTURE_DIR");
    if (!dir) dir = "/tmp";
    snprintf(path, sizeof(path), "%s/local-ec-%s.f32", dir, name);
    s->audio = fopen(path, "wb");
    /* Keep recording I/O out of these bounded live tests. 64 MiB holds
     * over five minutes of V.34 rows; normal shutdown flushes the stream.
     * Allocate this BEFORE any call, not in the per-sample receive path. */
    s->audio_buffer = (char *)malloc(64u*1024u*1024u);
    if (s->audio && s->audio_buffer)
        setvbuf(s->audio, s->audio_buffer, _IOFBF, 64u*1024u*1024u);
    else if (s->audio) {
        fclose(s->audio); s->audio = NULL;
        fprintf(stderr, "EC observation: insufficient audio buffering; capture disabled\n");
    }
    snprintf(path, sizeof(path), "%s/local-ec-%s-format.json", dir, name);
    FILE *format = fopen(path, "w");
    if (format) {
        fprintf(format, "{\"version\":2,\"dtype\":\"<f4\",\"columns\":[\"tx\",\"raw_input\",\"corrected_input\",\"residual\",\"flags\"]}\n");
        fclose(format);
    }
    snprintf(path, sizeof(path), "%s/local-ec-%s.csv", dir, name);
    s->stats = fopen(path, "w");
    s->stats_buffer = (char *)malloc(1024u*1024u);
    if (s->stats) {
        if (s->stats_buffer)
            setvbuf(s->stats, s->stats_buffer, _IOFBF, 1024u*1024u);
        fprintf(s->stats, "sample_end,samples,path,txstate,rxstate,flags,far_enabled,near_nonzero,far_nonzero,near_norm,far_norm,near_adapt_count,far_adapt_count,input_power,residual_power,estimate_power\n");
    }
}
__attribute__((constructor)) static void prepare_capture()
{
    if (on()) {
        open_stream(&v34, "v34");
        open_stream(&v92, "v92");
    }
}
static void observe(stream *s, float tx, float raw, float input, float output, unsigned flags)
{
    float *row = s->buffer[s->buffered++];
    row[0] = tx; row[1] = raw; row[2] = input; row[3] = output; row[4] = (float)flags;
    s->total++; s->count++;
    s->input += (double)input*input;
    s->output += (double)output*output;
    s->estimate += (double)(input-output)*(input-output);
    if (s->buffered == 4096) flush(s);
}
static double norm(const struct v34_echo *e, unsigned *nonzero)
{
    double sum = 0;
    unsigned i;
    *nonzero = 0;
    if (!e->coeff || e->taps > V34_ECHO_TAPS) return 0;
    for (i = 0; i < e->taps; i++) {
        double value = (double)e->coeff[i] / 16384.;
        sum += value*value;
        if (e->coeff[i]) (*nonzero)++;
    }
    return sqrt(sum);
}
static void v34_report(struct v34_object *o, int path)
{
    unsigned near_n, far_n;
    double near_norm = norm(&o->echo0, &near_n);
    double far_norm = norm(&o->echo1, &far_n);
    if (v34.stats) fprintf(v34.stats, "%lu,%lu,%d,%d,%d,%u,%d,%u,%u,%.9g,%.9g,%u,%u,%.12g,%.12g,%.12g\n",
        v34.total, v34.count, path, o->txstate, o->rxstate,
        (unsigned short)o->tx_flags, o->far_echo_enable, near_n, far_n,
        near_norm, far_norm, (unsigned short)o->echo0.adapt_count,
        (unsigned short)o->echo1.adapt_count, v34.input/v34.count,
        v34.output/v34.count, v34.estimate/v34.count);
    v34.count = 0; v34.input = v34.output = v34.estimate = 0;
    flush(&v34);
}
extern "C" int modem_serrint(void *object)
{
    if (!on()) return diag_real_modem_serrint(object);
    struct v34_object *o = (struct v34_object *)object;
    const short raw = o->echo_residual;
    const short input = (short)((unsigned short)raw + (unsigned short)o->echo_correction);
    const short index = o->hist2_idx;
    int ret = diag_real_modem_serrint(object);
    observe(&v34, o->tx_sample, raw, input, o->hist_2f58[index], (unsigned short)o->tx_flags);
    if (v34.count >= 960 || o->txstate != 0 && v34.total == 1) v34_report(o, 0);
    return ret;
}
extern "C" int adaptecho(void *object)
{
    if (!on()) return diag_real_adaptecho(object);
    struct v34_object *o = (struct v34_object *)object;
    const short raw = o->echo_residual;
    const short input = (short)((unsigned short)raw + (unsigned short)o->echo_correction);
    int ret = diag_real_adaptecho(object);
    observe(&v34, o->tx_sample, raw, input, o->echo_residual, (unsigned short)o->tx_flags);
    if (v34.count >= 960) v34_report(o, 1);
    return ret;
}

extern "C" void ec_v92_process(V92EchoCanceller *, float *, float *, unsigned)
    __asm__("_ZN16V92EchoCanceller7processEPfS0_j");
extern "C" void ec_v92_process(V92EchoCanceller *o, float *input, float *output, unsigned n)
{
    if (!on()) { diag_real_v92_process(o, input, output, n); return; }
    /* Save inputs first because an implementation may accept in-place buffers. */
    float *copy = (float *)malloc(n*sizeof(float));
    if (copy) memcpy(copy, input, n*sizeof(float));
    diag_real_v92_process(o, input, output, n);
    if (!copy) return;
    for (unsigned i = 0; i < n; i++) observe(&v92, 0, copy[i], copy[i], output[i], o->state);
    free(copy);
    if (v92.count >= 960) {
        double energy = 0; unsigned nonzero = 0;
        if (o->echoCoeff && o->filterLength < 4096)
            for (unsigned i = 0; i < o->filterLength; i++) {
                energy += (double)o->echoCoeff[i]*o->echoCoeff[i];
                if (o->echoCoeff[i] != 0) nonzero++;
            }
        if (v92.stats) fprintf(v92.stats, "%lu,%lu,2,-1,-1,%u,0,%u,0,%.9g,0,%u,0,%.12g,%.12g,%.12g\n",
            v92.total, v92.count, (unsigned)o->state, nonzero, sqrt(energy),
            o->updateSampleCount, v92.input/v92.count, v92.output/v92.count, v92.estimate/v92.count);
        v92.count = 0; v92.input = v92.output = v92.estimate = 0;
        flush(&v92);
    }
}
