# Olinet V.34 reliability investigation

The confirmed host-side defect is progressive jitter-buffer deletion. The
separate jitter-buffer change selects `PJMEDIA_JB_DISCARD_NONE` explicitly.
It is tracked in [issue #1](https://github.com/philpem/D-Modem/issues/1#issuecomment-6249508731);
this integration PR retains the baseline media policy. Intermittent V.34
training failures remain; disabling the handshake watchdog has not been
established as their correction.

## Source and build

The integration uses `philpem/D-Modem`, based on `pjsip2.15` commit
`902c79aa36fa094c7513fa911b64f4fa8a0bf614`, identical to the cryan209 branch
when compared on 18 October 2026. The working branch is
`feat/reconstructed-dsp-integration`. The jitter-buffer fix uses a separate
branch and PR. Existing integration edits were retained.

The diagnostic reconstructed DSP was built from
`philpem/slmodem_dsp_re` commit `28ccee0669a7f02a1663736b10900d239928b824`,
including merged PRs #293 and #295. Diagnostic wrappers use copied objects;
they do not modify that DSP source or the ordinary daemon binaries.

## Confirmed transport defect

Equal 40 ms minimum, maximum and initial prefetch values do not disable
pjproject's progressive frame-deletion policy. Its deletion path can cause
missing playback frames while the public discard counter stays zero.
The modem then receives a discontinuous waveform despite continuous RTP
sequence numbers and timestamps.

The standalone POC on [issue #1](https://github.com/philpem/D-Modem/issues/1)
exercises pjproject 2.15.1 at the production buffer settings. Of 2,000 ordered codec frames, the corrected policy preserves
all frames byte for byte. The former progressive policy produces three missing
frames and 1,931 subsequent frame mismatches. The test supplies no packet loss,
reordering, overflow or starvation. A failed live call also had continuous RTP
sequence numbers and timestamps in both directions.

The change retains 40 ms prefetch, 500 ms queue capacity and the existing
host I/O delay of 48. It does not establish tolerance of network loss or
clock drift, nor does it make all Olinet calls reliable.

## Upstream issue #13 and PR #14

[Issue #13](https://github.com/strozfriedberg/D-Modem/issues/13) describes
the same final watchdog condition observed in the reverse-direction failure:
9,600 consecutive low-metric watchdog increments set progress status 9,
which becomes `Link Error` and `NO CARRIER`. The issue describes this as one
second at the DSP rate. The counter advances in the handshake dispatch tail;
that claim should not be used as an exact wall-clock duration for every state.

The reconstructed source confirms that the default threshold is 101. It is
selected by the normal minimum-signal table's fallback entry, rather than
by a missing G.711 level reference. The compared metric is the receiver's
signed `agc_level`: its signal path includes echo cancellation and receiver
processing, and its adaptation can freeze. Raw RX waveform RMS alone does
not establish the value of this internal metric.

[PR #14](https://github.com/strozfriedberg/D-Modem/pull/14) offers an opt-in
threshold override. A threshold below every signed 16-bit metric prevents
this particular watchdog from expiring; other acquisition timeouts remain.
That intervention can establish whether the abort prevents recovery, but
cannot establish why acquisition failed or the metric fell.

The issue's corrected historical result is 19/30 completed calls with the
watchdog armed and 27/30 disabled on one installation. It does not establish
a speed increase. The PR description still repeats the older assertion that
every completed call reported 33,600 bit/s; the issue and its October 16
correction explicitly withdraw that assertion.

The PR installer pins a different vendor-object hash from either object in
this checkout. It must not be applied by bypassing its hash gate. For the
source-built diagnostic DSP, the three wrapper field offsets were checked
against the reconstructed types, and disassembly verified both calls:
`vpcm_run` to wrapper, and wrapper to original. All ten isolated upstream
wrapper tests passed locally. The wrapper remains a scratch experiment.

The initial matched live calls used the same diagnostic daemon and production
SIP endpoint, changing only the threshold override. Both completed training,
transferred 1,058 exact bytes each way, held for ten seconds, and transferred
another 1,085 exact bytes each way. Neither retrained. Stock negotiated
33,600 TX / 14,400 RX; disabled negotiated 33,600 TX / 12,000 RX, from D-Modem's
perspective. The stock call's sampled metric stayed above threshold and its
sampled low-signal counter stayed zero. These calls do not demonstrate a
benefit from disabling the watchdog or estimate its effect on reliability.

A subsequent stock call reproduced the target failure. It remained in
`JTXMIT` / `DET_SYNC`, with the sampled metric first 0 and then 80 against
threshold 101. Its sampled low-signal counter reached 9,500 immediately before
status 9. The logged interval from entering J to status 9 was about two
seconds. This directly establishes that the watchdog ends this failed
handshake, while leaving the reason J acquisition failed unresolved.

A second disabled-watchdog call connected and initially transferred 1,058
exact bytes each way. During its 60-second hold it retrained and returned to
data mode, with further rate renegotiations. Fresh payload verification then
passed D-Modem-to-hardware, but hardware-to-D-Modem returned only 1,023 of
1,085 bytes within the 30-second receive window. Carrier remained up. The
workaround therefore did not satisfy this call's sustained-data acceptance
criterion. This is not a matched recovery of the exact failed stock call,
and the small sample does not estimate either intervention's completion rate.

## Other hypotheses from the discussion

RX noise and delayed self-echo are plausible experiments in modelling an
analogue interface. The discussion's later noise/echo comparison disabled
the watchdog in every arm, so it could not establish whether noise prevents
this watchdog failure. Neither synthetic noise nor echo is enabled here.
An eventual comparison must keep the watchdog armed, state the injection
point and level convention, and verify payloads and retraining after CONNECT.

Host I/O delay describes local buffering and the DSP's interface assumptions;
it should not be set to the whole SIP/PBX/ATA round-trip delay. This checkout
already reports 48 rather than upstream's problematic zero. That value still
needs a measured buffering justification; a successful call at another value
does not establish the correct default.

The answering-side automode issue is also present: `modem.c` clears
`automode_requested`. It can prevent fallback between modulations, but does
not explain these tests, which explicitly selected V.34 at both ends and
reached V.34 phase 3. It needs a separate mixed-modulation acceptance test.

Stream framing is another host concern: the local audio socket is
`SOCK_STREAM`, while its readers and writers expect whole structures. Short
reads and writes need explicit handling. The retained call logs did not
report a short-frame error, so this is not established as the cause of the
captured Olinet failure.

## Remaining localisation

The failed stock-watchdog call now supplies the internal metric and handshake
states. The next comparison must establish why J acquisition fails, whether
the peer recognises the caller's J signal, and whether the low metric precedes
or follows that failure. Separately localise the post-retrain payload shortfall;
CONNECT and continuing carrier are insufficient acceptance criteria. Keep the
watchdog workaround separate from a DSP or analogue-interface correction.

Two calls with the optional local ALSA monitor compiled out succeeded without
retraining, one in each direction. Other calls with the monitor enabled also
succeeded. This is insufficient evidence to identify the monitor as a cause.

Raw evidence is retained in the enclosing integration workspace under
`scratch/olinet-reverse-diagnostics/` and
`scratch/olinet-reverse-review-*`, including per-call results, audio, logs,
RTP analysis, source/build identifiers and the isolated PR wrapper.
