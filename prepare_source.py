#!/usr/bin/env python3
"""Apply the small P2 overlay to an otherwise pristine pinned source tree."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

BUNDLE = Path(__file__).resolve().parent


def apply(source):
    marker = source / '.p2-overlay.json'
    overlay_files = ['overlay/mt7628an_360_p2.dts','overlay/p2-device.mk','p2.config','feeds.conf.pinned']
    revision = hashlib.sha256(b''.join((BUNDLE/p).read_bytes() for p in overlay_files)).hexdigest()
    if marker.exists():
        if json.loads(marker.read_text())['overlay_sha256'] != revision:
            raise ValueError('Source has a different P2 overlay; use a fresh build directory')
        print('P2 overlay already applied'); return
    dts = source/'target/linux/ramips/dts/mt7628an_360_p2.dts'
    image = source/'target/linux/ramips/image/mt76x8.mk'
    network = source/'target/linux/ramips/mt76x8/base-files/etc/board.d/02_network'
    im = image.read_text(encoding='utf-8')
    net = network.read_text(encoding='utf-8')
    anchor = '\tasus,rt-ac1200|\\\n'
    if 'Device/360_p2' in im or dts.exists(): raise ValueError('P2 target already exists without overlay marker')
    if net.count(anchor) != 1: raise ValueError('Unexpected upstream interface case; refusing approximate patch')
    net = net.replace(anchor,'\t360,p2|\\\n'+anchor,1)
    mac_start = net.index('ramips_setup_macs()')
    head, mac = net[:mac_start], net[mac_start:]
    case = '\tcase $board in\n'
    if mac.count(case) != 1: raise ValueError('Unexpected upstream MAC case')
    p2_mac = '\t360,p2)\n\t\twan_mac=$(macaddr_add "$(mtd_get_mac_binary factory 0x4)" 1)\n\t\t;;\n'
    mac = mac.replace(case,case+p2_mac,1)
    shutil.copyfile(BUNDLE/'overlay/mt7628an_360_p2.dts',dts)
    image.write_text(im.rstrip()+'\n'+(BUNDLE/'overlay/p2-device.mk').read_text(),encoding='utf-8',newline='\n')
    network.write_text(head+mac,encoding='utf-8',newline='\n')
    shutil.copyfile(BUNDLE/'feeds.conf.pinned',source/'feeds.conf.default')
    shutil.copyfile(BUNDLE/'p2.config',source/'.config')
    marker.write_text(json.dumps({'overlay_sha256':revision,'tag':'v25.12.2'},indent=2)+'\n',encoding='utf-8')
    print('Added 360 P2 target, 16MiB layout, factory calibration bindings, USB and network rules')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('source',type=Path)
    apply(parser.parse_args().source.resolve())
