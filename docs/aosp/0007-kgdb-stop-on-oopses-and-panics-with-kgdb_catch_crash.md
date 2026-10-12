# kgdb: stop on oopses and panics with kgdb_catch_crashes=

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0007-kgdb-stop-on-oopses-and-panics-with-kgdb_catch_crash.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `kernel/debug/debug_core.c` |

## The problem

A kernel crash is the moment a debugger is most useful, and without this patch kgdb lets it pass: the phone reboots, and gdb never hears about it.

kgdb has two ways to see a crash:

1. **An oops.** On arm64, a fatal fault in the kernel goes through `die()`, which calls the die notifier chain with `DIE_OOPS`. kgdb registers a notifier (`kgdb_notify()` in `arch/arm64/kernel/kgdb.c`) that calls `kgdb_handle_exception()`.
2. **A panic.** `panic()` calls `kgdb_panic()` early, before the panic notifiers run and before it reboots.

Both give up when `panic_timeout` is non-zero:

```c
/* kgdb_handle_exception() */
if (signo != SIGTRAP && panic_timeout)
	return 1;

/* kgdb_panic() */
if (panic_timeout)
	return;
```

The reasoning upstream is that a system configured to reboot on panic should not hang waiting for a debugger that may not be there. The GKI defconfig sets `PANIC_TIMEOUT=-1`, which means reboot at once, so on this phone both checks always return. Android also writes `1` to `/proc/sys/kernel/panic_on_oops` in `init.rc`, so every oops becomes a panic, and the phone reboots.

Setting `panic=0` on the command line would get past both checks, but it would also stop the phone from rebooting after any crash, including the ones kgdb cannot catch: anything before the USB serial port is up, about 5 seconds into boot. Those would leave the phone frozen until Volume Down + Power is held.

## What the patch changes

The patch adds a kernel parameter, `kgdb_catch_crashes`. When it is set:

- **An oops stops in kgdb** at the faulting instruction, as soon as `die()` reaches kgdb's notifier, whatever `panic_timeout` says. gdb sees `SIGSEGV` with the registers and backtrace of the fault.
- **A panic stops in kgdb**, in `kgdb_panic()`, called from `panic()`.
- **It enters the gdb stub, not kdb.** The kernel is built with `KGDB_KDB`, and kgdb starts in kdb, a text shell gdb cannot talk to. The patch sets `dbg_kdb_mode` to 0 before stopping, as [aosp 0004](0004-kgdboc-retry-binding-the-tty-and-add-kgdb_break_afte.md) and [aosp 0006](0006-usb-gadget-u_serial-break-into-kgdb-when-gdb-sends-t.md) do.
- **An oops stops once.** After an oops, `die()` goes on to `panic()`, which would stop a second time in `kgdb_panic()`. The patch remembers that it already stopped for an oops and lets that panic through.
- **The phone still reboots afterwards.** `panic_timeout` itself is not changed. When gdb continues or detaches, the panic goes on, and with `panic_timeout` at -1 the phone reboots straight away, as it does without the patch.

It only stops once kgdb has an I/O driver registered, that is, once kgdboc has bound to the USB serial port. Earlier crashes reboot as before, so a crash early in boot cannot leave the phone frozen.

`kgdb_catch_crashes` is a core parameter (`core_param()`), so it is set as `kgdb_catch_crashes=1` on the kernel command line, without a prefix. `scripts/package.sh` sets it. It can also be changed at runtime from gdb, with `set var kgdb_catch_crashes = 0`.

Without the parameter the kernel behaves exactly as before.

## Limits

- **With nothing attached, a crash freezes the phone** until gdb attaches or Volume Down + Power forces a restart. The watchdogs do not reset it ([gs101 device tree patch](../private/devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md)). Leave the phone connected to the host while debugging, or remove `kgdb_catch_crashes=1` from `DEBUG_CMDLINE` in `scripts/package.sh` for a phone that should reboot on its own.
- **Crashes before the USB serial port is up are not caught,** about the first 5 seconds of boot. They reboot the phone as before; read them afterwards with `adb shell dumpsys dropbox --print SYSTEM_LAST_KMSG`.
- **A crash in the USB stack itself may not be catchable.** kgdb talks to the host through the dwc3 controller and `u_serial`. A crash that leaves them in a broken state, or that happens while holding a lock they need, can leave kgdb unable to talk to gdb.
- **Warnings are not caught.** `WARN()` only prints a message and goes on; it does not go through `die()` or `panic()`.
- **The panic message is not passed to gdb.** At a panic, read it from the backtrace: `kgdb_panic()`'s `msg` argument holds it.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections, then package and flash. The phone's command line has the parameter:

```bash
adb shell cat /proc/cmdline | tr ' ' '\n' | grep kgdb_catch_crashes
```

Expected output:

```
kgdb_catch_crashes=1
```

Then crash the kernel on purpose and catch it. The README's [Catching a crash](../../README.md#catching-a-crash) does that from gdb, by resuming the kernel at address 0. In testing, both paths stopped in gdb: the oops at the faulting address, with `SIGSEGV`, and a panic called from gdb (`panic("Linux")`) in `kgdb_panic()`. After `detach`, the phone rebooted each time, with the boot reason `kernel_panic,null` after the oops and `kernel_panic,linux` after the panic.

Without the patch, `kgdb_handle_exception()` and `kgdb_panic()` return early, as shown above, so the phone reboots without stopping.
