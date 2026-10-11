# usb: gadget: u_serial: add polled I/O for kgdboc

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0002-usb-gadget-u_serial-add-polled-I-O-for-kgdboc.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `drivers/usb/gadget/function/u_serial.c` |

## The problem

kgdb talks to gdb through an I/O driver. kgdboc ("kgdb over console") is the one that uses a tty, the kernel's abstraction for a serial port. It does not use the tty's normal read and write paths, which depend on interrupts, wait queues and locks. Instead it calls three operations that a tty driver can provide in its `struct tty_operations`, available when `CONFIG_CONSOLE_POLL` is set:

| Operation | What kgdboc expects |
| --- | --- |
| `poll_init(driver, line, options)` | Get the port ready for polled use. Called when kgdboc binds, in normal process context. |
| `poll_get_char(driver, line)` | Return one received byte, or `NO_POLL_CHAR` if there is none. Must work with interrupts off. |
| `poll_put_char(driver, line, ch)` | Send one byte. Must work with interrupts off. |

On the phone, the tty is a USB serial port, `ttyGS<n>`, provided by `u_serial`. That is the shared code behind the CDC-ACM function, which the host sees as a `/dev/ttyACM<n>` device. `u_serial` implements none of the three operations, so kgdboc cannot use it.

## What the patch changes

It implements the three operations in `u_serial`, on top of `usb_gadget_poll()` from the previous patch.

### Requests set aside for polling

Each port normally has pools of requests for reading and writing, which belong to the interrupt-driven path and may be in the middle of a transfer when the kernel stops. The polled code keeps out of them. Instead, `gs_poll_init()` allocates two requests of its own: one on the IN endpoint (phone to host) and one on the OUT endpoint (host to phone), each the size of one USB packet on that endpoint at the time. kgdboc calls `gs_poll_init()` when it binds to the port.

### One transfer

`gs_poll_xfer()` does a single transfer:

1. Point the request's `complete()` callback at `gs_poll_complete()`, which only sets a flag, and queue the request with `usb_ep_queue()`.
2. Call `usb_gadget_poll()` in a loop, with a 1 µs delay between calls, until the flag is set or 250,000 polls have passed. Each poll also does the controller's work, so that is at least 250 ms.
3. Return the number of bytes transferred, or an error.

`usb_ep_queue()` takes `dwc->lock` itself and releases it before returning, so the lock stays balanced, for the reason given in the [previous patch's doc](0001-usb-gadget-add-a-polled-mode-implemented-for-dwc3.md#the-dwc3-implementation).

### Timeouts

A transfer only finishes when the host takes part: the host must read for an IN transfer to complete, and must send for an OUT transfer. If gdb is not attached, nothing reads, and the transfer times out.

A timed-out request is not cancelled; it stays queued in the controller. So it is marked busy, and the next call waits on it again instead of queuing it a second time. Its completion can arrive during any later poll, which is why each request has its own completion flag, stored in the port rather than on the caller's stack.

One byte can go wrong this way. `gs_poll_put_char()` writes its byte into the request's buffer before it checks whether the request is still busy, so after a timeout the new byte either replaces the old one in the transfer still in progress, or is never sent. The gdb remote protocol protects every packet with a checksum and resends it when the checksum fails, so a session recovers from this on its own.

### Reading and writing bytes

`gs_poll_put_char()` sends one byte as a one-byte transfer. `gs_poll_get_char()` receives a whole packet, up to the endpoint's packet size, and hands it out a byte at a time on later calls before it polls again. With nothing received it returns `NO_POLL_CHAR`, which kgdb treats as "no data yet".

### Lockup detectors

Every 1000 polls the loop touches the soft-lockup detector, the clocksource watchdog and the RCU stall detector, the kernel's own checks for a CPU or a clock that has stopped making progress. These run from timer interrupts, which are off while kgdb runs, and kgdb touches all three itself when it resumes. So this is only a precaution. The hardware watchdogs are a separate matter, handled by the [gs101 device tree](../private/devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md) and [soc/gs](../private/google-modules/soc/gs/0001-soc-gs-keep-the-watchdogs-from-resetting-a-kgdb-stop.md) patches.

## Limits

- **The port must already be connected:** the host must have enumerated the phone and configured the ACM function. Nothing here can bring USB up from inside the debugger.
- **Unplugging the cable while the kernel is stopped cannot be recovered.** The disconnect clears the port's USB link, after which `gs_poll_get_char()` no longer polls the controller, so the reconnection is never processed and the kernel waits for gdb forever. Recover with Volume Down + Power.
- **The polled requests are made once,** for the endpoints and packet size the port had when kgdboc bound. If the gadget is later rebound with different endpoints, dwc3 rejects them with a warning and the port stops working for kgdb. If the link comes back at a higher speed with larger packets, a received packet can overrun the receive buffer. Neither happened in the sessions run for this project, where the phone stayed on one connection.
- **The polled requests are never freed.** That would leak them if the port were removed, but the port `kgdb_acm` creates is kept for the life of the kernel.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. After the build, `gs_poll_init`, `gs_poll_get_char` and `gs_poll_put_char` are in `System.map`.
