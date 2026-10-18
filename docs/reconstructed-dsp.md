# Reconstructed DSP integration

Clone `philpem/slmodem_dsp_re` beside this D-Modem checkout, or set
`DSP_ROOT=/absolute/path/to/slmodem_dsp_re`. The integration tools are included
under `tools/`; generated evidence stays in ignored `scratch/` directories.
Run these commands from this checkout:

```sh
make DSP=original       # supplied Smart Link object (the default)
make DSP=reconstructed  # compile the reconstructed source and link it
make DSP=reconstructed-ec # add optional local echo observation wrappers
make variants          # retain both daemon executables for comparison
make check             # validate the diagnostic analysers
make analysis          # build the reused SpanDSP V.8 decoder
```

`DSP_ROOT=/absolute/path` selects another reconstruction checkout. The
reconstructed build uses the upstream Makefile's production flags, compiles
all 300 source translation units and partially links them into
`slmodem_dsp_re/build/dmodem/dsplibs.o`. It includes no proprietary object.
The existing bridge's `VPCMXF_Create` interposer calls a renamed reconstructed
implementation through its existing `__blob_VPCMXF_Create` ABI name.
`build/dmodem/build.json` records the source revision, compiler and object hash.
This build does not assert byte identity with the original object.

The diagnostic Python tools require NumPy, SciPy and Matplotlib. Hardware
testing uses Linux serial ports. `make analysis` needs a built SpanDSP 3
source tree; set `SPANDSP_ROOT` if it is not at
`../spandsp`. The decoder source is reused directly from
the reconstruction's `testbench/v8analyse.c`.

Identify the hardware and map its extension before dialling it. Adapter names
identify the serial adapter; ATI responses identify the attached modem:

```sh
python3 tools/modem_test.py --tty /dev/ttyUSB0 --identify
python3 tools/modem_test.py --tty /dev/ttyUSB0 --originator hardware \
  --dial 4242 --map-only --hardware-init ATX3 --out scratch/map
python3 tools/modem_test.py --tty /dev/ttyUSB0 --originator hardware \
  --dial 4242 --dsp reconstructed --hardware-init ATX3 \
  --bytes 4096 --hold 60 --verify-after-hold --out scratch/reconstructed-call
python3 tools/analyse_call.py scratch/reconstructed-call --role answerer
```

Each output directory must be new. The call runner opens both terminals for
the entire call, checks AT command results, verifies an exact payload in each
direction, and writes `result.json`, terminal logs, DSP logs and mono WAV
captures at 8 kHz and 9.6 kHz. `--verify-after-hold` requires fresh exact
payloads in both directions after the hold, so continuing carrier cannot hide
a stalled data path. It terminates only its own process group.
`--dsp original` runs the same procedure against the supplied DSP object.
`--diagnose` reads the hardware modem's last-call report without placing a call.
The runner also saves `hardware-diagnostics.json` and `dmodem-diagnostics.json`
after each call, before resetting anything. For an independent collection,
use `tools/modem_diagnostics.py --tty /dev/ttyUSB1 --kind other --out REPORT.json`
(`--kind usr` for the Courier). Courier `ATI6`, `ATI11`, `ATI16` and `ATI17`
provide link counters, signal/echo measurements, a channel-probe chart and a
training signature. Olinet `AT&V1`, `ATS86?` and `AT#UD` provide its last-call
report, failure reason and decoded hexadecimal carrier/frame/overrun counters.
Unsupported commands and empty responses remain explicit. Failed-call
placeholder values are retained and flagged; they are not line measurements.
Local teardown can replace a termination report with a host-requested hangup
or key abort. The result records whether a terminal failure preceded cleanup.

`tools/sip_exec.py` reads the external `asterisk-login-4242.secret` at launch
and supplies credentials through `SIP_SERVER`, `SIP_USER` and `SIP_PASSWORD`.
Use `--secret /path/to/account.secret` to select another account. Credentials
are never copied into the workspace or placed in command-line arguments.
The documented second account is 4343. These examples use internal test
extensions.

