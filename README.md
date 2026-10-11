# pixel6-kgdb-usb

Patches and scripts to debug the Pixel 6 kernel with `gdb` over the phone's USB cable, using kgdb on a CDC-ACM serial port. No UART, no debug board, no root.

| Setup | Details |
| --- | --- |
| Phone | Pixel 6 (`oriole`) |
| Android build | `CP1A.260405.005` (Android 16) |
| Kernel branch | `android-gs-raviole-6.1-android16` |
| Stock kernel in that build | `6.1.145-android14-11-gfa1d6308d1fe-ab14691759` |
| Kernel built from the branch | `6.1.124-android14-11` |

## Layout

```
pixel6-kgdb-usb/
├── manifest/pinned.xml   exact commit of every kernel project
├── patches/              the changes, one patch per concern
├── docs/                 one document per patch, explaining it
├── scripts/              build, repack, flash and attach helpers (not yet added)
├── kernel/               kernel source, synced here (ignored by git)
└── factory/              stock factory image, downloaded here (ignored by git)
```

`kernel/` and `factory/` are created by the steps below and never committed.

## Preparation

### Host tools

The host used here is Ubuntu 26.04 LTS. Install `git`, `curl`, `unzip` and `gdb-multiarch`, then Google's `repo` tool:

```bash
sudo apt install git curl unzip gdb-multiarch
mkdir -p ~/bin
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/bin/repo
chmod a+x ~/bin/repo
```

`git am` records each patch as a commit, so git needs a name and email. Skip this if `git config user.email` already prints one:

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

`adb` and `fastboot` come from Google's [platform-tools](https://developer.android.com/tools/releases/platform-tools); release 37.0.1 was used here. Download it into your home folder and put it first on your `PATH`, ahead of any distribution `adb`:

```bash
curl -O https://dl.google.com/android/repository/platform-tools_r37.0.1-linux.zip
unzip -d ~ platform-tools_r37.0.1-linux.zip
echo 'export PATH="$HOME/platform-tools:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

Check that both tools come from that folder:

```bash
which adb fastboot
```

Expected output, with your home folder:

```
/home/you/platform-tools/adb
/home/you/platform-tools/fastboot
```

### Clone this repository

```bash
git clone https://github.com/d0mzw/pixel6-kgdb-usb.git
cd pixel6-kgdb-usb
```

All later commands run from this folder unless they say otherwise.

### Sync the kernel source

The kernel tree is about 80 git projects managed by `repo`. Initialise it on the Pixel 6 branch, then sync with the pinned manifest so every project lands on the exact commit the patches were made against:

```bash
mkdir -p kernel
cd kernel
~/bin/repo init -u https://android.googlesource.com/kernel/manifest -b android-gs-raviole-6.1-android16 --depth=1
~/bin/repo sync -c --no-tags -j8 -m "$PWD/../manifest/pinned.xml"
cd ..
```

`--depth=1` fetches only the pinned commit of each project, not its history. Without it the clang prebuilts project alone is well over 36 GB; with it the whole tree is about 10 GB. When it finishes, `kernel/` contains `aosp/`, `build/`, `private/`, `prebuilts/` and `tools/`.

Syncing without `-m` gives you the tip of the branch instead, which may have moved since the patches were made.

### Download the factory image

The patched kernel is flashed on top of this exact stock build. The factory image is also the way back to stock if anything goes wrong.

```bash
mkdir -p factory
cd factory
curl -O https://dl.google.com/dl/android/aosp/oriole-cp1a.260405.005-factory-8081a8ba.zip
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

### Unlock the bootloader

Flashing a custom kernel needs an unlocked bootloader:

```bash
adb reboot bootloader
fastboot flashing unlock
```

Confirm on the phone with the volume keys and the power button. Unlocking wipes the phone.

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

`uname -r` is the stock GKI kernel Google ships in this build. The kernel built from the branch is older (`6.1.124`), which is fine: both are `android14-11`, so they share the KMI and the stock vendor modules load on either.

The running kernel's config can be read without root. Run this from the repository root:

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

`--config=raviole` selects the Pixel 6 and 6 Pro. `--config=pixel_debug_common` builds the GKI kernel from source instead of using Google's prebuilt one; without it, changes to the kernel never reach the image. The first build is slow because Bazel starts with an empty cache.

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

> [!NOTE]
> This repository is a work in progress. Only the first patch is published so far. The kernel cannot be debugged over USB until all patches are applied and the result is flashed; those patches and the flashing steps will follow.

Each patch belongs to one project inside `kernel/` and is stored under the same path in `patches/`. For example, `patches/build/kernel/` holds the patches for `kernel/build/kernel`. Every patch has a matching document under `docs/` with the same path and name, explaining what it changes and why.

Apply them in order, one at a time. Each step puts the project on a branch, applies the patch with `git am`, and checks it. `git am` records the patch as a commit, so `git log` shows exactly what was applied, and `git am --abort` backs out a patch that does not apply.

### 1. Kleaf build system: kgdb config set

- Project: `kernel/build/kernel`, Kleaf, the Bazel rules that build the kernel
- Patch: `patches/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.patch`
- Explained in: [`docs/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.md`](docs/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.md)

Put `build/kernel` on a branch. After a sync every project is on a detached HEAD, which is a bad place to commit:

```bash
cd kernel
~/bin/repo start kgdb build/kernel
```

`repo start` prints nothing when it succeeds. Apply the patch. The path is absolute because `git -C` changes directory before reading it:

```bash
git -C build/kernel am "$PWD/../patches/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.patch"
```

Expected output:

```
Applying: kleaf: kgdb: enable kgdb over the USB serial console
```

Check that it is the top commit:

```bash
git -C build/kernel log -1 --format=%s
```

Expected output:

```
kleaf: kgdb: enable kgdb over the USB serial console
```

Build with `--kgdb`, the flag this patch extends:

```bash
tools/bazel run \
    --config=stamp \
    --config=raviole \
    --config=pixel_debug_common \
    --kgdb \
    //private/devices/google/raviole:gs101_raviole_dist
cd ..
```

Read the configs the patch touches from the built kernel:

```bash
sh kernel/aosp/scripts/extract-ikconfig kernel/out/raviole/dist/vmlinux | grep -E '^(# )?CONFIG_(KGDB|KGDB_KDB|KGDB_SERIAL_CONSOLE|CONSOLE_POLL|MAGIC_SYSRQ|U_SERIAL_CONSOLE|PANIC_ON_OOPS|WATCHDOG|RANDOMIZE_BASE|STRICT_KERNEL_RWX)[ =]'
```

Expected output:

```
# CONFIG_RANDOMIZE_BASE is not set
CONFIG_STRICT_KERNEL_RWX=y
CONFIG_CONSOLE_POLL=y
CONFIG_WATCHDOG=y
CONFIG_U_SERIAL_CONSOLE=y
CONFIG_MAGIC_SYSRQ=y
CONFIG_KGDB=y
CONFIG_KGDB_SERIAL_CONSOLE=y
CONFIG_KGDB_KDB=y
# CONFIG_PANIC_ON_OOPS is not set
```

`STRICT_KERNEL_RWX=y` is expected: arm64 ignores the request to turn it off, and breakpoints work regardless. The doc explains why.
