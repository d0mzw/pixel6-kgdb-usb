# kleaf: kgdb: enable kgdb over the USB serial console

| Patch | Details |
| --- | --- |
| File | `patches/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.patch` |
| Project | `kernel/build/kernel` (Kleaf, the Bazel rules that build the kernel) |
| Changes | `kleaf/impl/kgdb.bzl` |

## The problem

Kleaf has a build flag, `--kgdb`, that turns a normal kernel build into a debug build. When it is set, `kgdb.bzl` does three things:

1. Adds the `scripts_gdb` make goal, which builds the gdb helper scripts.
2. Copies `vmlinux-gdb.py` and `scripts/gdb/linux/*.py` into the build output, so `gdb` can load the `lx-*` commands.
3. Changes the kernel configuration.

The configuration changes come from `_get_scripts_config_args()`, a function in `kgdb.bzl`. Kleaf is written in Starlark, Bazel's Python-like language, so it reads like a Python function. When `--kgdb` is set, it returns a list of edits such as `_config.enable("KGDB")`, each of which becomes an argument like `--enable KGDB`. Kleaf's configuration step, `_reconfig()` in `kleaf/impl/kernel_config.bzl`, then:

1. applies the list on top of the GKI defconfig with the kernel's own tool, `scripts/config --file .config --enable KGDB --disable RANDOMIZE_BASE ...`
2. merges the device's defconfig fragments, here `gs101_defconfig` and `raviole_defconfig`
3. runs `make olddefconfig`, which settles every option the edits left inconsistent

A *defconfig* is a file of `CONFIG_...` settings that a build starts from. GKI, the Generic Kernel Image, is the common kernel Google builds for all Android devices; the GKI defconfig is its configuration, and phone-specific code lives in separate vendor modules.

The stock list is:

| Config | Stock `--kgdb` | Why |
| --- | --- | --- |
| `GDB_SCRIPTS` | on | Builds the gdb helper scripts |
| `KGDB` | on | The kernel side of the debugger |
| `KGDB_KDB` | on | kdb, a text shell built into the kernel debugger |
| `RANDOMIZE_BASE` | off | No KASLR (randomised kernel addresses), so the kernel runs at the addresses in `vmlinux` |
| `STRICT_KERNEL_RWX` | off | Meant to keep kernel code writable for breakpoints; has no effect on arm64 (see below) |
| `VT` | on | Provides `HW_CONSOLE`, which kgdboc depends on |
| `VT_CONSOLE` | off | No console on the virtual terminal |
| `WATCHDOG` | off | Meant to stop watchdog resets while the kernel is halted |

That is enough for kgdb on a typical board with a serial port. It is not enough for this phone: kgdb has no way to reach the host, and turning `WATCHDOG` off removes the Pixel watchdog driver.

## What the patch changes

The patch adds three entries to the list and flips one. The "GKI default" column is the value in the GKI defconfig.

| Config | GKI default | With the patch | Why |
| --- | --- | --- | --- |
| `KGDB_SERIAL_CONSOLE` | not set | on | kgdboc, the driver that connects kgdb to a tty |
| `CONSOLE_POLL` | not set | on | The polled tty hooks kgdboc uses while interrupts are off |
| `MAGIC_SYSRQ` | on | on | SysRq, the kernel's emergency key channel; SysRq-G enters the debugger |
| `WATCHDOG` | on (stock `--kgdb` turns it off) | on | The gs101 watchdog drivers need it |

### kgdboc and the polled console

kgdb itself only speaks the gdb remote protocol. It needs an I/O driver to move those bytes to the host, and `KGDB_SERIAL_CONSOLE` builds kgdboc ("kgdb over console"), the one that uses a *tty*, the kernel's abstraction for a serial port. kgdboc does not use the tty's normal read and write paths, because the debugger runs with interrupts off and the other CPUs stopped. It uses three polling hooks instead, `poll_init`, `poll_get_char` and `poll_put_char`, which only exist when `CONSOLE_POLL` is set.

`KGDB_SERIAL_CONSOLE` already selects `CONSOLE_POLL` and `MAGIC_SYSRQ` in `lib/Kconfig.kgdb`, and depends on `HW_CONSOLE`, which only exists when `VT` is on. That is why stock `--kgdb` enables `VT`. The patch lists them anyway so the intent is visible in the build file and in the final `.config`.

### The USB serial port

This setup uses no UART, so the tty kgdboc uses is a USB serial gadget port, `ttyGS<n>`, on the same USB cable as adb. GKI already builds the serial gadget (`USB_CONFIGFS_ACM=y`), so no option is needed for the port itself. The gadget driver does not implement the polling hooks kgdboc needs; [aosp 0001](../../aosp/0001-usb-gadget-add-a-polled-mode-implemented-for-dwc3.md) and [aosp 0002](../../aosp/0002-usb-gadget-u_serial-add-polled-I-O-for-kgdboc.md) add them to the dwc3 USB controller driver and to `u_serial`.

### WATCHDOG

Stock `--kgdb` turns `WATCHDOG` off. On gs101 that removes the Pixel watchdog driver: the vendor watchdog Kconfig in `private/google-modules/soc/gs/drivers/watchdog/Kconfig` is wrapped in `if WATCHDOG`, so `S3C2410_WATCHDOG_GS`, which `gs101_defconfig` builds as a module, can no longer be selected. Without the driver, nothing stops the watchdog the bootloader starts, which is the same situation as a disabled watchdog node: the phone resets about a minute into every boot (see the [gs101 device tree patch](../../private/devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md#what-the-patch-does-not-do)).

The patch keeps `WATCHDOG` on. The watchdogs are kept from resetting a stopped kernel instead, by the [gs101 device tree patch](../../private/devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md) and the [soc/gs driver patch](../../private/google-modules/soc/gs/0001-soc-gs-keep-the-watchdogs-from-resetting-a-kgdb-stop.md).

### STRICT_KERNEL_RWX stays on

Stock `--kgdb` turns `STRICT_KERNEL_RWX` off, but `make olddefconfig` turns it back on. The option can only be changed on architectures that select `ARCH_OPTIONAL_KERNEL_RWX`, and arm64 does not, so it is always on. Breakpoints still work: arm64 kgdb writes the `brk` instruction (the arm64 breakpoint instruction) with `aarch64_insn_write()`, which patches kernel code through a temporary writable mapping instead of needing the code itself to be writable.

## Limits

- **Crashes are not caught by kgdb.** The GKI defconfig sets `PANIC_TIMEOUT=-1`, and kgdb ignores oopses and panics when the panic timeout is non-zero, so a crash reboots the phone without stopping in the debugger. Turning off `PANIC_ON_OOPS` would not help: AOSP's `init.rc` writes `1` to `/proc/sys/kernel/panic_on_oops` early in every boot.
- **The device fragments are merged after these edits.** `gs101_defconfig` and `raviole_defconfig` set none of these options today, but a fragment that did would override the patch. The check below reads the final values from the built kernel.

## Verify

Apply and build it with the README's [Apply the patches](../../../README.md#apply-the-patches) and [Build](../../../README.md#build) sections. The Build check reads the options above back out of the built kernel, which carries its own configuration (`IKCONFIG=y`).

Without the patch, `CONSOLE_POLL` and `KGDB_SERIAL_CONSOLE` are missing, and `WATCHDOG` is off. `STRICT_KERNEL_RWX=y` appears either way, for the reason above.
