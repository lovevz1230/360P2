#!/usr/bin/env bash
set -euo pipefail

bundle="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
build_root="${P2_BUILD_ROOT:-/tmp/immortalwrt-p2-build}"
result_dir="${P2_RESULT_DIR:-$bundle/firmware}"
jobs="${P2_JOBS:-2}"
if [[ "$EUID" == 0 ]]; then
  echo 'Compile as an unprivileged Linux user, not root.' >&2
  exit 1
fi
if [[ "$build_root" == *' '* || "$build_root" != /* ]]; then
  echo 'P2_BUILD_ROOT must be an absolute Linux path without spaces.' >&2
  exit 1
fi
for command in python3 make git curl tar patch gcc g++; do
  command -v "$command" >/dev/null || { echo "Missing build dependency: $command" >&2; exit 1; }
done
mkdir -p "$build_root" "$result_dir"
python3 -m unittest discover -s "$bundle/tests" -v
archive="$build_root/immortalwrt-v25.12.2.tar.gz"
if [[ ! -f "$archive" ]]; then
  curl --fail --location --retry 3 \
    'https://codeload.github.com/immortalwrt/immortalwrt/tar.gz/refs/tags/v25.12.2' \
    --output "$archive.part"
  mv -- "$archive.part" "$archive"
fi
python3 - "$bundle/source.lock.json" "$archive" <<'PY'
import hashlib,json,pathlib,sys
lock=json.loads(pathlib.Path(sys.argv[1]).read_text())
actual=hashlib.sha256(pathlib.Path(sys.argv[2]).read_bytes()).hexdigest()
if actual != lock['sha256']: raise SystemExit('Source archive SHA256 mismatch')
print('Source archive matches pinned stable release')
PY
source_dir="$build_root/source"
if [[ ! -d "$source_dir" ]]; then
  mkdir "$source_dir"
  tar --extract --gzip --file "$archive" --directory "$source_dir" --strip-components=1
elif [[ ! -f "$source_dir/.p2-overlay.json" ]]; then
  echo 'Existing source directory has no P2 marker; use a new P2_BUILD_ROOT.' >&2
  exit 1
fi
python3 "$bundle/prepare_source.py" "$source_dir"
cp -- "$bundle/source.lock.json" "$bundle/p2.config" "$bundle/feeds.conf.pinned" "$result_dir/"
cp -- "$bundle/overlay/mt7628an_360_p2.dts" "$result_dir/"
cd "$source_dir"
{
  echo '=== Build environment ==='
  date -u
  uname -a
  df -h .
  echo '=== Pinned feeds ==='
  ./scripts/feeds update -a
  ./scripts/feeds install -a
  echo '=== Device configuration ==='
  make defconfig
  for selection in \
    CONFIG_TARGET_ramips_mt76x8_DEVICE_360_p2 \
    CONFIG_PACKAGE_kmod-mt76x2 CONFIG_PACKAGE_kmod-usb2 \
    CONFIG_PACKAGE_kmod-usb-ohci CONFIG_PACKAGE_kmod-usb-storage \
    CONFIG_PACKAGE_kmod-fs-ext4 CONFIG_PACKAGE_block-mount \
    CONFIG_PACKAGE_luci CONFIG_PACKAGE_luci-i18n-base-zh-cn; do
    grep -qx "$selection=y" .config || { echo "Required selection missing: $selection" >&2; exit 1; }
  done
  cp .config "$result_dir/config.full"
  echo '=== Source downloads ==='
  make download -j4
  echo '=== Full source compilation ==='
  make -j"$jobs" V=s
  echo '=== Artifact verification ==='
  python3 "$bundle/collect_artifacts.py" "$source_dir" "$result_dir"
} 2>&1 | tee "$result_dir/build.log"

echo "Build and static artifact checks finished. Results: $result_dir"
echo 'On-device boot, Wi-Fi, USB and recovery tests are still required.'
