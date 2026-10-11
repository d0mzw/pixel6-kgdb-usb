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
├── scripts/              build, repack, flash and attach helpers
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

`adb` and `fastboot` come from Google's [platform-tools](https://developer.android.com/tools/releases/platform-tools) (release 37.0.1 was used here). Put the extracted `platform-tools/` folder on your `PATH`.

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

### Unlock the bootloader

Flashing a custom kernel needs an unlocked bootloader. Enable **OEM unlocking** in Developer options, then:

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

The phone reboots into setup when the script finishes. Skip through setup, then turn on **Developer options** (Settings, About phone, tap **Build number** seven times) and enable **USB debugging**. Accept the authorisation prompt when the host first connects.

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