The runners enable maximum DSP verbosity (`SLMODEMD_DSP_DEBUG=2147483647`)
and readable encoded diagnostics (`SLMODEMD_DSP_PLAIN=1`). The startup log
confirms the effective settings. The original DSP retains its encoded output;
the analyser reuses the reconstruction's `tools/eddecode.py` to make a readable copy. Daemon logging
remains at `-d9`. `SLMODEMD_CAPTURE_DIR` selects the raw capture directory.
The level is positive in both signed and unsigned comparisons: some DSP
sites cast the global to signed `int`, so `4294967295` becomes -1 and suppresses
those messages. Environment overrides are respected.

The analyser produces:

- `dsp-readable.log` and `dsp-trace.log`, preserving negotiation, training,
  echo and error messages with their original timestamps.
- `negotiation.txt` and `negotiation.json`: V.8 CM/JM/CI payloads, advertised
  modulations, call function, LAPM, PSTN/PCM capabilities, repeated-message
  confirmation and CJ. The actual CONNECT mode/rates are recorded separately
  from advertised capabilities. V.8bis CL/CLR/MS capability trees, ACK/NAK
  and raw non-standard provider fields are decoded only after HDLC CRC
  validation. Both V.21 channels are checked because V.8bis initiating roles
  can differ from caller/answerer roles. Unknown bits remain explicit.
