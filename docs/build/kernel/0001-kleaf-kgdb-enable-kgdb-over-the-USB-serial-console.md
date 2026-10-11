# kleaf: kgdb: enable kgdb over the USB serial console

| Patch | Details |
| --- | --- |
| File | `patches/build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.patch` |
| Project | `kernel/build/kernel` (Kleaf, the kernel build system) |
| Changes | `kleaf/impl/kgdb.bzl` |

## What `--kgdb` does without the patch

Kleaf has a build flag, `--kgdb`, that turns a normal kernel build into a debug build. When it is set, `kgdb.bzl` does three things:

1. Adds the `scripts_gdb` make goal, which builds the gdb helper scripts.
2. Copies `vmlinux-gdb.py` and `scripts/gdb/linux/*.py` into the build output, so `gdb` can load the `lx-*` commands.
3. Changes the kernel config.

The config changes come from `_get_scripts_config_args()`, a function in `kgdb.bzl`. Kleaf is written in Starlark, Bazel's Python-like language, so it reads like a Python function. When `--kgdb` is set, it returns a list of config edits such as `_config.enable("KGDB")`, each of which becomes an argument like `--enable KGDB`. Kleaf's config step (`_reconfig()` in `kleaf/impl/kernel_config.bzl`) collects the list and, after loading the defconfig, applies it with the kernel's own tool:

```
scripts/config --file .config --enable KGDB --disable RANDOMIZE_BASE ...
```

Because the edits run after the defconfig, they override it. This is the list the patch extends. Stock, it is:

| Config | Stock `--kgdb` | Why |
| --- | --- | --- |
| `GDB_SCRIPTS` | on | Builds the gdb helper scripts |
| `KGDB` | on | The kernel side of the debugger |
| `KGDB_KDB` | on | The built-in kdb shell |
| `RANDOMIZE_BASE` | off | No KASLR, so the kernel runs at the addresses in `vmlinux` |
| `STRICT_KERNEL_RWX` | off | Meant to keep kernel text writable for breakpoints; has no effect on arm64 (see below) |
| `VT` | on | Provides `HW_CONSOLE`, which kgdboc depends on |
| `VT_CONSOLE` | off | No console on the virtual terminal |
| `WATCHDOG` | off | Stops watchdog resets while the kernel is halted |

That is enough for kgdb on a typical board with a UART. It is not enough for this phone: kgdb has no way to reach the host, and turning `WATCHDOG` off drops the gs101 watchdog driver.

## What the patch changes

The patch adds five configs to the list and flips one. The "Default" column is the value in the GKI defconfig. GKI, the Generic Kernel Image, is the common kernel Google builds for all Android devices; phone-specific code lives in separate vendor modules.

| Config | Default | With the patch | Why |
| --- | --- | --- | --- |
| `KGDB_SERIAL_CONSOLE` | not set | on | kgdboc, the driver that connects kgdb to a tty |
| `CONSOLE_POLL` | not set | on | The polled tty hooks kgdboc uses while interrupts are off |
| `MAGIC_SYSRQ` | on | on | Sysrq-g, one way to break into the debugger |
| `U_SERIAL_CONSOLE` | not set | on | Lets the USB serial gadget ports (`ttyGS*`) act as consoles |
| `PANIC_ON_OOPS` | on | off | Keeps the kernel running after an oops |
| `WATCHDOG` | on (stock `--kgdb` turns it off) | on | The gs101 watchdog drivers need it |

### kgdboc and the polled console

kgdb itself only speaks the gdb remote protocol. It needs an I/O driver to move those bytes to the host, and `KGDB_SERIAL_CONSOLE` builds kgdboc, the one that uses a tty. kgdboc does not use the tty's normal read and write paths, because the debugger runs with interrupts off and the other CPUs stopped. It uses three polling hooks instead, `poll_init`, `poll_get_char` and `poll_put_char`, which only exist when `CONSOLE_POLL` is set.

`KGDB_SERIAL_CONSOLE` already selects `CONSOLE_POLL` and `MAGIC_SYSRQ` in `lib/Kconfig.kgdb`, and depends on `HW_CONSOLE`, which only exists when `VT` is on. That is why stock `--kgdb` enables `VT`. The patch lists them anyway so the intent is visible in the build file and in the final `.config`.

### The USB serial gadget console

This setup uses no UART, so the tty kgdboc uses is a USB serial gadget port, `ttyGS<n>`, on the same USB cable as adb. GKI already builds the serial gadget (`USB_CONFIGFS_ACM=y`), but not its console support. `U_SERIAL_CONSOLE` adds it.

This config only makes the ports console-capable. The gadget driver does not implement the polling hooks kgdboc needs. Adding them takes changes to `u_serial` and the dwc3 USB controller driver, which are later patches in this series (not yet published).

### PANIC_ON_OOPS

The GKI defconfig sets `PANIC_ON_OOPS=y` and `PANIC_TIMEOUT=-1`: any oops panics and the phone reboots immediately. There is no command-line option that turns this off, only one that turns it on (`oops=panic`), so the patch disables it in the config. A debug kernel should stay up so the oops can be read and debugged.

### WATCHDOG

Stock `--kgdb` turns `WATCHDOG` off. On gs101 that removes the Pixel watchdog driver: the vendor watchdog Kconfig in `private/google-modules/soc/gs/drivers/watchdog/Kconfig` is wrapped in `if WATCHDOG`, so `S3C2410_WATCHDOG_GS`, which the gs101 defconfig builds as a module, can no longer be selected.

The patch keeps `WATCHDOG` on. The watchdogs are stopped at runtime instead, by the kernel command line and by watchdog patches later in this series (not yet published).

### STRICT_KERNEL_RWX stays on

Stock `--kgdb` turns `STRICT_KERNEL_RWX` off, but on arm64 the request is ignored. The option is only user-selectable on architectures that select `ARCH_OPTIONAL_KERNEL_RWX`, and arm64 does not, so it is always on. Breakpoints still work: arm64 kgdb writes the `brk` instruction with `aarch64_insn_write()`, which patches text through a temporary writable mapping instead of needing the text itself to be writable.

## Verify

Apply, build and check this patch with [step 1 in the README](../../../README.md#apply-the-patches). The check reads the configs above back out of the built kernel, which carries its own config (`IKCONFIG=y`).

Without the patch, `CONSOLE_POLL`, `U_SERIAL_CONSOLE` and `KGDB_SERIAL_CONSOLE` are missing, `PANIC_ON_OOPS` is on, and `WATCHDOG` is off, which drops the gs101 watchdog driver. `STRICT_KERNEL_RWX=y` appears either way, for the reason above.
