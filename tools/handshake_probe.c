/* Optional reconstructed-only observer. No DSP fields are written here.
 * Record in preallocated memory; CSV I/O occurs only at normal shutdown. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dsplib/v34fsk.h"

extern void diag_real_v34handshak(void *);
extern void diag_real_fskdemodulate(struct v34_object *, const short *, struct v34_fsk *);

#define MAX_ROWS 262144
struct row {
    unsigned call, stage;
    int mst, tx, rx, recovery, armed, counter, sr, bits, phase, next, prev;
    int tx0, tx1, tx2, txpos, txcrc, rx0, rx1, rx2, rtd;
    int input[4];
};
static struct row rows[MAX_ROWS];
static unsigned used, dropped, calls;
static int enabled;
static FILE *output;

static void finish(void)
{
    unsigned i;
    if (!output) return;
    fprintf(output,"# rows=%u dropped=%u handshake_calls=%u\n",used,dropped,calls);
    fputs("call,stage,microstate,txstate,rxstate,recovery,armed,counter,sr,nbits,phase,next,prev,tx0,tx1,tx2,txpos,txcrc,rx0,rx1,rx2,rtd,input0,input1,input2,input3\n",output);
    for (i=0;i<used;i++) {
        const struct row *r=&rows[i];
        fprintf(output,"%u,%u,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d\n",
            r->call,r->stage,r->mst,r->tx,r->rx,r->recovery,r->armed,r->counter,
            r->sr,r->bits,r->phase,r->next,r->prev,r->tx0,r->tx1,r->tx2,
            r->txpos,r->txcrc,r->rx0,r->rx1,r->rx2,r->rtd,
            r->input[0],r->input[1],r->input[2],r->input[3]);
    }
    fclose(output);
}

__attribute__((constructor)) static void prepare(void)
{
    char path[4096];
    const char *flag=getenv("SLMODEMD_HANDSHAKE_PROBE");
    const char *dir=getenv("SLMODEMD_CAPTURE_DIR");
    enabled=flag && atoi(flag)!=0;
    if (!enabled) return;
    /* Touch the buffer before calls to avoid page faults while training. */
    memset(rows,0,sizeof(rows));
    {
        size_t page;
        volatile unsigned char *touch=(volatile unsigned char *)rows;
        for (page=0;page<sizeof(rows);page+=4096) touch[page]=0;
    }
    snprintf(path,sizeof(path),"%s/handshake-state.csv",dir?dir:"/tmp");
    output=fopen(path,"w");
    if (!output) enabled=0;
    else atexit(finish);
}

static void record(struct v34_object *o, unsigned stage)
{
    struct row *r;
    if (!enabled) return;
    if (used==MAX_ROWS) {dropped++;return;}
    r=&rows[used++];
    r->call=calls; r->stage=stage;
    r->mst=o->microstate; r->tx=o->txstate; r->rx=o->rxstate;
    r->recovery=o->short_3588; r->armed=o->short_358a; r->counter=o->short_aa78;
    r->sr=o->fsk.sr; r->bits=o->fsk.nbits; r->phase=o->fsk.phase;
    r->next=o->fsk.next; r->prev=o->fsk.prev;
    r->tx0=o->msgrec[0].word[0]; r->tx1=o->msgrec[0].word[1]; r->tx2=o->msgrec[0].word[2];
    r->txpos=o->msgrec[0].pos; r->txcrc=o->msgrec[0].crc;
    r->rx0=o->msgrec[1].word[0]; r->rx1=o->msgrec[1].word[1]; r->rx2=o->msgrec[1].word[2];
    r->rtd=o->rtd;
}

void v34handshak(void *obj)
{
    calls++;
    record(obj,0);
    diag_real_v34handshak(obj);
    record(obj,2);
}

void fskdemodulate(struct v34_object *obj, const short *in, struct v34_fsk *st)
{
    unsigned before=used;
    diag_real_fskdemodulate(obj,in,st);
    record(obj,1);
    if (used!=before) {
        unsigned i;
        for (i=0;i<4;i++) rows[used-1].input[i]=in[i];
    }
}
