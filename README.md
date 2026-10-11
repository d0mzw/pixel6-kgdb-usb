# pixel6-kgdb-usb

Patches and scripts to debug the Pixel 6 kernel with `gdb` over the phone's USB cable. The kernel's debugger, kgdb, talks to `gdb` on the host through a USB serial port the phone adds next to adb. No UART, no debug board, no root.

| Setup | Details |
| --- | --- |
| Phone | Pixel 6 (`oriole`) |
| Android build | `CP1A.260405.005` (Android 16) |
| Kernel branch | `android-gs-raviole-6.1-android16` |
| Stock kernel in that build | `6.1.145-android14-11-gfa1d6308d1fe-ab14691759` |
| Kernel built from the branch | `6.1.124-android14-11` |

## Before you start

You need:

- A Pixel 6 whose bootloader can be unlocked, and a USB cable to the host. Unlocking and flashing wipe the phone, so back it up first.
- An x86-64 Linux host. Ubuntu 26.04 LTS was used here.
- About 25 GB of free disk: 13 GB for the kernel source and its build output, 7 GB for the factory image and its unpacked copy, and room for the images to flash.
- A few minutes per build. A full build took about 4.5 minutes on a 32-thread Intel Core i9-13980HX with 184 GB of RAM.

While the debug kernel is installed, the phone is not for everyday use: it stops in the debugger about 90 seconds after every boot and stays frozen until `gdb` resumes it, and its watchdogs can no longer recover it from a real hang. Flash the factory image to go back.

## Layout

```
pixel6-kgdb-usb/
├── manifest/pinned.xml   exact commit of every kernel project
├── patches/              the changes, one patch per concern
├── docs/                 one document per patch, explaining it
├── scripts/              apply, package and attach helpers
├── requirements.txt      Python packages for scripts/kgdb-attach.py
├── kernel/               kernel source, synced here (ignored by git)
├── factory/              stock factory image, downloaded here (ignored by git)
├── images/               images to flash, made by scripts/package.sh (ignored by git)
└── .venv/                Python virtual environment for the scripts (ignored by git)
```

`kernel/`, `factory/`, `images/` and `.venv/` are created by the steps below and never committed.

## Terms

| Term | Meaning |
| --- | --- |
| kgdb | The debugger built into the Linux kernel. It stops the kernel and speaks the gdb remote protocol. |
| kgdboc | "kgdb over console": connects kgdb to a serial port (a *tty*). |
| CDC-ACM | The standard USB serial port class. On the phone its ports are `ttyGS<n>`; on the host they appear as `/dev/ttyACM<n>`. |
| GKI | Generic Kernel Image, the common kernel Google builds for Android devices. Phone-specific code lives in separate modules. |
| Kleaf | The Bazel rules that build Android kernels. |
| `oriole`, `raviole` | Code names: `oriole` is the Pixel 6, `raviole` the build target shared with the Pixel 6 Pro. |
| fastboot, fastbootd | The bootloader's flashing mode, and a second flashing mode that runs inside a minimal Android and can write the partitions inside `super`. |
| Slot | Pixels keep two copies of most partitions, slots `a` and `b`; the phone boots one. |

## Preparation

### Host tools

Install the tools below, then Google's `repo` tool. `binutils` provides `strings`; `cpio` and `e2fsprogs` are used to repack images; `android-sdk-platform-tools-common` installs the udev rules that let `adb` and `fastboot` reach the phone without `sudo`:

```bash
sudo apt install git curl unzip binutils cpio e2fsprogs gdb-multiarch python3-venv android-sdk-platform-tools-common
mkdir -p ~/bin
curl -fo ~/bin/repo https://storage.googleapis.com/git-repo-downloads/repo
chmod a+x ~/bin/repo
```

`git am` records each patch as a commit, so git needs a name and email. Skip this if `git config user.email` already prints one:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

`adb` and `fastboot` come from Google's [platform-tools](https://developer.android.com/tools/releases/platform-tools); release 37.0.1 was used here. Unpack it into your home folder, replacing any older copy there, and put it first on your `PATH`, ahead of any distribution `adb`:

```bash
curl -fo /tmp/platform-tools.zip https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip
unzip -o -d ~ /tmp/platform-tools.zip
echo 'export PATH="$HOME/platform-tools:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

`source ~/.bashrc` updates the current shell; other terminals pick up the change when they are opened again.

Check that both tools come from that folder:

```bash
which adb fastboot
```

Expected output, with your home folder:

```
/home/you/platform-tools/adb
/home/you/platform-tools/fastboot
```

### Serial port access

With the debug kernel installed, the phone's kgdb port appears on the host as `/dev/ttyACM0` (or the next free number). Two things get in the way of `gdb` opening it: the device belongs to root, and ModemManager, which runs on Ubuntu desktops, takes the port as a possible modem (it advertises the AT command protocol) and may probe it with AT commands, which would land in the middle of the debugger's traffic. One udev rule fixes both, for Google's USB devices only:

```bash
printf '%s\n' \
  'SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="18d1", \' \
  '  MODE="0666", ENV{ID_MM_DEVICE_IGNORE}="1"' \
  | sudo tee /etc/udev/rules.d/99-kgdb-acm.rules
