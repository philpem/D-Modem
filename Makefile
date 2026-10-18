PJSIP_DIR=pjproject-2.15.1
PKG_CONFIG_PATH=pjsip.install/lib/pkgconfig
DSP ?= original
DSP_ROOT ?= $(CURDIR)/slmodem_dsp_re
override DSP_ROOT := $(abspath $(DSP_ROOT))
export DSP_ROOT

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

clean:
	rm -f d-modem.o d-modem
	$(MAKE) -C slmodemd clean

realclean: clean
	$(MAKE) -C $(PJSIP_DIR) realclean
	rm -rf pjsip.install/*
	

.PHONY: all clean realclean slmodemd variants
