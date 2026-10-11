# kgdboc: add kgdb_break_after= to trap into kgdb after boot

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0004-kgdboc-add-kgdb_break_after-to-trap-into-kgdb-after-.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `drivers/tty/serial/kgdboc.c` |

## The problem

gdb can only talk to kgdb once the kernel has stopped in it. A running kernel has to be stopped first, and the usual ways of doing that are all closed on this phone:

| Usual way | Why it does not work here |
| --- | --- |
| `echo g > /proc/sysrq-trigger` | Needs root |
| SysRq-G on a keyboard | No keyboard |
| A serial *break* from the host, as with a UART | The ACM function accepts the host's break request, but only reports it back to the host as a line state; it never reaches the tty as a SysRq |
| kgdb's NMI "knock" (`CONFIG_SERIAL_KGDB_NMI`) | Needs NMIs, and this arm64 kernel is built without pseudo-NMI support (`ARM64_PSEUDO_NMI`) |

## What the patch changes

It adds a kernel command line parameter, `kgdb_break_after=<seconds>`. When kgdboc initialises, early in boot, it schedules a delayed work item for that many seconds later, so the delay is counted from roughly the start of boot. The work item stops the kernel in kgdb by calling `kgdb_breakpoint()`, which executes a `brk` instruction that kgdb's exception handler claims. gdb can then attach and finds the kernel already waiting.

90 seconds is used on this phone: late enough that USB is up and the host has connected to the ACM port, early enough not to wait long.

The parameter is read with `__setup()`, and the patch uses `dbg_kdb_mode`, which is not exported to modules, so kgdboc must be built into the kernel (`KGDB_SERIAL_CONSOLE=y`). The [Kleaf patch](../build/kernel/0001-kleaf-kgdb-enable-kgdb-over-the-USB-serial-console.md) does that.

### Binding before trapping

kgdboc is not ready until it has bound to its tty, the `kgdboc=ttyGS3` from the command line. It binds through the driver core's *deferred probe*: if the tty does not exist yet, the probe is put aside and retried later. Retries only happen when some other driver probes successfully, and the USB serial port only appears once the host has enumerated the phone. If that comes after the last other probe, kgdboc can stay unbound for good. Earlier versions trapped on one boot and silently did nothing on the next, from the same image.

So the work item first tries the binding itself, by calling `configure_kgdboc()` under kgdboc's `config_mutex`. If the tty is still not ready, it logs a warning and tries again 10 seconds later. It makes 10 attempts in all, so the stop can come up to 90 seconds late, after which it gives up and logs that it is not trapping.

It never traps while unbound. Without a bound I/O driver nobody claims the `brk`, and the kernel oopses.

### gdb, not kdb

The kernel is built with `KGDB_KDB` too, and in that case kgdb starts in kdb, a text shell that gdb cannot speak to. The work item sets `dbg_kdb_mode` to 0 first, so the trap enters the gdb stub. `dbg_kdb_mode` is declared in `kernel/debug/debug_core.h`, which is private to that directory, so the patch declares it `extern` itself.

Without `kgdb_break_after=` on the command line, nothing is scheduled and nothing changes.

## Limits

- **Every boot stops,** whether or not a debugger is waiting. With no gdb attached the phone stays frozen until gdb connects or Volume Down + Power forces a restart. Leave `kgdb_break_after=` off the command line for a phone in normal use.
- **It stops once per boot.** To stop again, reboot. `scripts/kgdb-attach.py` does that for you.
- **There is no way to break in on demand** without root. Once gdb continues, the kernel runs until it hits a breakpoint gdb set.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. After the build, `kgdboc_break_fn` is in `System.map`. On the phone, the kernel stops about 90 seconds after boot and gdb's first backtrace shows `kgdboc_break_fn`.
