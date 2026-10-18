/* Synthetic wrapper forwarding control; not DSP reachability evidence. */
#include <assert.h>
#include <string.h>
#include "dsplib/v34fsk.h"
static struct v34_object object, expected;
static short input[64];
static unsigned hs_calls, fsk_calls;
extern void v34handshak(void *);
void diag_real_fskdemodulate(struct v34_object *o,const short *samples,struct v34_fsk *st)
{
    assert(o==&object && samples==input && st==&object.fsk);
    fsk_calls++;
    st->sr=15; st->nbits=2;
}
void diag_real_v34handshak(void *p)
{
    assert(p==&object);
    hs_calls++;
    object.microstate=47;
    fskdemodulate(&object,input,&object.fsk);
}
int main(void)
{
    object.microstate=46;
    expected=object;
    expected.microstate=47; expected.fsk.sr=15; expected.fsk.nbits=2;
    v34handshak(&object);
    assert(hs_calls==1 && fsk_calls==1);
    assert(memcmp(&object,&expected,sizeof(object))==0);
    return 0;
}
