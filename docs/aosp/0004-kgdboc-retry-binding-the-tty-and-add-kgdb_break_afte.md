# kgdboc: retry binding the tty, and add kgdb_break_after=

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0004-kgdboc-retry-binding-the-tty-and-add-kgdb_break_afte.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `drivers/tty/serial/kgdboc.c` |

## The problem

kgdboc is not ready until it has bound to its tty, the `kgdboc=ttyGS3` from the command line. It binds through the driver core's *deferred probe*: if the tty does not exist yet, the probe is put aside and retried later. Retries only happen when some other driver probes successfully, and the USB serial port only appears once Android has set up USB and the host has enumerated the phone. If that comes after the last other probe, kgdboc can stay unbound for good, and kgdb has no way to reach the host. On this phone that happened on some boots and not on others, from the same image.

A second, smaller problem: gdb can only talk to kgdb once the kernel has stopped in it. [aosp 0006](0006-usb-gadget-u_serial-break-into-kgdb-when-gdb-sends-t.md) stops the kernel when gdb connects, but it can only do that once the USB port is up. A stop at a chosen time after boot, with nothing sent from the host, is still useful as a fallback.

## What the patch changes

### Retrying the binding

When kgdboc initialises, early in boot, it schedules a delayed work item if `kgdboc=` is set. The work item tries the binding itself, by calling `configure_kgdboc()` under kgdboc's `config_mutex`. If the tty is not ready yet, it tries again a second later, for up to 100 seconds, and then gives up with `kgdboc: ttyGS3 never bound` in the kernel log. Once bound, it logs `kgdboc: bound to ttyGS3`.

On this phone the port appears about 4 seconds into boot, and kgdboc binds within the next second.

### kgdb_break_after=

The patch also adds a kernel command line parameter, `kgdb_break_after=<seconds>`. With it, the same work item, once the tty is bound, waits until that many seconds after kgdboc initialised, which is roughly that many seconds into boot, and then stops the kernel in kgdb by calling `kgdb_breakpoint()`. That executes a `brk` instruction that kgdb's exception handler claims. gdb can then attach and finds the kernel already waiting.

It never traps while unbound. Without a bound I/O driver nobody claims the `brk`, and the kernel oopses.

`scripts/package.sh` does not set it: [aosp 0006](0006-usb-gadget-u_serial-break-into-kgdb-when-gdb-sends-t.md) is the normal way in, and `scripts/kgdb-attach.py --boot` stops the kernel as soon as the port comes up. Add it by hand to stop at a fixed time, for example to catch something about a minute into boot.

### gdb, not kdb

The kernel is built with `KGDB_KDB` too, and in that case kgdb starts in kdb, a text shell that gdb cannot speak to. The work item sets `dbg_kdb_mode` to 0 before the trap, so it enters the gdb stub. `dbg_kdb_mode` is declared in `kernel/debug/debug_core.h`, which is private to that directory, so the patch declares it `extern` itself.

The parameter is read with `__setup()`, and `dbg_kdb_mode` is not exported to modules, so kgdboc must be built into the kernel (`KGDB_SERIAL_CONSOLE=y`). The [Kleaf patch](../build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.md) does that.

## Limits

- **With `kgdb_break_after=`, every boot stops,** whether or not a debugger is waiting. With no gdb attached the phone stays frozen until gdb connects or Volume Down + Power forces a restart.
- **It stops once per boot.** To stop again later, attach with gdb ([aosp 0006](0006-usb-gadget-u_serial-break-into-kgdb-when-gdb-sends-t.md)).
- **A port that appears more than 100 seconds into boot is not bound.** The retries stop there.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. After the build, `kgdboc_late_fn` is in `System.map`. On the phone, the kernel log read from gdb with `lx-dmesg` shows `KGDB: Registered I/O driver kgdboc` within a second or so of `kgdb_acm: ttyGS3 attached to config 1`.

To check `kgdb_break_after=`, add `kgdb_break_after=30` to `DEBUG_CMDLINE` in `scripts/package.sh`, package and flash. About 30 seconds into boot the phone freezes, and `scripts/kgdb-attach.py --now` finds it stopped in `kgdboc_late_fn`. In testing it stopped at 31.7 seconds of kernel uptime.
