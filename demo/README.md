# Presentation demo

Run everything from this checkout with one shell script:

```sh
./demo.sh --dsp-root /path/to/slmodem_dsp_re --secret /path/to/account.secret
```

The default is the reconstructed DSP. The script builds it and the SIP endpoint,
identifies the USRobotics Courier, calls SIP extension 4242 from the USR, and
auto-answers with slmodem. It displays commands and responses from both modems,
plays live negotiation audio, alternates the song's lines between the modems,
and verifies the bytes received for every line. After hanging up it displays
the USR's `ATI6` and `ATI11` reports, before resetting anything.

The current bench uses the Prolific serial adapter for the Courier. Use
`--tty /dev/serial/by-id/...` for another adapter; identification must still
report a USRobotics modem. `--dial` selects the internal SIP extension.
The external credential file has `server:`, `extension:` and `secret:` fields.
Credentials remain outside the repository and process arguments.

Other useful options:

- `--no-build`: reuse the prepared binaries for the presentation.
- `--line-delay 1`: leave a second between verified song lines.
- `--audio pulse --audio-device SINK`: select a PulseAudio/PipeWire output.
- `--audio alsa --audio-device default`: use ALSA playback instead.
- `--extended-stats`: also display the USR channel probe, training signature
  and configuration reports.
- `--plain`: omit terminal colour codes.
- `--out /path/to/new/directory`: select the saved rehearsal directory.
- `--song /path/to/ascii.txt`: use another text; at least two lines are needed.

Playback is stereo: USR-to-slmodem on the left and slmodem-to-USR on the right.
The player reads growing 8 kHz capture files in a separate thread and stops
when both modems report CONNECT. It does not feed samples back into the modem
or change the DSP, timing, watchdog or media policy. An unavailable player or
missing captured audio fails the normal demo; `--audio off` is an explicit
headless diagnostic option.

Each run saves its complete transcript, daemon/build logs, exact-transfer
results, USR statistics, separate RX/TX WAV files and the stereo negotiation
recording under `scratch/demo-TIMESTAMP/`. A failed call exits nonzero and
retains its evidence; it is not replaced by simulated dialogue or statistics.
Ctrl-C hangs up and releases this run's process group and serial port.

The fixed-DTE setting `AT&B1` is deliberate: the host serial port stays at
115200 baud when the line connects at another rate. The demo disables UART
hardware flow control for the bench cable and sends one verified line at a
time. No modem settings are written to a saved profile.

`major-general.txt` contains the Major-General's three verses from Gilbert
and Sullivan's *The Pirates of Penzance* (1879), with line wraps joined and
ASCII punctuation. The words are by W. S. Gilbert and are in the public domain.
Source: [Project Gutenberg, The 14 Gilbert and Sullivan Plays](https://www.gutenberg.org/files/808/808-h/808-h.htm).
The demo transfers the words, rather than playing a recording of the song.

Remaining debugging is tracked separately in [TODO.md](../TODO.md) and
[issue #4](https://github.com/philpem/D-Modem/issues/4).

## Rehearsal

The 18 October 2026 Courier/reconstructed-DSP rehearsal transferred all 36
lines (2,049 bytes total) exactly in alternating directions. It played 14.22
seconds of captured negotiation audio, then read both USR last-call reports
after remote cleardown. The next rehearsal also passed through the default automatic-build path,
completing in about 65 seconds. These are demonstrated runs, not a guarantee
of modem training reliability. An earlier rehearsal exposed the missing fixed-DTE
setting; its failed transfer was reported and retained before that setup was
corrected.
