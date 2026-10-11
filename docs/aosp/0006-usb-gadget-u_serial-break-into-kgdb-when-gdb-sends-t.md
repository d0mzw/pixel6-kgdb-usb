# usb: gadget: u_serial: break into kgdb when gdb sends to the port

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0006-usb-gadget-u_serial-break-into-kgdb-when-gdb-sends-t.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `drivers/usb/gadget/function/u_serial.c` |

## The problem

gdb can only talk to kgdb once the kernel has stopped in it. A running kernel has to be stopped first, and the usual ways of doing that are all closed on this phone:

| Usual way | Why it does not work here |
| --- | --- |
| `echo g > /proc/sysrq-trigger` | Needs root: the file is root-only, and SELinux denies the adb shell even a look at it |
| SysRq-G on a keyboard | No keyboard |
| A serial *break* from the host, as with a UART | The ACM function accepts the host's break request, but only reports it back to the host as a line state; it never reaches the tty as a SysRq |
| kgdb's NMI "knock" (`CONFIG_SERIAL_KGDB_NMI`) | Needs NMIs, and this arm64 kernel is built without pseudo-NMI support (`ARM64_PSEUDO_NMI`) |

The polled I/O from [aosp 0002](0002-usb-gadget-u_serial-add-polled-I-O-for-kgdboc.md) only runs once the kernel is stopped. While it runs, nothing on the phone reads the kgdb port, so whatever gdb sends is lost: `target remote` times out, and Ctrl+C in gdb does nothing.

## What the patch changes

It makes the kgdb port itself the way in. gdb starts every packet with `$`, and sends a single `0x03` byte for Ctrl+C. When either arrives while the kernel runs, the kernel stops in kgdb.

### The OUT request stays queued

In aosp 0002, the polled OUT request (host to phone) is only queued while kgdb waits for a byte. This patch keeps it queued all the time: it is queued as soon as the port is set up for polling, and its completion callback, `gs_poll_rx_complete()`, queues it again every time. What arrives is copied into a 4 KB buffer in the port, and `gs_poll_get_char()` hands out bytes from that buffer, polling the controller once when it is empty.

The completion runs in two situations:

| When | Context | What it does with the data |
| --- | --- | --- |
| The kernel runs | The dwc3 interrupt handler | Looks for `$` or `0x03`. Drops what comes before it, keeps the rest, and schedules the stop. With no match, drops it all. |
| The kernel is stopped in kgdb | `usb_gadget_poll()`, called from `gs_poll_get_char()` or `gs_poll_put_char()` | Keeps it all, for kgdb to read |

`in_dbg_master()` tells the two apart: it is true only on the CPU that runs kgdb, while kgdb runs.

If the request completes with an error because the host disconnected, it is not queued again. `gserial_connect()` makes new requests and queues the OUT one on the next connection.

### Stopping from a tasklet

The completion cannot stop the kernel itself. It runs in the middle of dwc3's event handling, and kgdb, polling the controller, would run that event handling again from inside itself. Instead it schedules a tasklet, a deferred function that the kernel runs as soon as interrupt handling is done. The tasklet, `gs_poll_break_fn()`:

1. checks that kgdboc still has an I/O driver registered (`dbg_io_ops`). Without one, a breakpoint is not claimed and the kernel oopses.
2. sets `dbg_kdb_mode` to 0, so the stop enters the gdb stub rather than the kdb shell, as [aosp 0004](0004-kgdboc-retry-binding-the-tty-and-add-kgdb_break_afte.md) does.
3. calls `kgdb_breakpoint()`.

That is why gdb's first backtrace after attaching shows `gs_poll_break_fn` and the tasklet code under `kgdb_breakpoint`.

The bytes that started the stop are already in the buffer, so kgdb reads them first:

- **A new gdb session** starts with a `qSupported` packet, which the gdb stub answers as usual.
- **Ctrl+C after `continue`** is a lone `0x03`. kgdb knows gdb is connected, so it sends gdb a stop report on its own, and skips the `0x03` while it looks for the next packet.

### Nothing else on the host may write to the port

Any `$` or `0x03` sent to the kgdb port stops the kernel. ModemManager probes every ACM port it finds by sending it modem commands; the udev rule in the README's [Serial port access](../../README.md#serial-port-access) keeps it away from this one. Do not open the port on the host with anything other than gdb.

## Limits

- **The port has to be up.** The kernel can only be stopped this way once Android has set up USB, the host has enumerated the phone, and kgdboc has bound to the port. On this phone that is about 5 seconds into boot. Nothing before that can be debugged over USB.
- **The buffer holds 4 KB.** Bytes that do not fit are dropped. gdb sends one packet at a time and waits for the reply, so this does not happen in normal use, and a dropped byte only costs a resend, as the packet's checksum fails.
- **A stray `$` or `0x03` stops the kernel,** see above.
- **Not tested:** suspend. With the USB cable connected, the phone stayed out of full system suspend in testing, so a queued request across suspend and resume has not been exercised.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. After the build, `gs_poll_rx_complete` and `gs_poll_break_fn` are in `System.map`. On the phone:

- `scripts/kgdb-attach.py` on a running phone stops it within a couple of seconds, and gdb's backtrace shows `gs_poll_break_fn`.
- After `continue`, Ctrl+C in gdb stops the kernel again.
