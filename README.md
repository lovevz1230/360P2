# ImmortalWrt 25.12.2 — experimental 360 P2 full-source build

This repository is a build recipe and a small source overlay, **not a prebuilt firmware**. It does not contain the user's EEPROM, complete Flash backup, passwords or wireless configuration.

中文操作说明见 [使用说明.md](使用说明.md)。

## Target

- Device: 360 P2, MT7628AN + MT7612E, 128MiB RAM, 16MiB SPI Flash.
- Source: ImmortalWrt stable tag `v25.12.2`; source archive SHA256 is pinned in `source.lock.json`.
- Feed commits: pinned to the official 25.12.2 `feeds.buildinfo`.
- Independent device identity: `360,p2`, target profile `360_p2`.
- Protected partitions: bootloader `0x0+0x30000`, environment `0x30000+0x10000`, factory `0x40000+0x10000`.
- Firmware partition: `0x50000+0xFB0000`, ending exactly at 16MiB.
- Wi-Fi calibration: original factory offsets 0 (2.4GHz) and 0x8000 (5GHz); no EEPROM data embedded in firmware.
- USB: EHCI/OHCI enabled; storage and ext4 drivers included.
- OpenClash prerequisites: TUN, nftables TPROXY and inet-diag modules are built into the image's package set and required by the artifact manifest check. The OpenClash application, proxy core and databases must be installed separately on suitable writable storage; they are not embedded in the 16 MiB image.
- Network: follows the established MT7628/RT-AC1200/HC5861B switch group, port 0 WAN and ports 1–4 LAN. WAN MAC is base factory MAC + 1.
- Initial GPIO assumptions: reset 38, system LED 37, Wi-Fi LED 44. These follow the related MT7628 board configuration and **have not been validated on this P2 under Linux**. The source contains no ASUS GPIO4 LED-power hog or GPIO11 WPS button.
- Initial SPI clock is a conservative 12MHz, matching the observed Breed display.

## Run on GitHub Actions

1. Put this directory's contents at a GitHub repository root. Include the hidden `.github` directory. Do not upload the user's Desktop backup folder.
2. Open **Actions → Build ImmortalWrt for 360 P2 → Run workflow**.
3. The workflow downloads and verifies the complete stable source, applies the overlay, downloads pinned feeds, builds the cross compiler/kernel/root filesystem from source, then checks the resulting images.
4. Download the Actions artifact. It contains the initramfs and sysupgrade images only if their static checks pass, plus configuration, package manifest, hashes and logs. A failed run may upload logs without usable firmware; check the run status and `artifact-checks.json`.

The workflow is manual-only and has no scheduled runs or automatic releases. It uses an ephemeral GitHub-hosted Ubuntu runner, reclaims its fixed preinstalled Android/.NET/other tool directories for disk space, and installs compiler dependencies. It does not execute this cleanup on Windows or self-hosted runners. Actions usage is charged against the selected account's applicable quota/plan; this recipe does not purchase capacity.

## Local Linux alternative

Run as a normal user, in a case-sensitive Linux filesystem; install the dependencies listed in the workflow first:

```bash
P2_BUILD_ROOT=/tmp/immortalwrt-p2-build P2_JOBS=2 bash build.sh
```

Do not compile under a Windows-mounted case-insensitive source directory. The default source build directory is `/tmp/immortalwrt-p2-build`. The script does not automatically delete or overwrite an unrecognized existing source tree.

## Verification and limitations

```bash
python3 -m unittest discover -s tests -v
python3 verify_image.py path/to/compiled-sysupgrade.bin --type sysupgrade
```

The checker parses the actual embedded DTB and checks the board identity, Flash layout, read-only factory/boot regions, dual-band calibration bindings, Ethernet MAC source, USB controller enablement, PCIe and reset GPIO. It verifies the uImage CRCs, MIPS/Linux image type and image size. For sysupgrade it also checks the SquashFS superblock/table bounds and the fwtool trailer CRC and supported board metadata; it does not fully decompress or test the filesystem. The collector rejects raw DTBs and checks the package manifest for required drivers. Standalone DTBs remain accepted only for source-level preflight checks.

For this ramips image recipe, the board DTB must end exactly at the end of the decompressed kernel. Linux 6.12's internal `drivers/of/empty_root.dtb` is not the appended board description. Tests also reject an interior P2 tree followed by the wrong appended board or unexplained trailing bytes. If validation fails, `diagnostics/` preserves only selected text metadata for investigation; rejected firmware binaries, package files and archives are excluded. Diagnostic text cannot be flashed.

These checks **do not prove the firmware boots or that hardware works**. Before calling this firmware usable, test the physical LAN/WAN ports, both Wi-Fi bands, the actual USB device, reboot/reset and the Breed recovery entry on the user's router. Keep the original complete Flash/EEPROM backups. No flashing operation is automated by this repository.

Do not flash a compiler input, DTB, source archive, or a log-only failed artifact. Installation steps will be determined from the actual successful build output and recovery options, after review.

## Existing extroot installations

A new kernel build can change the kernel package ABI. An existing USB overlay can contain the previous APK package database and files that shadow the new firmware. Preserve a private configuration backup and the old USB overlay, and plan a fresh overlay migration for the new firmware before upgrading. Do not force mismatched kernel packages or automatically reuse an old overlay database. This repository never flashes or modifies a router or USB device.
