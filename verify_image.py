#!/usr/bin/env python3
"""Check a compiled P2 DTB/uImage without writing anything to a router."""
import argparse
import hashlib
import json
import lzma
from pathlib import Path
import struct
import sys
import zlib

FLASH_BYTES = 0x1000000
FIRMWARE_BYTES = 0xFB0000


def u32s(raw):
    if len(raw) % 4: raise ValueError('unaligned_cells')
    return list(struct.unpack('>' + 'I' * (len(raw)//4), raw))


def strings(raw):
    return raw.rstrip(b'\0').decode('ascii').split('\0') if raw else []


def parse_dtb(blob):
    if len(blob) < 40: raise ValueError('truncated_dtb')
    h = struct.unpack_from('>10I', blob)
    magic, total, st, ss, reserve, version, compatible, boot, string_size, structure_size = h
    if magic != 0xD00DFEED or not (40 <= reserve < total <= len(blob)):
        raise ValueError('invalid_dtb_header')
    if not (40 <= st < st+structure_size <= total and 40 <= ss <= ss+string_size <= total):
        raise ValueError('invalid_dtb_bounds')
    table = blob[ss:ss+string_size]
    pos, end, stack, nodes, finished = st, st+structure_size, [], {}, False
    while pos+4 <= end:
        token = struct.unpack_from('>I',blob,pos)[0]; pos += 4
        if token == 1:
            null = blob.index(0,pos,end)
            name = blob[pos:null].decode('ascii'); pos = (null+4)&~3
            stack.append(name)
            path = '/' + '/'.join(x for x in stack if x)
            if path in nodes: raise ValueError('duplicate_node')
            nodes[path] = {}
        elif token == 2:
            if not stack: raise ValueError('unbalanced_nodes')
            stack.pop()
        elif token == 3:
            if not stack or pos+8 > end: raise ValueError('invalid_property')
            length, off = struct.unpack_from('>II',blob,pos); pos += 8
            if off >= len(table) or pos+length > end: raise ValueError('invalid_property_bounds')
            name = table[off:table.index(0,off)].decode('ascii')
            path = '/'+'/'.join(x for x in stack if x)
            if name in nodes[path]: raise ValueError('duplicate_property')
            nodes[path][name] = blob[pos:pos+length]; pos = (pos+length+3)&~3
        elif token == 4: pass
        elif token == 9:
            if stack: raise ValueError('unbalanced_nodes')
            finished = True; break
        else: raise ValueError('invalid_token')
    if not finished or '/' not in nodes: raise ValueError('unterminated_dtb')
    return nodes


def validate_board(nodes):
    errors = []
    def require(condition, key):
        if not condition: errors.append(key)
    def by_compatible(text):
        return [p for p,v in nodes.items() if text in strings(v.get('compatible',b''))]
    def enabled(path):
        while path:
            if strings(nodes.get(path,{}).get('status',b'okay')) not in (['okay'],['ok']): return False
            path = path.rsplit('/',1)[0]
        return True
    require('360,p2' in strings(nodes['/'].get('compatible',b'')), 'board_identity')
    parts = {strings(v['label'])[0]:(p,v) for p,v in nodes.items() if '/partitions/' in p and p.count('/') == p.split('/partitions/')[0].count('/')+2 and 'label' in v}
    for label, offset, size in [('u-boot',0,0x30000),('u-boot-env',0x30000,0x10000),('factory',0x40000,0x10000),('firmware',0x50000,FIRMWARE_BYTES)]:
        p,v = parts.get(label,('',{}))
        require(u32s(v.get('reg',b'')) == [offset,size], label.replace('-','_')+'_layout')
        if label != 'firmware': require('read-only' in v, label.replace('-','_')+'_writable')
    ranges = []
    for label,(p,v) in parts.items():
        reg = u32s(v.get('reg',b''))
        if len(reg) == 2:
            require(0 < reg[1] and reg[0]+reg[1] <= FLASH_BYTES,'partition_out_of_flash')
            ranges.append((reg[0],reg[0]+reg[1]))
    ranges.sort()
    require(all(a[1] <= b[0] for a,b in zip(ranges,ranges[1:])), 'overlapping_partitions')
    handles = {}
    for p,v in nodes.items():
        for key in ('phandle','linux,phandle'):
            if key in v:
                values = u32s(v[key])
                if len(values) != 1: raise ValueError('invalid_phandle')
                if values[0] in handles and handles[values[0]] != p: raise ValueError('duplicate_phandle')
                handles[values[0]] = p
    factory_path = parts.get('factory',('',{}))[0]
    def binding(path, name, expected):
        v = nodes.get(path,{})
        names, refs = strings(v.get('nvmem-cell-names',b'')), u32s(v.get('nvmem-cells',b''))
        if len(names) != len(refs) or name not in names: return False
        target = handles.get(refs[names.index(name)],'')
        return bool(factory_path and target.startswith(factory_path+'/') and u32s(nodes.get(target,{}).get('reg',b'')) == expected)
    wm = by_compatible('mediatek,mt7628-wmac')
    require(len(wm)==1 and enabled(wm[0]) and binding(wm[0],'eeprom',[0,0x400]),'calibration_2g')
    pci = by_compatible('mediatek,mt7620-pci')
    wifi = by_compatible('mediatek,mt76')
    require(len(pci)==1 and enabled(pci[0]), 'pcie_disabled')
    require(len(wifi)==1 and pci and wifi[0].startswith(pci[0]+'/') and enabled(wifi[0]) and binding(wifi[0],'eeprom',[0x8000,0x200]),'calibration_5g')
    eth = by_compatible('ralink,rt5350-eth')
    require(len(eth)==1 and binding(eth[0],'mac-address',[4,6]),'ethernet_mac_source')
    for compatible,key in [('generic-ehci','usb_ehci_disabled'),('generic-ohci','usb_ohci_disabled'),('mediatek,mt7628-usbphy','usb_phy_disabled')]:
        paths = by_compatible(compatible)
        require(len(paths)==1 and enabled(paths[0]),key)
    resets = [v for p,v in nodes.items() if p.endswith('/reset') and 'gpios' in v]
    require(len(resets)==1 and u32s(resets[0]['gpios'])[1:] == [38,1], 'reset_gpio')
    # MAC addresses should come from the original calibration blob, not another board's override.
    for path in wm+wifi:
        require('mac-address' not in strings(nodes[path].get('nvmem-cell-names',b'')), 'unexpected_wifi_mac_override')
    return list(dict.fromkeys(errors))


def upgrade_body(data):
    """Read the fwtool trailer format (openwrt/fwtool fwimage.h/fwtool.c)."""
    end = len(data)
    for _ in range(4):
        if end < 24: raise ValueError('upgrade_metadata_missing')
        magic, crc, kind, padding, size = struct.unpack_from('>IIB3sI',data,end-16)
        if magic != 0x46577830: raise ValueError('upgrade_metadata_missing')
        if not 16 <= size <= min(end,32*1024): raise ValueError('upgrade_metadata_bounds')
        if crc != zlib.crc32(data[:end-16]) ^ 0xffffffff: raise ValueError('upgrade_metadata_crc')
        start = end-size
        if kind == 0:  # Optional signature follows the information trailer.
            end = start
            continue
        if kind != 1 or size < 24 or data[start:start+8] != bytes(8):
            raise ValueError('upgrade_metadata_header')
        info = json.loads(data[start+8:end-16])
        if not isinstance(info,dict) or not isinstance(info.get('supported_devices'),list) or '360,p2' not in info['supported_devices']:
            raise ValueError('upgrade_metadata_board')
        return data[:start]
    raise ValueError('upgrade_metadata_missing')


def validate_rootfs(body, kernel_end):
    # ramips uses append-rootfs; padding may precede the SquashFS superblock.
    positions = {kernel_end,(kernel_end+4095)&~4095,(kernel_end+65535)&~65535}
    candidates = [p for p in positions if body[p:p+4] == b'hsqs']
    if len(candidates) != 1: raise ValueError('squashfs_missing')
    at = candidates[0]
    if len(body)-at < 96: raise ValueError('squashfs_truncated')
    h = struct.unpack_from('<5I6H8Q',body,at)
    _, inodes, timestamp, block, fragments, compression, block_log, flags, ids, major, minor, root, used, *tables = h
    if not (inodes and ids and major == 4 and minor == 0 and compression == 4 and 12 <= block_log <= 20 and block == 1 << block_log):
        raise ValueError('squashfs_superblock')
    if not 96 < used <= len(body)-at: raise ValueError('squashfs_truncated')
    # id, inode and directory tables are mandatory; other tables may be absent.
    if any(not 96 <= tables[i] < used for i in (0,2,3)):
        raise ValueError('squashfs_table_bounds')
    if any(t != 0xffffffffffffffff and not 96 <= t < used for t in tables):
        raise ValueError('squashfs_table_bounds')


def inspect(data, image_type=None):
    if data[:4] == bytes.fromhex('d00dfeed'):
        if image_type: raise ValueError('firmware_must_be_uimage')
        return validate_board(parse_dtb(data)), 'dtb'
    if len(data) > FIRMWARE_BYTES: raise ValueError('image_too_large')
    if len(data) < 64 or data[:4] != bytes.fromhex('27051956'): raise ValueError('not_uimage')
    h = struct.unpack('>7I4B32s',data[:64])
    header = bytearray(data[:64]); header[4:8] = bytes(4)
    if zlib.crc32(header) != h[1]: raise ValueError('uimage_header_crc')
    if not (0 < h[3] <= len(data)-64): raise ValueError('uimage_payload_size')
    payload = data[64:64+h[3]]
    if zlib.crc32(payload) != h[6]: raise ValueError('uimage_payload_crc')
    if h[7:10] != (5,5,2): raise ValueError('expected_mips_linux_kernel')
    if h[10] != 3: raise ValueError('unsupported_kernel_compression')
    if image_type == 'sysupgrade': validate_rootfs(upgrade_body(data),64+h[3])
    if image_type == 'initramfs' and len(data) != 64+h[3]: raise ValueError('unexpected_initramfs_tail')
    decoder = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
    kernel = decoder.decompress(payload,max_length=32*1024*1024)
    if not decoder.eof: raise ValueError('kernel_decompression_limit')
    candidates, pos, valid_trees = [],0,0
    while True:
        at = kernel.find(bytes.fromhex('d00dfeed'),pos)
        if at < 0: break
        pos = at+4
        try:
            nodes = parse_dtb(kernel[at:])
            valid_trees += 1
            # KERNEL_DTB appends the board DTB after the raw kernel. Linux 6.12
            # also embeds drivers/of/empty_root.dtb inside the kernel itself.
            # Only the DTB that ends exactly at the decompressed image end is
            # the board appended by this target's image recipe.
            total = struct.unpack_from('>I',kernel,at+4)[0]
            if at+total == len(kernel): candidates.append(nodes)
        except (ValueError,IndexError,UnicodeError,struct.error): continue
    if len(candidates) != 1:
        raise ValueError(f'expected_one_appended_dtb(valid={valid_trees},terminal={len(candidates)})')
    return validate_board(candidates[0]), 'uimage'


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('image',type=Path)
    parser.add_argument('--type',choices=['initramfs','sysupgrade'],dest='image_type')
    args = parser.parse_args()
    result = {'ok':False,'file':str(args.image),'errors':[]}
    try:
        data = args.image.read_bytes()
        result.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        image_type = args.image_type
        if image_type is None and args.image.name.endswith('-squashfs-sysupgrade.bin'): image_type = 'sysupgrade'
        if image_type is None and args.image.name.endswith('-initramfs-kernel.bin'): image_type = 'initramfs'
        errors, kind = inspect(data,image_type)
        result.update(kind=kind,image_type=image_type,errors=errors,ok=not errors)
    except (ValueError,OSError,IndexError,UnicodeError,struct.error,lzma.LZMAError) as err:
        result['errors'] = [str(err)]
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__': sys.exit(main())
