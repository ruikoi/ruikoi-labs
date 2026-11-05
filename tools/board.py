#!/usr/bin/env python3
"""Download and prepare the pinned third-party Duo S Debian SD image."""
import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess
import urllib.request
from payloads import verify_file

URL = 'https://github.com/Fishwaldo/sophgo-sg200x-debian/releases/download/v1.4.0/duos_sd.img.lz4'
SHA256 = '2061c6f72311cf4803388ff9f8daab0d4f40deefae2ba39ecd271f9f07d82890'
FIP_SHA256 = 'ffc638b8844dfc7ad5dc6d76aa0a75c93aeae0f0ae08a4748dbfea5562846ef9'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True, type=Path)
    a = p.parse_args()
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for tool in ('lz4', 'mcopy'):
        if not shutil.which(tool):
            raise ValueError('Install lz4 and mtools before preparing board images')
    archive = output / 'duos_sd.img.lz4'
    if not archive.exists():
        tmp = archive.with_suffix('.partial')
        urllib.request.urlretrieve(URL, tmp)
        verify_file(tmp, SHA256)
        tmp.replace(archive)
    verify_file(archive, SHA256)
    image = output / 'duos_sd-padded.img'
    if image.exists():
        raise ValueError('Refusing to overwrite an existing SD image')
    with image.open('xb') as dest:
        subprocess.run(['lz4', '-d', '-c', str(archive)], stdout=dest, check=True)
    if image.stat().st_size > 1024 ** 3:
        raise ValueError('Unexpected image capacity; refusing to truncate it')
    with image.open('r+b') as f:
        mbr = f.read(512)
        if mbr[510:512] != b'\x55\xaa':
            raise ValueError('Invalid MBR')
        offset = struct.unpack_from('<I', mbr, 446 + 8)[0] * 512
        f.truncate(1024 ** 3)
    vendor_fip = output / 'vendor-fip.bin'
    subprocess.run(['mcopy', '-i', str(image) + '@@' + str(offset),
                    '::fip.bin', str(vendor_fip)], check=True)
    verify_file(vendor_fip, FIP_SHA256)
    (output / 'source.json').write_text(json.dumps({'source': URL,
        'archive_sha256': SHA256, 'vendor_fip_sha256': FIP_SHA256,
        'capacity': 1024 ** 3, 'scope': 'Third-party Debian image; no ROM/DDR training'}, indent=2) + '\n')
    print('Prepared SD image and vendor FIP; use --firmware to supply a separate FreeRTOS ELF')


if __name__ == '__main__':
    main()