sudo udevadm control --reload-rules
```

The trailing `\` inside the rule is udev's own line continuation, so the file holds one rule over two lines.

The rule applies when the port next appears, the first time the phone boots the debug kernel. Once it has, check it:

```bash
udevadm info -q property /dev/ttyACM0 | grep ID_MM
```

Expected output:

```
ID_MM_CANDIDATE=1
ID_MM_DEVICE_IGNORE=1
```

`ID_MM_CANDIDATE=1` is ModemManager's own rules marking the port as a possible modem; `ID_MM_DEVICE_IGNORE=1` tells it to leave the port alone.

### Clone this repository

```bash
git clone https://github.com/d0mzw/pixel6-kgdb-usb.git
cd pixel6-kgdb-usb
```

All later commands run from this folder unless they say otherwise.

### Python environment

`scripts/kgdb-attach.py` uses Python packages that Ubuntu does not install by default, listed in `requirements.txt`. Install them into a virtual environment, `.venv/`, once:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then activate it in each new shell before running `scripts/kgdb-attach.py`:

```bash
source .venv/bin/activate
```

### Sync the kernel source

The kernel tree is about 80 git projects managed by `repo`. Initialise it on the Pixel 6 branch, then sync with the pinned manifest so every project lands on the exact commit the patches were made against:

```bash
mkdir -p kernel
cd kernel
~/bin/repo init -u https://android.googlesource.com/kernel/manifest -b android-gs-raviole-6.1-android16 --depth=1
~/bin/repo sync -c --no-tags -j8 -m "$PWD/../manifest/pinned.xml"
cd ..
```

`--depth=1` fetches only the pinned commit of each project, not its history. Without it the clang prebuilts project alone is well over 36 GB; with it the whole tree is about 10 GB. When it finishes, `kernel/` includes `aosp/`, `build/`, `private/`, `prebuilts/` and `tools/`.

Syncing without `-m` gives you the tip of the branch instead, which may have moved since the patches were made.

### Download the factory image

The patched kernel is flashed on top of this exact stock build. The factory image is also the way back to stock if anything goes wrong.

```bash
mkdir -p factory
cd factory
curl -fO https://dl.google.com/dl/android/aosp/oriole-cp1a.260405.005-factory-8081a8ba.zip
echo "8081a8ba182a6ae60b155b03c1843fe8dabe285868147cdf9b08c45f458560de  oriole-cp1a.260405.005-factory-8081a8ba.zip" | sha256sum -c
unzip oriole-cp1a.260405.005-factory-8081a8ba.zip
cd ..
```

`sha256sum -c` must print `OK`. The zip is 3.4 GB and unpacks into `factory/oriole-cp1a.260405.005/`.

### Turn on Developer options and USB debugging

On the phone, open **Settings > About phone** and tap **Build number** seven times. Then open **Settings > System > Developer options** and turn on:

- **OEM unlocking**, needed for the next step. It is greyed out on carrier-locked phones, which cannot be unlocked.
- **USB debugging**, so `adb` can reach the phone.

Connect the phone to the host and accept the **Allow USB debugging?** prompt on the phone.

### Check the current build

Before anything is wiped, check which build the phone runs now:

```bash
adb shell getprop ro.build.id
```

The next steps flash `CP1A.260405.005`. If the phone runs a newer build, that is a downgrade. Pixel 6 bootloaders have refused downgrades before: once on Android 13, they could no longer flash Android 12 builds. Read the notes on Google's [factory images page](https://developers.google.com/android/images) before flashing an older release.

### Unlock the bootloader

Flashing a custom kernel needs an unlocked bootloader. **Unlocking wipes the phone**, so back up anything you need first:

```bash
adb reboot bootloader
fastboot flashing unlock
```

Confirm on the phone with the volume keys and the power button. From then on, the phone shows a warning about the unlocked bootloader at every boot. That is expected.

### Flash the stock build

Start from the exact stock build, so the only change later is the kernel. Put the phone in fastboot mode, either with `adb reboot bootloader` or by holding Volume Down + Power while it boots, then check that the host sees it:

```bash
fastboot devices
```

Flash everything with Google's script. `-w` inside it wipes user data:

```bash
cd factory/oriole-cp1a.260405.005
./flash-all.sh
cd ../..
```

The phone reboots into setup when the script finishes. The flash wiped it, so skip through setup and repeat [Turn on Developer options and USB debugging](#turn-on-developer-options-and-usb-debugging). OEM unlocking stays on.

### Check the phone

```bash
adb devices -l
```

The phone must be listed as `device` with `product:oriole`. Then confirm the build and the kernel:

```bash
adb shell getprop ro.product.device             # oriole
adb shell getprop ro.build.id                   # CP1A.260405.005
adb shell getprop ro.boot.verifiedbootstate     # orange (bootloader unlocked)
adb shell uname -r                              # 6.1.145-android14-11-gfa1d6308d1fe-ab14691759
```

`uname -r` is the stock GKI kernel Google ships in this build. The kernel built from the branch is older (`6.1.124`). That is fine here, because every kernel module on the phone is rebuilt from the same tree along with the kernel (see [Package](#package)); nothing built for the stock kernel is loaded. What has to match is the Android release: the rest of the phone stays stock, and packaging reuses the stock ramdisk, `bootconfig` and `vendor_dlkm` files from `CP1A.260405.005`.

The running kernel's configuration can be read without root:

```bash
adb pull /proc/config.gz factory/stock.config.gz
zgrep CONFIG_KGDB factory/stock.config.gz   # "# CONFIG_KGDB is not set"
```

Stock has no kgdb, which is what the patches change.

### Inspect the stock kernel without the phone

The same kernel is inside `boot.img` in the factory image. Unpack it with the tools from the synced tree:

```bash
mkdir -p factory/stock-boot
cd factory/stock-boot
unzip -o ../oriole-cp1a.260405.005/image-oriole-cp1a.260405.005.zip boot.img
python3 ../../kernel/tools/mkbootimg/unpack_bootimg.py --boot_img boot.img --out unpacked
../../kernel/prebuilts/kernel-build-tools/linux-x86/bin/lz4 -d unpacked/kernel Image
strings Image | grep -m1 'Linux version'
sh ../../kernel/aosp/scripts/extract-ikconfig Image > stock.config
cd ../..
```

`strings` prints the same `6.1.145-android14-11-gfa1d6308d1fe-ab14691759` as `uname -r`, and `stock.config` is the full config of the stock kernel.

## Baseline build

Build the unpatched tree once before applying anything. If a patched build fails later, this tells you whether the problem is the patch or the tree and host.

```bash
cd kernel
tools/bazel run \
    --config=stamp \
    --config=raviole \
    --config=pixel_debug_common \
    //private/devices/google/raviole:gs101_raviole_dist
