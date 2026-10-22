"""Artifact-boundary tests: reject unsafe layouts and mismatched hardware."""
import copy
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def cells(*values):
    return struct.pack('>' + 'I' * len(values), *values)


def node(name, properties=None, children=None):
    return [name, properties or {}, children or []]


def fixture():
    return node('', {'compatible': b'360,p2\0mediatek,mt7628an-soc\0'}, [
        node('keys', {}, [node('reset', {'gpios': cells(1, 38, 1)})]),
        node('gpio', {'phandle': cells(1)}),
        node('palmbus@10000000', {}, [node('spi@b00', {}, [node('flash@0', {}, [
            node('partitions', {}, [
                node('partition@0', {'label': b'u-boot\0', 'reg': cells(0, 0x30000), 'read-only': b''}),
                node('partition@30000', {'label': b'u-boot-env\0', 'reg': cells(0x30000, 0x10000), 'read-only': b''}),
                node('partition@40000', {'label': b'factory\0', 'reg': cells(0x40000, 0x10000), 'read-only': b''}, [
                    node('nvmem-layout', {}, [
                        node('eeprom@0', {'phandle': cells(3), 'reg': cells(0, 0x400)}),
                        node('eeprom@8000', {'phandle': cells(4), 'reg': cells(0x8000, 0x200)}),
                        node('macaddr@4', {'phandle': cells(5), 'reg': cells(4, 6)}),
                    ]),
                ]),
                node('partition@50000', {'label': b'firmware\0', 'compatible': b'denx,uimage\0', 'reg': cells(0x50000, 0xFB0000)}),
            ]),
        ])])]),
        node('ethernet@10100000', {'compatible': b'ralink,rt5350-eth\0', 'nvmem-cells': cells(5), 'nvmem-cell-names': b'mac-address\0'}),
        node('esw@10110000', {'mediatek,portmap': cells(0x3e)}),
        node('wmac@10300000', {'compatible': b'mediatek,mt7628-wmac\0', 'status': b'okay\0', 'nvmem-cells': cells(3), 'nvmem-cell-names': b'eeprom\0'}),
        node('pcie@10140000', {'compatible': b'mediatek,mt7620-pci\0', 'status': b'okay\0'}, [
            node('wifi@0,0', {'compatible': b'mediatek,mt76\0', 'nvmem-cells': cells(4), 'nvmem-cell-names': b'eeprom\0'}),
        ]),
        node('usbphy@10120000', {'compatible': b'mediatek,mt7628-usbphy\0', 'status': b'okay\0'}),
        node('ehci@101c0000', {'compatible': b'generic-ehci\0', 'status': b'okay\0'}),
        node('ohci@101c1000', {'compatible': b'generic-ohci\0', 'status': b'okay\0'}),
    ])


def encode(tree):
    names, strings = {}, bytearray()
    def token(n):
        output = bytearray(cells(1)) + n[0].encode() + b'\0'
        output += bytes((-len(output)) % 4)
        for key, value in n[1].items():
            if key not in names:
                names[key] = len(strings); strings.extend(key.encode() + b'\0')
            output += cells(3, len(value), names[key]) + value
            output += bytes((-len(output)) % 4)
        for child in n[2]: output += token(child)
        return output + cells(2)
    structure = token(tree) + cells(9)
    return cells(0xd00dfeed, 56 + len(structure) + len(strings), 56, 56 + len(structure), 40, 17, 16, 0, len(strings), len(structure)) + bytes(16) + structure + strings


def find(tree, name):
    if tree[0] == name: return tree
    for child in tree[2]:
        found = find(child, name)
        if found is not None: return found


class VerifyArtifact(unittest.TestCase):
    def check(self, tree):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'board.dtb'; path.write_bytes(encode(tree))
            run = subprocess.run([sys.executable, str(ROOT/'verify_image.py'), str(path)], capture_output=True, text=True, encoding='utf-8')
            self.assertTrue(run.stdout.startswith('{'), 'Checker must produce a structured result, not crash: ' + run.stderr)
            return run.returncode, json.loads(run.stdout)

    def test_accepts_matching_p2_artifact(self):
        code, result = self.check(fixture())
        self.assertEqual(code, 0, result)
        self.assertTrue(result['ok'])

    def test_rejects_32mib_layout_even_when_image_file_is_small(self):
        tree = fixture(); find(tree,'partition@50000')[1]['reg'] = cells(0x50000,0x1fb0000)
        code, result = self.check(tree)
        self.assertEqual(code, 1, result)
        self.assertIn('firmware_layout', result['errors'])

    def test_rejects_disabled_usb_controller(self):
        tree = fixture(); find(tree,'ehci@101c0000')[1]['status'] = b'disabled\0'
        code, result = self.check(tree)
        self.assertEqual(code, 1, result)
        self.assertIn('usb_ehci_disabled',result['errors'])

    def test_rejects_5ghz_calibration_bound_to_wrong_region(self):
        tree = fixture(); find(tree,'wifi@0,0')[1]['nvmem-cells'] = cells(3)
        code, result = self.check(tree)
        self.assertEqual(code, 1, result)
        self.assertIn('calibration_5g',result['errors'])

    def test_rejects_writable_factory_partition(self):
        tree = fixture(); del find(tree,'partition@40000')[1]['read-only']
        code, result = self.check(tree)
        self.assertEqual(code, 1, result)
        self.assertIn('factory_writable',result['errors'])

    def test_rejects_unrelated_board(self):
        tree = fixture(); tree[1]['compatible'] = b'asus,rt-ac1200\0'
        code, result = self.check(tree)
        self.assertEqual(code, 1, result)
        self.assertIn('board_identity',result['errors'])


if __name__ == '__main__': unittest.main()
