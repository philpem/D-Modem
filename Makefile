PJSIP_DIR=pjproject-2.15.1
PKG_CONFIG_PATH=pjsip.install/lib/pkgconfig
DSP ?= original
DSP_ROOT ?= $(abspath ../slmodem_dsp_re)
override DSP_ROOT := $(abspath $(DSP_ROOT))
export DSP_ROOT
SPANDSP_ROOT ?= $(abspath ../spandsp)

all: d-modem slmodemd

$(PKG_CONFIG_PATH)/libpjproject.pc:
	(cd $(PJSIP_DIR); ./configure --prefix="$(CURDIR)/pjsip.install" --disable-video)
	$(MAKE) -C $(PJSIP_DIR) dep
	$(MAKE) -C $(PJSIP_DIR) && \
	$(MAKE) -C $(PJSIP_DIR) install

d-modem: d-modem.c $(PKG_CONFIG_PATH)/libpjproject.pc slmodemd/modem.h
	$(CC) -DWITH_AUDIO -Wall -Islmodemd/ -o $@ $< `PKG_CONFIG_PATH="$(PKG_CONFIG_PATH)" pkg-config --static --cflags --libs libpjproject`

slmodemd:
	$(MAKE) -C slmodemd

variants:
	$(MAKE) -C slmodemd DSP=original
	$(MAKE) -C slmodemd DSP=reconstructed
	$(MAKE) d-modem

analysis: scratch/v8analyse scratch/fsk_bits

scratch/fsk_bits: tools/fsk_bits.c
	mkdir -p scratch
	$(CC) -O2 -Wall -I$(SPANDSP_ROOT)/src -o $@ $< $(SPANDSP_ROOT)/src/.libs/libspandsp.a -lm

scratch/v8analyse: $(DSP_ROOT)/testbench/v8analyse.c
	mkdir -p scratch
	$(CC) -O2 -I$(SPANDSP_ROOT)/src -o $@ $< $(SPANDSP_ROOT)/src/.libs/libspandsp.a -lm

check: analysis
	OPENBLAS_NUM_THREADS=1 python3 tools/v34_response.py --selftest
	OPENBLAS_NUM_THREADS=1 python3 tools/v8bis_trace.py --selftest
	OPENBLAS_NUM_THREADS=1 python3 tools/test_negotiation.py
	OPENBLAS_NUM_THREADS=1 python3 tools/local_ec.py --selftest
	python3 tools/test_modem_io.py
	python3 tools/test_modem_diagnostics.py
	OPENBLAS_NUM_THREADS=1 python3 tools/v34_info.py --selftest
	python3 tools/handshake_trace.py --selftest
	python3 $(DSP_ROOT)/tools/eddecode.py --selftest

check-handshake:
	python3 tools/test_handshake_probe.py

clean:
	rm -f d-modem.o d-modem
	$(MAKE) -C slmodemd clean

realclean: clean
	$(MAKE) -C $(PJSIP_DIR) realclean
	rm -rf pjsip.install/*
	

.PHONY: all clean realclean slmodemd variants analysis check check-handshake