cd ..
```

`--config=raviole` selects the Pixel 6 and 6 Pro. `--config=pixel_debug_common` builds the GKI kernel from source instead of using Google's prebuilt one; without it, changes to the kernel never reach the image. A full build takes a few minutes (about 4.5 on the host in [Before you start](#before-you-start)); later builds reuse Bazel's cache in `kernel/out/` and only redo what changed.

The output lands in `kernel/out/raviole/dist/`. List the images and `vmlinux`:

```bash
ls kernel/out/raviole/dist/ | grep -E '\.img$|^vmlinux$'
```

Expected output:

```
boot.img
dtb.img
dtbo.img
initramfs.img
vendor_dlkm.img
vmlinux
```

Read the kernel version from `vmlinux`:

```bash
strings kernel/out/raviole/dist/vmlinux | grep -m1 'Linux version'
```

Expected output:

```
Linux version 6.1.124-android14-11 (build-user@build-host) (Android (10087095, +pgo, +bolt, +lto, -mlgo, based on r487747c) clang version 17.0.2 (https://android.googlesource.com/toolchain/llvm-project d9f89f4d16663d5012e5c09495f3b30ece3d2362), LLD 17.0.2) #1 SMP PREEMPT Thu Jan  1 00:00:00 UTC 1970
```

`6.1.124-android14-11` is the branch's kernel, built from source. The 1970 date is normal: Kleaf builds are reproducible, so the build timestamp is fixed at zero.

## Apply the patches

Each patch belongs to one project inside `kernel/` and is stored under the same path in `patches/`: `patches/aosp/` holds the patches for `kernel/aosp`, and so on. Every patch has a document under `docs/` with the same path and name, explaining what it changes and why.

| Project | Patch | What it does |
| --- | --- | --- |
| `build/kernel` | [0001](docs/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.md) | Kernel config for kgdb over a USB serial console |
| `aosp` | [0001](docs/aosp/0001-usb-gadget-add-a-polled-mode-implemented-for-dwc3.md) | Lets the USB controller be driven without interrupts |
| `aosp` | [0002](docs/aosp/0002-usb-gadget-u_serial-add-polled-I-O-for-kgdboc.md) | Polled I/O on the USB serial port, for kgdboc |
| `aosp` | [0003](docs/aosp/0003-usb-gadget-configfs-add-a-CDC-ACM-port-for-kgdb-with.md) | `kgdb_acm`: adds the USB serial port without root |
| `aosp` | [0004](docs/aosp/0004-kgdboc-add-kgdb_break_after-to-trap-into-kgdb-after-.md) | `kgdb_break_after=`: stops in kgdb after boot |
| `aosp` | [0005](docs/aosp/0005-scripts-gdb-symbols-use-a-raw-string-for-the-module-.md) | Silences a Python warning in the gdb scripts |
| `private/devices/google/gs101` | [0001](docs/private/devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md) | Device tree: watchdogs do not reset a stopped kernel |
| `private/google-modules/soc/gs` | [0001](docs/private/google-modules/soc/gs/0001-soc-gs-keep-the-watchdogs-from-resetting-a-kgdb-stop.md) | Watchdog drivers: same, in code |

Apply them all:

```bash
scripts/apply-patches.sh
```

For each project, the script runs `repo start kgdb <project>` to put it on a branch named `kgdb`, then `git am` with that project's patches in order. `git am` records each patch as a commit. Expected output in a terminal:

```
== aosp
Starting kgdb: 100% (1/1), done in 0.007s
Applying: usb: gadget: add a polled mode, implemented for dwc3
Applying: usb: gadget: u_serial: add polled I/O for kgdboc
Applying: usb: gadget: configfs: add a CDC-ACM port for kgdb with kgdb_acm
Applying: kgdboc: add kgdb_break_after= to trap into kgdb after boot
Applying: scripts/gdb: symbols: use a raw string for the module pattern
== build/kernel
Starting kgdb: 100% (1/1), done in 0.007s
Applying: kleaf: kgdb: enable kgdb over the USB serial console
== private/devices/google/gs101
Starting kgdb: 100% (1/1), done in 0.007s
Applying: gs101: dts: keep the watchdogs from resetting a kgdb stop
== private/google-modules/soc/gs
Starting kgdb: 100% (1/1), done in 0.007s
Applying: soc: gs: keep the watchdogs from resetting a kgdb stop
```

The `Starting kgdb` lines are `repo`'s progress output, and the time on them varies. When the output goes to a file or a pipe, `repo` leaves them out.

If a patch does not apply, the script stops there. `git -C kernel/<project> am --abort` backs out the failed patch; then check that the sync used `manifest/pinned.xml`. Before running the script again, start over as below, because it would otherwise try to apply the patches that already went in.

To start over from unpatched source, put the four projects back on their pinned commits and delete the branches:

```bash
cd kernel
~/bin/repo forall build/kernel aosp private/devices/google/gs101 private/google-modules/soc/gs -c 'git checkout -q --detach "$REPO_LREV" && git branch -D kgdb'
cd ..
```

It prints one `Deleted branch kgdb (was <commit>)` line per project.

## Build

Build with `--kgdb`, the Kleaf flag the first patch extends:

```bash
cd kernel
tools/bazel run \
    --config=stamp \
    --config=raviole \
    --config=pixel_debug_common \
    --kgdb \
    //private/devices/google/raviole:gs101_raviole_dist
cd ..
```

Check the kernel config the first patch sets, read back from the built kernel:

```bash
sh kernel/aosp/scripts/extract-ikconfig kernel/out/raviole/dist/vmlinux | grep -E '^(# )?CONFIG_(KGDB|KGDB_KDB|KGDB_SERIAL_CONSOLE|CONSOLE_POLL|MAGIC_SYSRQ|WATCHDOG|RANDOMIZE_BASE|STRICT_KERNEL_RWX)[ =]'
```

Expected output:

```
# CONFIG_RANDOMIZE_BASE is not set
CONFIG_STRICT_KERNEL_RWX=y
CONFIG_CONSOLE_POLL=y
CONFIG_WATCHDOG=y
CONFIG_MAGIC_SYSRQ=y
CONFIG_KGDB=y
CONFIG_KGDB_SERIAL_CONSOLE=y
CONFIG_KGDB_KDB=y
```

`STRICT_KERNEL_RWX=y` is expected: on arm64 the option cannot be turned off, so `make olddefconfig` turns it back on, and breakpoints work regardless. The [first patch's doc](docs/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.md#strict_kernel_rwx-stays-on) explains why.

Check that the code from the `aosp` patches is in the kernel:

```bash
grep -E ' (dwc3_gadget_poll|gs_poll_init|gs_poll_get_char|gs_poll_put_char|kgdb_acm_setup|kgdboc_break_fn)$' kernel/out/raviole/dist/System.map
```

Expected output:

```
ffffffc008873290 t kgdboc_break_fn
ffffffc008a00804 t dwc3_gadget_poll
ffffffc008a50f4c t gs_poll_init
ffffffc008a50ffc t gs_poll_get_char
ffffffc008a51108 t gs_poll_put_char
ffffffc009c8d5dc t kgdb_acm_setup
```

The build is reproducible, so the addresses match too.

Check the device tree from the `gs101` patch:

```bash
kernel/prebuilts/kernel-build-tools/linux-x86/bin/dtc -I dtb -O dts -q kernel/out/raviole/dist/dtb.img | grep -E 'use_multistage_wdt;|panic = <'
```

Expected output:

```
		panic = <0x00>;
```

The stock device tree prints `panic = <0x01>;` and `use_multistage_wdt;` here.

## Package

The build's images cannot be flashed as they are: the modules are in a nested layout Android does not read, and `vendor_boot` has to be rebuilt around the stock one with the debug kernel command line. `scripts/package.sh` does both and writes the results to `images/`:

```bash
scripts/package.sh
```

Expected output:

```
== stock images from the factory image
== vendor_dlkm
322 modules, 319 in modules.load, dropped ehld.ko hardlockup-debug.ko hardlockup-watchdog.ko
== vendor_boot ramdisk
203 modules, 200 in modules.load, dropped ehld.ko hardlockup-debug.ko hardlockup-watchdog.ko
== vendor_boot
== done
images/boot.img           53477376 bytes
images/vendor_boot.img    32088064 bytes
images/vendor_dlkm.img    58720256 bytes
images/vbmeta.img            12288 bytes
```

What it does:

- **Flattens the modules.** Kleaf installs them as `lib/modules/<release>/.../foo.ko`; Android ships `lib/modules/foo.ko`, and its first-stage init only reads that layout. Flashed unflattened, no modules load: no display, no touch, no modem. `scripts/flatten.py` rewrites the tree and its `modules.load` and `modules.dep`.
- **Leaves out three lockup-detection modules** from `modules.load`, so they never load. `hardlockup-debug.ko` would reboot the phone when `watchdog_cl1` fires, and no patch changes it. `hardlockup-watchdog.ko` and `ehld.ko` are already made harmless by the patches; leaving them out as well is a precaution. See the [gs101 patch's limits](docs/private/devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md#limits).
- **Rebuilds `vendor_dlkm`** from the flattened modules and the stock image's `/etc`.
- **Rebuilds `vendor_boot`** from the stock one in the factory image, keeping its header values, platform ramdisk and `bootconfig`, but with this build's device tree, the flattened module ramdisk, and the debug command line appended to the stock one.
- **Copies** `boot.img` from the build and the stock `vbmeta.img` from the factory image.

If the script stops with `missing: <file>`, the build output or the factory image is not where it expects; build first, and check that `factory/oriole-cp1a.260405.005/` exists. Any other failure stops the script at the failing step, and its work files are kept in `images/work/`, including `mkuserimg.log` from building `vendor_dlkm`.

The debug command line:

| Parameter | Purpose |
| --- | --- |
| `nowatchdog nosoftlockup` | Turn off the kernel's own lockup detectors |
| `softlockup_panic=0 hung_task_panic=0` | Override the device tree, which makes both panic |
| `sysrq_always_enabled` | Keep SysRq, the kernel's emergency command channel, available |
| `arm64.nopauth` | Turn off pointer authentication, so return addresses in backtraces are plain pointers |
| `kgdb_acm` | Add the USB serial port for kgdb ([aosp 0003](docs/aosp/0003-usb-gadget-configfs-add-a-CDC-ACM-port-for-kgdb-with.md)) |
| `kgdboc=ttyGS3` | Run kgdb over that port, serial line 3 on this phone |
| `kgdb_break_after=90` | Stop in kgdb 90 seconds after boot ([aosp 0004](docs/aosp/0004-kgdboc-add-kgdb_break_after-to-trap-into-kgdb-after-.md)) |

## Flash

Two rules for every flash:

- **Flash `boot`, `vendor_boot` and `vendor_dlkm` together, from one build.** Mixing builds leaves modules that do not match the kernel.
- **Never run `fastboot flashing lock`.** Locking the bootloader with a custom kernel installed can leave the phone unable to boot.

Make sure the factory image is on disk first. It is the way back if anything goes wrong:

```bash
ls factory/oriole-cp1a.260405.005/flash-all.sh
```

Expected output:

```
factory/oriole-cp1a.260405.005/flash-all.sh
```

Reboot into the bootloader and check it is unlocked. If the debug kernel is already installed and the phone is frozen in kgdb, adb cannot reach it; hold Volume Down + Power for about 10 seconds instead.

```bash
adb reboot bootloader
fastboot getvar unlocked
```

Expected output:

```
unlocked: yes
Finished. Total time: 0.005s
```

The time on the `Finished.` lines varies.

**The first time only**, turn off verified boot's integrity checking (dm-verity). The custom `vendor_dlkm` would fail it:

```bash
fastboot --disable-verity flash vbmeta images/vbmeta.img
```

Use `--disable-verity` on its own: when `--disable-verification` was added as well, this phone did not boot. Flashing the stock build with `flash-all.sh` turns verity back on, so repeat this step after that.

Flash the kernel and `vendor_boot` from the bootloader:

```bash
fastboot flash boot images/boot.img
fastboot flash vendor_boot images/vendor_boot.img
```

Each ends with a `Finished.` line. `vendor_dlkm` lives inside the `super` partition, which only fastbootd, a fastboot that runs in a minimal Android, can write. Switch to it:

```bash
fastboot reboot fastboot
```

This takes about 30 seconds and ends with a `Finished.` line. If it fails with `fastboot: error: Failed to boot into userspace fastboot`, run it again (see [Known issues](#known-issues)).

Check that fastbootd is up. Flashing `vendor_dlkm` from the bootloader fails with `Invalid command resize-logical-partition`:

```bash
fastboot getvar is-userspace
```

Expected output, once it is ready (repeat until it says `yes`):

```
is-userspace: yes
Finished. Total time: 0.001s
```

Then flash and boot:

```bash
fastboot flash vendor_dlkm images/vendor_dlkm.img
fastboot reboot
```

The phone boots Android in about 20 seconds. If it lands back in the bootloader instead, run `fastboot reboot` again (see [Known issues](#known-issues)). Once it has booted, check the kernel:

```bash
adb shell uname -r
```

Expected output:

```
6.1.124-android14-11
```

About 90 seconds after boot, the kernel stops in kgdb and the phone freezes. That is expected, and it happens on every boot. If kgdboc is slow to bind its port, the stop can come up to 90 seconds later. The next section attaches gdb.

## Debug

Attach, with the virtual environment active and the [serial port rule](#serial-port-access) in place:

```bash
scripts/kgdb-attach.py
```

The script:

1. Checks the host: `adb`, `fastboot` and `gdb-multiarch` are installed, and the build left `vmlinux`, the kernel's gdb scripts and the sources in place.
2. Checks the phone: it runs the same kernel release as `vmlinux`, and its command line has `kgdb_acm`, `kgdboc=` and `kgdb_break_after=`. If adb does not answer, it waits for a phone that is still booting, and stops with a hint to use `--now` if the kernel already looks stopped.
3. Reboots the phone and shows a progress bar while Android boots. If the phone stops in the bootloader instead (see [Known issues](#known-issues)), it runs `fastboot reboot`, up to three times.
4. Shows a second progress bar while the kernel counts down to `kgdb_break_after`, and notices when it stops.
5. Starts `gdb-multiarch` on `/dev/ttyACM0` with `vmlinux`, the kernel sources and the kernel's gdb helper scripts loaded.

If the kernel has already stopped, `scripts/kgdb-attach.py --now` skips straight to step 5. `--help` lists the other options.

### A demo session

This session was recorded on the phone, with the host set up as in this README. The progress bars clear themselves when they finish, so they do not appear here.

The script's output, up to the moment gdb connects to the stopped kernel:

```
[02:31:01] checking the host
[02:31:01]   adb: /home/you/platform-tools/adb
[02:31:01]   fastboot: /home/you/platform-tools/fastboot
[02:31:01]   gdb-multiarch: /usr/bin/gdb-multiarch
[02:31:01]   vmlinux: kernel/out/raviole/dist/vmlinux
[02:31:01]   gdb scripts: kernel/bazel-bin/aosp/kernel_aarch64/gdb_scripts/vmlinux-gdb.py
[02:31:01]   kernel sources: kernel/aosp
[02:31:01]   vmlinux release: 6.1.124-android14-11
[02:31:01] checking the phone
[02:31:01]   device: oriole
[02:31:01]   running kernel: 6.1.124-android14-11
[02:31:01]   kgdboc=ttyGS3, kgdb_break_after=90
[02:31:01] rebooting the phone
[02:31:06] phone went down, waiting for Android to boot
[02:31:23] booted after 17 s
[02:31:23] phone uptime 5 s, kernel stops at 90 s
[02:32:58] kernel stopped (noticed at 101 s of uptime)
[02:32:58] serial port /dev/ttyACM0 is ready
[02:32:58] starting gdb-multiarch; `continue` or `detach` resumes the phone
Reading symbols from /home/you/Desktop/pixel6-kgdb-usb/kernel/out/raviole/dist/vmlinux...
The target architecture is set to "aarch64".
Source directories searched: /home/you/Desktop/pixel6-kgdb-usb/kernel/aosp:$cdir:$cwd
Remote debugging using /dev/ttyACM0
arch_kgdb_breakpoint () at arch/arm64/include/asm/kgdb.h:21
21              asm ("brk %0" : : "I" (KGDB_COMPILED_DBG_BRK_IMM));
```

`bt` shows how the kernel got here: the work item from `kgdb_break_after` called `kgdb_breakpoint()`.

```
(gdb) bt 4
#0  arch_kgdb_breakpoint () at arch/arm64/include/asm/kgdb.h:21
#1  kgdb_breakpoint () at kernel/debug/debug_core.c:1221
#2  0xffffffc0088732f0 in kgdboc_break_fn (work=<optimized out>) at drivers/tty/serial/kgdboc.c:334
#3  0xffffffc0080da9e8 in process_one_work (worker=worker@entry=0xffffff8002c2c240, work=work@entry=0xffffffc00a1ab770 <kgdboc_break_work>)
    at kernel/workqueue.c:2299
(More stack frames follow...)
```

Set a breakpoint on a line of kernel source. Line 1339 of `fs/open.c` is in `do_sys_openat2()`, the common path of every `open()` system call, just after `getname()` has copied the file name from the calling process into the kernel. Then resume, and the kernel runs until some process opens a file:

```
(gdb) break fs/open.c:1339
Breakpoint 1 at 0xffffffc0083a14d0: file fs/open.c, line 1339.
(gdb) continue
Continuing.
[New Thread 6553]
[Thread 6401 exited]
[Thread 3284 exited]
[Switching to Thread 6553]

Thread 4924 hit Breakpoint 1, do_sys_openat2 (dfd=-100, filename=<optimized out>, how=how@entry=0xffffffc02276bdf0) at fs/open.c:1339
1339            fd = get_unused_fd_flags(how->flags);
```

`[Switching to Thread 6553]` names the thread that hit the breakpoint by its kernel ID. The `4924` in `Thread 4924 hit Breakpoint 1` is gdb's own thread number, not an ID. The `[Thread ... exited]` lines are threads that ended while the kernel ran.

Inspect the stopped kernel. `bt` gives the call stack, `print` reads kernel structures through their types, and `$lx_task_by_pid()`, from the kernel's gdb scripts, finds a thread's `task_struct` by its ID:

```
(gdb) bt 3
#0  do_sys_openat2 (dfd=-100, filename=<optimized out>, how=how@entry=0xffffffc02276bdf0) at fs/open.c:1339
#1  0xffffffc0083a17d8 in do_sys_open (dfd=46727168, filename=0xfe0 <error: Cannot access memory at address 0xfe0>, flags=578207240, mode=<optimized out>)
    at fs/open.c:1357
#2  __do_sys_openat (dfd=46727168, filename=0xfe0 <error: Cannot access memory at address 0xfe0>, flags=<optimized out>, mode=<optimized out>) at fs/open.c:1373
(More stack frames follow...)
(gdb) print *tmp
$1 = {name = 0xffffff8002c90020 "/system/etc/hosts", uptr = 0x7cd0aff0d2 <error: Cannot access memory at address 0x7cd0aff0d2>, refcnt = 1, aname = 0x0,
  iname = 0xffffff8002c90020 "/system/etc/hosts"}
(gdb) print tmp->name
$2 = 0xffffff8002c90020 "/system/etc/hosts"
(gdb) print $lx_task_by_pid(6553)->comm
$3 = "Dns_52_10127\000\000\000"
(gdb) print $lx_task_by_pid(6553)->pid
$4 = 6553
(gdb) print $lx_task_by_pid(6553)->real_parent->comm
$5 = "init", '\000' <repeats 11 times>
(gdb) print/x how->flags
$6 = 0xa0000
```

`tmp` is the kernel's `struct filename` for the path being opened, `/system/etc/hosts`. `uptr`, the caller's pointer to the same path, cannot be read (see [Things to know](#things-to-know)). Thread 6553 is named `Dns_52_10127`, and its parent process is `init`. `0xa0000` in `how->flags` is `O_LARGEFILE | O_CLOEXEC`, opened read-only. Ignore the `dfd` and `filename` values in frames #1 and #2: in optimised code gdb cannot always recover a caller's arguments, and shows stale values instead.

Read raw memory: the first 32 bytes of the path, then the same address as a string:

```
(gdb) x/32xb tmp->name
0xffffff8002c90020:     0x2f    0x73    0x79    0x73    0x74    0x65    0x6d    0x2f
0xffffff8002c90028:     0x65    0x74    0x63    0x2f    0x68    0x6f    0x73    0x74
0xffffff8002c90030:     0x73    0x00    0x00    0x00    0x00    0x00    0x00    0x00
0xffffff8002c90038:     0x00    0x00    0x00    0x00    0x00    0x00    0x00    0x00
(gdb) x/s tmp->name
0xffffff8002c90020:     "/system/etc/hosts"
```

Resume again. The breakpoint is still set, so the next `open()` stops the kernel, this time in another thread, `android.bg`:

```
(gdb) continue
Continuing.
[Switching to Thread 2664]

Thread 2042 hit Breakpoint 1, do_sys_openat2 (dfd=-100, filename=<optimized out>, how=how@entry=0xffffffc01b12bdf0) at fs/open.c:1339
1339            fd = get_unused_fd_flags(how->flags);
(gdb) print tmp->name
$7 = 0xffffff8005a58020 "/data/data/com.google.android.gms/databases"
(gdb) print $lx_task_by_pid(2664)->comm
$8 = "android.bg\000\000\000\000\000"
```

Remove the breakpoint and detach. Detaching resumes the kernel and leaves the phone running normally:

```
(gdb) delete
(gdb) info breakpoints
No breakpoints, watchpoints, tracepoints, or catchpoints.
(gdb) detach
Detaching from program: /home/you/Desktop/pixel6-kgdb-usb/kernel/out/raviole/dist/vmlinux, Remote target
Ending remote debugging.
[Inferior 1 (Remote target) detached]
```

Quit gdb and check the phone over adb:

```bash
adb shell "echo phone alive; uptime; getprop ro.boot.bootreason; uname -r"
```

```
phone alive
 02:33:29 up 2 min,  0 users,  load average: 8.88, 2.98, 1.08
reboot
6.1.124-android14-11
```

`up 2 min` means the kernel was never reset: it booted, stopped in kgdb for the session, and carried on. A watchdog reset would also show in the boot reason, as `watchdog`, instead of `reboot`.

Addresses, thread IDs and file names differ on every run.

### Things to know

- **The phone is frozen while the kernel is stopped.** `continue` or `detach` resumes it.
- **Pointers into a process's memory cannot be read**, like `uptr` above. kgdb reads memory with ordinary kernel loads (`copy_from_kernel_nofault()`), and arm64's Privileged Access Never (`CONFIG_ARM64_PAN`) blocks those from reaching user addresses. Read the kernel's own copy instead, as `tmp` above.
- **`$lx_current()` does not work** over kgdb on arm64: it needs the `sp_el0` register, which kgdb does not send. Use `$lx_task_by_pid()` with the ID from `[Switching to Thread N]`.
- **The kernel stops once per boot.** To stop again, run `scripts/kgdb-attach.py`, which reboots.

## Known issues

- **A reboot sometimes ends in the bootloader instead of Android.** It has happened after `fastboot reboot`, after `adb reboot`, and as `fastboot reboot fastboot` failing with `Failed to boot into userspace fastboot`. Running the same command again, sometimes more than once, has always got the phone going. The slot stays healthy (`fastboot getvar all` shows `slot-successful:a:yes`, `slot-unbootable:a:no`), and the failed attempt leaves no boot reason in Android. The cause is not known yet. `scripts/kgdb-attach.py` detects it and runs `fastboot reboot` for you.
- **A crash reboots the phone instead of stopping in kgdb.** GKI is built with `PANIC_TIMEOUT=-1`, and kgdb ignores oopses and panics when the panic timeout is non-zero, so gdb never sees them. See the [Kleaf patch's limits](docs/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.md#limits).

## Recovery

If the phone is frozen and gdb cannot reach it, hold **Volume Down + Power for about 10 seconds**. The phone restarts into the bootloader; from there, flash again, or choose **Start** to boot normally.

To go back to stock, flash the factory image. This wipes the phone:

```bash
cd factory/oriole-cp1a.260405.005
./flash-all.sh
cd ../..
```

Going back to stock does not mean locking the bootloader: never run `fastboot flashing lock` (see [Flash](#flash)).

## License

This repository is licensed under the GNU General Public License, version 2 only ([`LICENSE`](LICENSE)). The patches change Linux kernel code, which is under the same license.
