/* Apparatus test: wrappers must forward arguments/results without modifying
 * the mock DSP state or sample outputs. This is not DSP reachability evidence. */
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "dsplib/v34fsk.h"
#include "dsplib/v34rx.h"
#include "dsplib/V92EchoCanceller.h"
extern "C" int diag_real_modem_serrint(void *ptr) {
    v34_object *o=(v34_object *)ptr;
    o->hist_2f58[o->hist2_idx]=o->echo_residual-100;
    o->hist2_idx=(o->hist2_idx+1)%600;
    o->tx_sample=100;
    return 73;
}
extern "C" int diag_real_adaptecho(void *ptr) {
    v34_object *o=(v34_object *)ptr;
    o->echo_residual-=100;
    o->tx_sample=100;
    return 47;
}
extern "C" void diag_real_v92_process(V92EchoCanceller *o,float *in,float *out,unsigned n) {
    for(unsigned i=0;i<n;i++) out[i]=in[i]-.5f;
    o->updateSampleCount+=n;
}
V92EchoCanceller::V92EchoCanceller(V92Parameters *,unsigned,unsigned) {
    memset(this,0,sizeof(*this));
}
V92EchoCanceller::~V92EchoCanceller() {}
extern "C" void ec_v92_process(V92EchoCanceller *,float *,float *,unsigned)
    __asm__("_ZN16V92EchoCanceller7processEPfS0_j");
int main() {
    v34_object *o=(v34_object *)calloc(1,sizeof(v34_object));
    v34_object *expected=(v34_object *)calloc(1,sizeof(v34_object));
    short coeff[4]={1638,0,0,0};
    o->echo0.coeff=coeff; o->echo0.taps=4; o->tx_flags=0x200; o->txstate=3;
    for(int i=0;i<1920;i++) {
        o->echo_residual=800;
        memcpy(expected,o,sizeof(*o));
        int want=i<960?diag_real_modem_serrint(expected):diag_real_adaptecho(expected);
        int got=i<960?modem_serrint(o):adaptecho(o);
        if(got!=want || memcmp(o,expected,sizeof(*o))) return 1;
    }
    V92EchoCanceller a(NULL,0,0),b(NULL,0,0);
    float x[40],y[40];
    for(int i=0;i<40;i++) x[i]=y[i]=i;
    diag_real_v92_process(&a,x,x,40);
    ec_v92_process(&b,y,y,40);
    if(memcmp(x,y,sizeof(x)) || memcmp(&a,&b,sizeof(a))) return 2;
    free(o); free(expected);
    puts("PASS: 1920 V.34 wrapper calls, return values/state preserved; V.92 in-place output/state preserved");
    return 0;
}
