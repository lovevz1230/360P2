"""A failed build verification must not expose flashable binary outputs."""
import lzma
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib

from test_verify_image import encode, fixture

ROOT = Path(__file__).resolve().parents[1]
OPENCLASH_MODULES = ['kmod-tun', 'kmod-nft-tproxy', 'kmod-inet-diag']
PACKAGES = ['kmod-mt7603','kmod-mt76x2','kmod-usb2','kmod-usb-ohci','kmod-usb-storage','block-mount','kmod-fs-ext4','luci-base'] + OPENCLASH_MODULES


def image(kernel=None):
    payload = lzma.compress(encode(fixture()) if kernel is None else kernel,format=lzma.FORMAT_ALONE)
    header = bytearray(struct.pack('>7I4B32s',0x27051956,0,0,len(payload),0x80000000,0x80000000,zlib.crc32(payload),5,5,2,3,b'test kernel'))
    struct.pack_into('>I',header,4,zlib.crc32(header))
    return bytes(header)+payload


def sysupgrade(board='360,p2', truncated=False):
    kernel = image()
    # Structurally bounded test superblock, not a bootable filesystem fixture.
    rootfs = struct.pack('<5I6H8Q',0x73717368,1,0,65536,0,4,16,0,1,4,0,
                         0,128,100,0xffffffffffffffff,96,100,0xffffffffffffffff,0xffffffffffffffff)+bytes(32)
    if truncated: rootfs = rootfs[:100]
    data = kernel+rootfs
    metadata = bytes(8)+json.dumps({'supported_devices':[board]}).encode()
    data += metadata
    return data+struct.pack('>IIB3sI',0x46577830,zlib.crc32(data)^0xffffffff,1,bytes(3),len(metadata)+16)


class CollectArtifacts(unittest.TestCase):
    def run_collect(self, folder, packages, corrupt_second=False, raw=False, second=None):
        source = Path(folder)/'source'
        target = source/'bin/targets/ramips/mt76x8'; target.mkdir(parents=True)
        prefix = 'immortalwrt-test-ramips-mt76x8-360_p2'
        (target/(prefix+'-initramfs-kernel.bin')).write_bytes(encode(fixture()) if raw else image())
        (target/(prefix+'-squashfs-sysupgrade.bin')).write_bytes(b'corrupt' if corrupt_second else second if second is not None else encode(fixture()) if raw else sysupgrade())
        (target/(prefix+'.manifest')).write_text('\n'.join(p+' - 1' for p in packages))
        output = Path(folder)/'output'
        run = subprocess.run([sys.executable,str(ROOT/'collect_artifacts.py'),str(source),str(output)],capture_output=True,text=True)
        return run,output

    def test_missing_usb_module_publishes_no_binary(self):
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,[p for p in PACKAGES if p!='kmod-usb2'])
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(output.glob('*.bin')),[])

    def test_missing_openclash_module_publishes_no_binary(self):
        for module in OPENCLASH_MODULES:
            with self.subTest(module=module), tempfile.TemporaryDirectory() as folder:
                run,output = self.run_collect(folder,[p for p in PACKAGES if p!=module])
                self.assertNotEqual(run.returncode,0)
                self.assertIn(module,run.stderr)
                self.assertEqual(list(output.glob('*.bin')),[])

    def test_failure_diagnostics_exclude_firmware_packages_and_archives(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'source'
            target = source/'bin/targets/ramips/mt76x8'
            target.mkdir(parents=True)
            metadata = {
                'profiles.json': '{"profiles": {}}',
                'config.buildinfo': 'CONFIG_TARGET_ramips=y',
                'immortalwrt-ramips-mt76x8-360_p2.manifest': 'kernel - 1',
            }
            for name,content in metadata.items():
                (target/name).write_text(content)
            for name in ['rejected-360_p2-squashfs-sysupgrade.bin',
                         'rejected-360_p2-initramfs-kernel.bin',
                         'kernel.apk', 'UNVERIFIED-target-output.tar.gz',
                         'private-key.pem']:
                (target/name).write_bytes(b'must not be uploaded')
            output = Path(folder)/'output'
            run = subprocess.run([sys.executable,str(ROOT/'collect_artifacts.py'),
                                  str(source),str(output),'--diagnostics-only'],
                                 capture_output=True,text=True)
            self.assertEqual(run.returncode,0,run.stderr)
            diagnostics = output/'diagnostics'
            self.assertEqual({p.name for p in diagnostics.iterdir()},
                             set(metadata)|{'DO-NOT-FLASH.txt'})
            for name,content in metadata.items():
                self.assertEqual((diagnostics/name).read_text(),content)

    def test_corrupt_second_image_does_not_publish_first(self):
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,PACKAGES,corrupt_second=True)
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(output.glob('*.bin')),[])

    def test_matching_images_and_packages_publish_both(self):
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,PACKAGES)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertEqual(len(list(output.glob('*.bin'))),2)
            self.assertTrue((output/'artifact-checks.json').is_file())
            self.assertEqual(len((output/'SHA256SUMS').read_text().splitlines()),2)

    def test_raw_dtbs_are_not_published_as_firmware(self):
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,PACKAGES,raw=True)
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(output.glob('*.bin')),[])

    def test_kernel_without_rootfs_is_not_sysupgrade(self):
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,PACKAGES,second=image())
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(output.glob('*.bin')),[])

    def test_wrong_upgrade_board_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,PACKAGES,second=sysupgrade(board='asus,rt-ac1200'))
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(output.glob('*.bin')),[])

    def test_truncated_rootfs_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,PACKAGES,second=sysupgrade(truncated=True))
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(output.glob('*.bin')),[])

    def test_corrupt_rootfs_crc_is_rejected(self):
        damaged = bytearray(sysupgrade()); damaged[len(image())+100] ^= 1
        with tempfile.TemporaryDirectory() as folder:
            run,output = self.run_collect(folder,PACKAGES,second=damaged)
            self.assertNotEqual(run.returncode,0)
            self.assertEqual(list(output.glob('*.bin')),[])


if __name__ == '__main__': unittest.main()
