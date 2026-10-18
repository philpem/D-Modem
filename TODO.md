# Deferred debugging TODO

Tracked in [issue #4](https://github.com/philpem/D-Modem/issues/4).

Debugging is deferred while the presentation demo is prepared on `demo/presentation`.

- [ ] Review/land integration PR #3 separately. Jitter PR #2 is now merged; its POC/results remain on #1.
- [ ] Localise the post-retrain hardware-to-D-Modem shortfall: 1,023 of 1,085 bytes arrived despite carrier remaining up. Trace serial/PTY, link framing and DSP boundaries before attributing it to DSP code.
- [ ] Determine why reverse-direction Olinet V.34 J acquisition stalls (`JTXMIT` / `DET_SYNC`). The status-9 watchdog is the observed abort mechanism; disabling it did not satisfy sustained-data acceptance. Keep the stock watchdog for root-cause work.
- [ ] Extend meaningful valid-history period differential coverage: philpem/slmodem_dsp_re#1 and philpem/slmodem_dsp_re#190.
- [ ] Capture the bounded finite/NaN raw-operand witness in philpem/slmodem_dsp_re#188.
- [ ] Schedule ABI integration (#15) and standards/interop work (#5, #11, #12, #13, #128 in philpem/slmodem_dsp_re), retaining separate fidelity and repaired-behaviour claims.
- [ ] Keep DSP PR philpem/slmodem_dsp_re#303 in review/draft until its source-boundary rationale is resolved; passing CI alone does not establish an identity gain.
- [ ] Revisit deferred modern portability, optional fuzz expansion and cosmetic cleanup after demonstrated modem failures and reconstruction coverage.

Demo acceptance: one shell entry point; visible commands/responses from both the USR and slmodem; verified text transfer in both directions using Gilbert and Sullivan; live negotiation RX/TX audio; hangup followed by USR last-call statistics before reset. Preserve the remaining debugging work rather than folding speculative DSP/transport changes into the demo.
