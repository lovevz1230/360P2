#!/usr/bin/env python3
"""Only publish artifacts that contain the expected P2 hardware definition."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

BUNDLE = Path(__file__).resolve().parent


def collect(source, output):
    target = source/'bin/targets/ramips/mt76x8'
    images = sorted(target.glob('*-360_p2-*.bin'))
    sysup = [p for p in images if p.name.endswith('-squashfs-sysupgrade.bin')]
    initramfs = [p for p in images if p.name.endswith('-initramfs-kernel.bin')]
    if len(sysup) != 1 or len(initramfs) != 1:
        raise ValueError('Expected exactly one P2 sysupgrade and one initramfs image')
    output.mkdir(parents=True,exist_ok=True)
    checks = []
    images = initramfs+sysup
    for image in images:
        image_type = 'sysupgrade' if image == sysup[0] else 'initramfs'
        run = subprocess.run([sys.executable,str(BUNDLE/'verify_image.py'),str(image),'--type',image_type],capture_output=True,text=True,encoding='utf-8')
        if run.returncode:
            raise ValueError('Artifact rejected: '+run.stdout+run.stderr)
        check = json.loads(run.stdout)
        if check.get('kind') != 'uimage' or check.get('image_type') != image_type:
            raise ValueError('Unexpected firmware image type')
        checks.append(check)
    manifest = target/(sysup[0].name.removesuffix('-squashfs-sysupgrade.bin')+'.manifest')
    if not manifest.is_file(): raise ValueError('Package manifest missing')
    package_names = {line.split()[0] for line in manifest.read_text().splitlines() if line.split()}
    required = {'kmod-mt7603','kmod-mt76x2','kmod-usb2','kmod-usb-ohci','kmod-usb-storage','block-mount','kmod-fs-ext4','luci-base'}
    if not required <= package_names:
        raise ValueError('Required packages absent from built manifest: '+str(sorted(required-package_names)))
    # Do not expose any flashable output until both images and package selection pass.
    for image in images:
        shutil.copyfile(image,output/image.name)
    shutil.copyfile(manifest,output/manifest.name)
    for name in ('feeds.buildinfo','version.buildinfo','config.buildinfo','profiles.json'):
        path = target/name
        if path.is_file(): shutil.copyfile(path,output/name)
    (output/'artifact-checks.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines = [hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name for p in sorted(output.glob('*.bin'))]
    (output/'SHA256SUMS').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Both P2 image device trees and the package manifest passed static checks')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('source',type=Path); parser.add_argument('output',type=Path)
    args = parser.parse_args(); collect(args.source,args.output)
