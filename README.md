# pixel6-kgdb-usb

Patches and scripts to debug the Pixel 6 kernel with `gdb` over the phone's USB cable, using kgdb on a CDC-ACM serial port. No UART, no debug board, no root.

| Setup | Details |
| --- | --- |
| Phone | Pixel 6 (`oriole`) |
| Android build | `CP1A.260405.005` (Android 16) |
| Kernel branch | `android-gs-raviole-6.1-android16` |
| Kernel release | `6.1.124-android14-11` |

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
~/bin/repo init -u https://android.googlesource.com/kernel/manifest -b android-gs-raviole-6.1-android16
~/bin/repo sync -c -j8 -m "$PWD/../manifest/pinned.xml"
cd ..
```

The sync downloads several GB. When it finishes, `kernel/` contains `aosp/`, `build/`, `private/`, `prebuilts/` and `tools/`.

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

The phone must run this same build before you flash the patched kernel. If it does not, flash the factory image first with `flash-all.sh` from that folder (this wipes the phone).

### Unlock the bootloader

Flashing a custom kernel needs an unlocked bootloader. Enable **OEM unlocking** in Developer options, then:

```bash
adb reboot bootloader
fastboot flashing unlock
```

Unlocking wipes the phone.
