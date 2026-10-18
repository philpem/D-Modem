#!/usr/bin/env python3
"""Compile and partially link the complete reconstructed DSP for D-Modem."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from integration_paths import DSP_ROOT


def run(argv, **kwargs):
    subprocess.run([str(a) for a in argv], check=True, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path,
                    default=DSP_ROOT)
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--ec-probe', action='store_true', help='link optional local echo observation wrappers')
    ap.add_argument('--handshake-probe', action='store_true', help='link optional handshake/FSK observation wrappers')
    args = ap.parse_args()
    if args.ec_probe and args.handshake_probe:
        ap.error('select one observer at a time')
    source = args.source.resolve()
    inputs = sorted(p for p in (source / 'src').rglob('*')
                    if p.suffix in ('.c', '.cpp', '.S'))
    if not inputs:
        ap.error('no DSP sources found')
    targets = [Path('build') / p.relative_to(source).with_suffix('.o') for p in inputs]
    run(['make', '-s', '-C', source, f'-j{args.jobs}', *targets])
    out = source / ('build/dmodem-hs' if args.handshake_probe else
                    'build/dmodem-ec' if args.ec_probe else 'build/dmodem')
    out.mkdir(parents=True, exist_ok=True)
    objects = [source / p for p in targets]
    creator = next(p for p in objects if p.name == 'VpcmFloModem.o')
    private = out / creator.name
    run(['objcopy', '--redefine-sym', 'VPCMXF_Create=__blob_VPCMXF_Create',
         creator, private])
    objects[objects.index(creator)] = private
    if args.ec_probe:
        renames = {'V34RX.o': ('modem_serrint', 'diag_real_modem_serrint'),
                   'V34TX.o': ('adaptecho', 'diag_real_adaptecho'),
                   'V92EchoCanceller.o': ('_ZN16V92EchoCanceller7processEPfS0_j', 'diag_real_v92_process')}
        for name, (symbol, renamed) in renames.items():
            original = next(p for p in objects if p.name == name)
            copy = out / name
            run(['objcopy', '--redefine-sym', f'{symbol}={renamed}', original, copy])
            objects[objects.index(original)] = copy
        wrapper = out / 'ec_probe.o'
        run(['g++', '-m32', '-O2', '-g', '-fno-pie', '-fno-exceptions', '-fno-rtti',
             '-nostdinc++', '-I' + str(source / 'include'),
             '-c', Path(__file__).with_name('ec_probe.cpp'), '-o', wrapper])
        objects.append(wrapper)
    if args.handshake_probe:
        for name,symbol in (('V34hshak.o','v34handshak'),('DPSK.o','fskdemodulate')):
            original=next(p for p in objects if p.name==name)
            copy=out/name
            run(['objcopy','--redefine-sym',f'{symbol}=diag_real_{symbol}',original,copy])
            objects[objects.index(original)]=copy
        wrapper=out/'handshake_probe.o'
        run(['gcc','-m32','-O2','-g','-fno-pie','-I'+str(source/'include'),
             '-c',Path(__file__).with_name('handshake_probe.c'),'-o',wrapper])
        objects.append(wrapper)
    result = out / 'dsplibs.o'
    run(['ld', '-m', 'elf_i386', '-r', '-o', result, *objects])
    commit = subprocess.check_output(['git', '-C', source, 'rev-parse', 'HEAD'], text=True).strip()
    manifest = dict(source_commit=commit, translation_units=len(inputs),
                    sha256=hashlib.sha256(result.read_bytes()).hexdigest(),
                    proprietary_objects=0, object=str(result))
    manifest['ec_probe'] = args.ec_probe
    manifest['handshake_probe'] = args.handshake_probe
    manifest['compiler'] = subprocess.check_output(['gcc', '--version'], text=True).splitlines()[0]
    manifest['source_modified'] = subprocess.run(
        ['git', '-C', source, 'diff', '--quiet', '--', 'src', 'include', 'Makefile']).returncode != 0
    (out / 'build.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