- `v8-rx.log` and `v8-tx.log`, with the upstream independent SpanDSP V.8
  decoder, plus V.21 octet traces. V.8bis dual-tone signals and their second
  segments are traced in `v8bis-rx.json` and `v8bis-tx.json`. Layouts and tone
  frequencies come from
  [ITU-T V.8bis (11/2000)](https://www.itu.int/rec/dologin_pub.asp?id=T-REC-V.8bis-200011-I!!PDF-E&lang=e&type=items).
- `v34-response.png`, CSV measurements and JSON metadata, derived from the
  received standard line probe. Four deliberately omitted tones are excluded.
  Magnitude is relative to 750 Hz; constant phase and bulk delay are removed.
  Delay is ambiguous by one 150 Hz period. These measurements include the
  far modem, ATA, network codec and receive resampler. They do not isolate
  the ATA's analogue circuitry. A capture without a qualified probe produces
  an explicit failure instead of a graph. Frequency ticks and grid lines are
  spaced at every 150 Hz bin, including the deliberately unexcited bins.
- `info0-rx.json`, `info0-tx.json` and a section in `negotiation.txt`: CRC-checked
  V.34 INFO0 frames recovered directly from the audio, including supported
  symbol rates, carrier choices, clock source and recovery acknowledgment.
  A receive recording can contain returned local echo; the transmitter role
  is identified from its 1200/2400 Hz carrier. Standard 17-bit INFO0 is decoded;
  PCM extensions and INFO1 are outside this decoder's scope.
- Echo diagnostic messages and coefficient reports, when the DSP emits
  them. A report saying “Nothing to report” does not establish that echo is
  absent. Hardware `ATI11` echo losses describe the hardware receiver's
  perspective; they are not local DSP cancellation statistics.

For local echo telemetry, build and select `reconstructed-ec`. Copies of three
DSP objects have their observation entry points renamed; wrappers call the
unchanged implementations and record samples and state. No reconstruction
source or header is modified. `SLMODEMD_EC_PROBE=0` disables observation for
control runs. This is an optional diagnostic build; the ordinary
`reconstructed` build has no observation wrappers.

The observer opens its files before the call and buffers up to 64 MiB of
audio per stream (over five minutes of V.34 telemetry) and 1 MiB of statistics.
These buffers flush on normal process shutdown; abrupt termination can lose
buffered telemetry. Longer captures can fill a buffer and write during the
call. This buffering keeps file creation and periodic disk writes out of the
short training/data tests.

`local-ec.txt`, JSON and plots report near/far nonzero tap counts, coefficient
norms, adaptation counters, feed/freeze flags and input/residual power.
Format version 2 records five float32 values per sample: local transmit,
raw receive, receive after correction but before the echo filter sum,
post-filter residual, and flags. The adjacent format JSON identifies the
layout. V.34 samples are at 9600 Hz. Version 1 captures have four columns and
combine receive correction with the echo filter's effect; the analyser labels
them accordingly. Total power reduction includes the far-end signal and is
not echo-only ERLE. The optional V.92 observer reports sample counts without
assuming a sample rate.

Generate the separate modem graphs and combined graph in one command:

```sh
python3 tools/compare_calls.py scratch/postboot-courier-original \
  scratch/postboot-courier-reconstructed scratch/postboot-olinet-original \
  scratch/postboot-olinet-reconstructed-ec --split-modems --out scratch/modem-comparison
```

All plots use the same magnitude/phase limits and curve colours. A qualified
probe from a failed training attempt remains usable; `results.json` retains
the call's connection and data-test outcome. The far modem remains part of
the measured path, so differences between modem models cannot be attributed
solely to the ATA.

The serial runner handles short writes and drains both ends while sending.
The default console-cable setup ignores DTR (`&D0`) and disables modem-side
hardware flow control as well as host RTS/CTS. Cisco USB console cables may
lack handshake wires; a successful ioctl does not prove those wires exist.
`--payload-bps 4800` paces transfers for adapters without working flow-control
lines; `--hardware-flow rtscts` requires CTS to be asserted after hardware
initialization. Payloads are still checked byte for byte. `--data-wait`
controls the write/receive timeout. Avoid competing registrations by running
hardware tests sequentially.

For two-hardware controls, `tools/hardware_pair.py` reuses the same serial
handling, exact payload checks and diagnostics. Calls between 1901 and 1903
stay inside the VG204. To include Asterisk and RTP, use its existing
packet-preserving relay:

```sh
python3 tools/hardware_pair.py --caller /dev/ttyUSB1 --answer /dev/ttyUSB0 \
  --dial 4242 --relay-to 1901 --relay-secret /path/to/account.secret \
  --out scratch/hardware-rtp-control --caller-init 'ATX3;ATS95=47'
```

This path includes two gateway media legs. A successful V.34 data transfer
demonstrates that path can carry the modulation; a failure alone does not
locate the impairment. The relay reuses `testbench/rtprelay.py` and records
each direction without decoding, mixing or reclocking its forwarded packets.

The decoder checks include known V.8/V.8bis payloads transmitted as synthetic
V.21 waveforms on both channels, corrupted CRCs, and silence/noise controls.
`tools/replay_test.py RECORDING --out NEW_DIRECTORY` reuses the upstream
blocking replay peer to compare telemetry on/off against the ordinary build
on identical input. This is an open-loop apparatus check, not a real call:
the recording cannot react to transmitted audio. The Courier recording
produced bit-identical transmit PCM in all three configurations. The separate
`tools/test_ec_probe.cpp` fixture checks wrapper forwarding and in-place V.92
buffers using mock implementations; it is not DSP reachability evidence.

For a transport control, `tools/pair_test.py` supports two daemon instances:

```sh
python3 tools/pair_test.py --caller reconstructed --answer original \
  --transport local --out scratch/local-pair
python3 tools/pair_test.py --caller original --answer original \
  --transport sip --out scratch/sip-control
```

The SIP pair uses accounts 4242 and 4343. Run account-sharing tests sequentially
to avoid competing registrations. A failed local or SIP pair test is retained
as evidence and must not be taken alone as a reconstruction defect; compare
the original-DSP control and hardware calls.

### Bench results, 17 October 2026

The reconstruction at `67d401f8d7991dcda8dd05e896642bcd3da5fb11`, compiled
with GCC 13.3.0 for i386, connected to the Courier V.Everything on extension
1901 through the Cisco ATA and Asterisk. The original DSP passed the same
hardware-originated call. Both reported local rates of 33600 bit/s transmit
and 14400 bit/s receive, with LAPM and V.42bis. The reconstructed build also
passed 4130 exact bytes in each direction and a 60-second carrier hold.

The received probe curves agree with the original control within 0.061 dB
RMS magnitude and 0.23 degrees RMS phase across the two reconstructed calls.
The comparison and numeric results are in
[`scratch/ata-comparison/`](../scratch/ata-comparison/). The DSP diagnostics
show repeated INFO0 recovery in both implementations. No reconstruction-only
training failure was established by these hardware calls.

The original-DSP local and SIP software-pair controls failed V.34 training.
An outbound SIP call to 1901 received status 503 before modem audio began;
hardware-originated calls to 4242 worked. These failures remain recorded in
[`scratch/test-summary.json`](../scratch/test-summary.json).

Neither integer echo canceller emitted non-zero coefficient data. The
Courier's last-call report for the longer reconstructed call gave 38.1 dB
SNR, 14.6 dB near echo loss, 60.5 dB far echo loss and 151 ms round-trip delay.
Those are measurements at the Courier. No local DSP ERLE result was obtained.
The separate random-data call also passed 4130 exact bytes each way. Its
Courier report claimed an impossible 6392.9 dB SNR, unchanged on rereading;
the analyser preserves the values and marks that report implausible.

Follow-up diagnostics decode the actual CM/JM octets and CJ from both modems.
Both advertise V.34 and V.32/V.32bis in common with the local answerer; the
local JM selects LAPM. The Olinet also advertises V.23. No CRC-valid V.8bis
capability frame was found in these captures, despite checking both V.21
channels; the waveform controls demonstrate that the decoder accepts valid
frames and rejects corrupted frames.

After an operator-reported ATA reboot, the original and ordinary reconstructed
Courier controls each passed training and 1058 exact bytes in each direction.
The optional EC build passed with capture disabled. With buffered capture
enabled it passed once and failed its repeat, so instrumentation reliability
is **unresolved**. File creation was moved before the call and short captures
are buffered until shutdown, but this did not eliminate the training failure.
The generic `slmodemd` executable was restored to the ordinary reconstructed
build, byte-identical to the earlier passing binary. EC observation remains
an experimental option. The ordinary reconstructed build also had one earlier
INFO0 recovery timeout, so the available tests do not establish a single cause.

The Olinet on extension 1903 failed V.34 training in both original-DSP and
instrumented-reconstruction post-reboot calls. An earlier ordinary
reconstructed call reached 33600 TX / 7200 RX but failed data verification
and lost carrier. The probe was measurable even on failed calls; the latest
separate Courier/Olinet plots and combined plot are under
[`scratch/modem-comparison/`](../scratch/modem-comparison/), with shared scales
and 150 Hz ticks. Routing failures during the reported ATA reboot are recorded
separately from these training outcomes.

The successful buffered Courier capture contains 251424 local V.34 EC samples.
It recorded feed enabled and adaptation counters advancing, but all high-half
coefficients stayed zero and no echo filter sum was applied. This is an observation
of that call, not proof that its echo path is absent. An earlier, longer
diagnostic call observed 144 nonzero near and far coefficients, but failed one
payload direction; that legacy capture also combines receive correction with
cancellation. Both reports are retained, without claiming echo-only ERLE.
The final replay at debug level 2147483647 produced bit-identical transmit PCM
with EC observation absent, disabled and enabled, and emitted the signed-gated
data-rate diagnostics suppressed by the old UINT_MAX setting. Replay agreement
does not establish live timing equivalence.

The VG204 configuration supplied later has identical voice settings on 0/0
(Courier, 1901) and 0/2 (Olinet, 1903), with no explicit impedance setting.
With console-cable controls configured, the two hardware modems passed local
V.34 at 33600/33600 and the packet-relay control at 31200/33600, each carrying
290 exact bytes both ways. The Courier reported 36.9 dB SNR and 174 ms
round-trip delay for the packet control, without retrains or block errors.
No gateway setting was changed.

The Olinet still failed with the original DSP after reversing the call,
disabling DSP debug output, and forcing V.34 on the hardware. Audio decoding
locates a recovery deadlock: fresh original and ordinary reconstructed calls
each received one valid remote INFO0, then transmitted 178 INFO0 frames with
ACK bit 0 before timing out. The successful Courier control returned six
INFO0 frames with ACK bit 1. One reconstructed attempt failed earlier waiting
for V.8 CJ; its repeat reached the INFO0 timeout. These are separate failure
stages. Subsequent reconstructed-only state tracing found a false recovery
trigger during near silence: FSK input samples `[1, 0, -2, -3]` produced a
discriminator value of -1 and fresh bits changed the shift register from
`0x0207` to `0x040f`. The four-one test in `t3m_micro47` then entered INFO0
recovery at counter 85, before the normal line-probe transition at 96. This
was fresh demodulator output, rather than stale INFO0 fill bits. V.34
11.2.1.1.3 requires the caller to become silent after its Tone B reversal.
The Courier observer control also showed this near-silence false trigger;
it returned an ACK and passed phase 2, but that call failed later in training.
This localizes a shared Smart Link recovery problem without establishing a
tested correction to the detector. The decoded
timeline is in [`scratch/olinet-investigation-info0.png`](../scratch/olinet-investigation-info0.png),
and all new call outcomes and limitations are in
[`scratch/olinet-investigation.json`](../scratch/olinet-investigation.json).
V.32bis is a tested fallback: original and ordinary reconstructed builds
both trained at 14400 and carried 290 exact bytes in each direction with a
five-second carrier hold. The original call's last-call report showed a
retrain and final rates of 12000/12000; the reconstructed call remained at
14400/14400. Software `ATI11`/`AT#UD` are unsupported and `AT&V` returns no
connection report; local DSP measurements still come from DSP diagnostics
and the optional observer, rather than those AT commands.

For further handshake investigation, `DSP=reconstructed-hs` links two small
observation wrappers around copied `v34handshak` and `fskdemodulate` objects.
The runner enables `SLMODEMD_HANDSHAKE_PROBE=1` for this variant. The ordinary
reconstruction and original DSP remain separate. Records contain state before
the handshake, after FSK demodulation and after the handshake, including four
input samples at the FSK stage. The 262144-row buffer is touched before the
call and written to `handshake-state.csv` only during normal shutdown.
`tools/handshake_trace.py CSV --out REPORT.json` reports transitions, fresh-bit
recovery triggers and dropped-row counts. Buffer overflow drops observations;
abrupt termination loses the buffered trace. The first, smaller observer
capture was truncated after 65536 rows; the subsequent Olinet and Courier
captures had zero dropped rows. The synthetic forwarding fixture checks that
the wrappers preserve the mock DSP's state, but does not prove live timing
equivalence or successful training.
`make check-handshake` compiles and runs that 32-bit synthetic fixture with
observation enabled and disabled; `make check` includes the trace analyser's
fresh-bit, stale-bit and no-recovery controls.


## Packaging validation, 18 October 2026

The checked-in tools now resolve the D-Modem checkout directly and use
`DSP_ROOT` for the separate reconstruction. Terminal helpers are included
locally so an unmodified reconstruction checkout is sufficient; the tools do
not require patching its `testbench/call.py` to suppress command-line parsing.
All four daemon variants built against reconstruction revision
`28ccee0669a7f02a1663736b10900d239928b824`. `make check` and the separate
32-bit `make check-handshake` passed. The terminal-I/O fixture verifies fresh
bidirectional payloads and rejects full-length corrupted post-hold data.
A fresh local reconstructed-caller/original-answerer call failed training;
this packaging does not claim that local or hardware training is reliable.

See [the Olinet investigation](olinet-reliability.md) for the separately
tracked jitter-buffer fix and unresolved J acquisition/data failures.

The freshly packaged reconstructed daemon also passed a Courier-originated
hardware call: 1,058 exact bytes each way, a ten-second hold, then 1,061 fresh
exact bytes each way with `--verify-after-hold`. The baseline endpoint media
policy was retained for this independent PR. The local mixed-DSP failure and
this hardware success are separate observations, not a reliability estimate.

A clean native pjproject 2.15.1 configure/dependency/build/install cycle and
endpoint link passed in this checkout. The V.34/V.92 echo-wrapper fixture
also passed with observation disabled and enabled.
