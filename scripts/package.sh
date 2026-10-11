#!/bin/bash
# Turn a kgdb kernel build into the images to flash:
#
#   images/boot.img         the kernel, as built
#   images/vendor_boot.img  stock vendor_boot with this build's device tree,
#                           flattened ramdisk modules and the debug cmdline
#   images/vendor_dlkm.img  this build's modules, flattened, with stock /etc
#   images/vbmeta.img       stock vbmeta, flashed once with --disable-verity
#
# Run from the repository root after building with --kgdb.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TREE=$ROOT/kernel
DIST=$TREE/out/raviole/dist
FACTORY=$ROOT/factory/oriole-cp1a.260405.005
OUT=$ROOT/images
WORK=$OUT/work
BIN=$TREE/prebuilts/kernel-build-tools/linux-x86/bin

# Appended to the stock vendor_boot command line.
DEBUG_CMDLINE="nowatchdog nosoftlockup softlockup_panic=0 hung_task_panic=0"
DEBUG_CMDLINE+=" sysrq_always_enabled arm64.nopauth"
DEBUG_CMDLINE+=" console=ttyGS0 console=ttyGS1 console=ttyGS2 console=ttyGS3 console=ttyGS4"
DEBUG_CMDLINE+=" kgdb_acm kgdboc=ttyGS3 kgdb_break_after=90"

# Modules that reset or panic the phone while the kernel is stopped.
export DROP_MODULES="ehld.ko hardlockup-debug.ko hardlockup-watchdog.ko"

for f in "$FACTORY/image-oriole-cp1a.260405.005.zip" "$DIST/boot.img" \
	 "$DIST/dtb.img" "$DIST/initramfs.img" "$DIST/vendor_dlkm.img"; do
	[ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK"

echo "== stock images from the factory image"
unzip -q -o "$FACTORY/image-oriole-cp1a.260405.005.zip" \
	vendor_boot.img vendor_dlkm.img vbmeta.img -d "$WORK/stock"
python3 "$TREE/tools/mkbootimg/unpack_bootimg.py" \
	--boot_img "$WORK/stock/vendor_boot.img" --out "$WORK/stock/vendor_boot" \
	--format=mkbootimg > "$WORK/stock/vendor_boot.args"

echo "== vendor_dlkm"
mkdir -p "$WORK/dlkm" "$WORK/dlkm-flat/lib/modules"
debugfs -R "rdump / $WORK/dlkm" "$DIST/vendor_dlkm.img" 2>/dev/null
debugfs -R "rdump /etc $WORK/dlkm-flat" "$WORK/stock/vendor_dlkm.img" 2>/dev/null
RELEASE=$(ls "$WORK/dlkm/lib/modules")
python3 "$ROOT/scripts/flatten.py" "$WORK/dlkm/lib/modules/$RELEASE" \
	"$WORK/dlkm-flat/lib/modules" /vendor/lib/modules
cp "$DIST/vendor_dlkm.modules.blocklist" "$WORK/dlkm-flat/lib/modules/modules.blocklist"
"$BIN/mkuserimg_mke2fs" --journal_size 0 --share_dup_blocks --reserved_percent 0 \
	--label vendor_dlkm "$WORK/dlkm-flat" "$OUT/vendor_dlkm.img" \
	ext4 vendor_dlkm 58720256 \
	"$TREE/private/devices/google/common/sepolicy/vendor_dlkm_file_contexts" \
	> "$WORK/mkuserimg.log" 2>&1

echo "== vendor_boot ramdisk"
mkdir -p "$WORK/ramdisk"
"$BIN/lz4" -d -c "$DIST/initramfs.img" | (cd "$WORK/ramdisk" && cpio -idm --quiet)
cp -a "$WORK/ramdisk" "$WORK/ramdisk-flat"
rm -rf "$WORK/ramdisk-flat/lib/modules"
python3 "$ROOT/scripts/flatten.py" "$WORK/ramdisk/lib/modules/$RELEASE" \
	"$WORK/ramdisk-flat/lib/modules" /lib/modules
"$BIN/mkbootfs" "$WORK/ramdisk-flat" > "$WORK/ramdisk-flat.cpio"
"$BIN/lz4" -l -12 -q -f "$WORK/ramdisk-flat.cpio" "$WORK/ramdisk-flat.img"

echo "== vendor_boot"
# Rebuild with the stock header values, swapping in this build's device
# tree and module ramdisk and appending the debug command line.
python3 - "$WORK/stock/vendor_boot.args" "$DIST/dtb.img" \
	"$WORK/ramdisk-flat.img" "$DEBUG_CMDLINE" > "$WORK/vendor_boot.args" <<'PY'
import shlex, sys
args = shlex.split(open(sys.argv[1]).read())
dtb, ramdisk, extra = sys.argv[2:5]
for i, a in enumerate(args):
    if a == "--dtb":
        args[i + 1] = dtb
    elif a == "--vendor_cmdline":
        args[i + 1] += " " + extra
    elif a == "--ramdisk_name" and args[i + 1] == "dlkm":
        assert args[i + 2] == "--vendor_ramdisk_fragment"
        args[i + 3] = ramdisk
print("\n".join(args))
PY
mapfile -t ARGS < "$WORK/vendor_boot.args"
python3 "$TREE/tools/mkbootimg/mkbootimg.py" "${ARGS[@]}" \
	--vendor_boot "$OUT/vendor_boot.img"

cp "$DIST/boot.img" "$OUT/boot.img"
cp "$WORK/stock/vbmeta.img" "$OUT/vbmeta.img"

echo "== done"
for f in boot vendor_boot vendor_dlkm vbmeta; do
	printf 'images/%-16s %10s bytes\n' "$f.img" "$(stat -c%s "$OUT/$f.img")"
done
