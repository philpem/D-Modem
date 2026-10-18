/* Recover clocked V.21 bits using the independent SpanDSP receiver.
 * Input: signed little-endian 16-bit PCM, 8000 Hz. Output: sample,bit CSV.
 * Negative values are carrier/status events and delimit independent bursts.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <spandsp.h>

static unsigned long sample;
static void bit(void *unused, int value)
{
    (void)unused;
    printf("%lu,%d\n", sample, value);
}

int main(int argc, char **argv)
{
    FILE *input;
    fsk_rx_state_t *rx;
    int16_t value;
    int channel, mode;
    if (argc != 4 || (strcmp(argv[2], "ch1") && strcmp(argv[2], "ch2")) ||
        (strcmp(argv[3], "async") && strcmp(argv[3], "sync"))) {
        fprintf(stderr, "usage: %s PCM.raw ch1|ch2 async|sync\n", argv[0]);
        return 2;
    }
    input = fopen(argv[1], "rb");
    if (!input) { perror(argv[1]); return 1; }
    channel = !strcmp(argv[2], "ch1") ? FSK_V21CH1 : FSK_V21CH2;
    mode = !strcmp(argv[3], "sync") ? FSK_FRAME_MODE_SYNC : FSK_FRAME_MODE_ASYNC;
    rx = fsk_rx_init(NULL, &preset_fsk_specs[channel], mode, bit, NULL);
    if (!rx) { fclose(input); return 1; }
    while (fread(&value, sizeof(value), 1, input) == 1) {
        fsk_rx(rx, &value, 1);
        sample++;
    }
    fprintf(stderr, "samples=%lu rate=8000 channel=%s mode=%s\n", sample, argv[2], argv[3]);
    fsk_rx_free(rx);
    fclose(input);
    return sample ? 0 : 1;
}
