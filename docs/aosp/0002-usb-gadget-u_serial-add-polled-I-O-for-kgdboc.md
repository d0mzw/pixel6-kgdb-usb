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

Each port normally has pools of requests for reading and writing, which belong to the interrupt-driven path and may be in the middle of a transfer when the kernel stops. The polled code keeps out of them and uses two requests of its own: one on the IN endpoint (phone to host) and one on the OUT endpoint (host to phone), each the size of one USB packet on that endpoint.

When kgdboc binds to the port, `gs_poll_init()` marks the port as used for polling and makes the two requests. After that, `gserial_connect()` makes them again every time the host connects. A USB request belongs to one endpoint, and the endpoints and the packet size can change between connections, when Android rebinds the gadget or the link comes up at a different speed. Making the requests per connection keeps them matched.

### One transfer

`gs_poll_xfer()` does a single transfer:

1. Point the request's `complete()` callback at `gs_poll_complete()`, which only sets a flag, and queue the request with `usb_ep_queue()`.
2. Call `usb_gadget_poll()` in a loop, with a 1 µs delay between calls, until the flag is set or 250,000 polls have passed. Each poll also does the controller's work, so that is at least 250 ms.
3. Return the number of bytes transferred, or an error.

`usb_ep_queue()` takes `dwc->lock` itself and releases it before returning, so the lock stays balanced, for the reason given in the [previous patch's doc](0001-usb-gadget-add-a-polled-mode-implemented-for-dwc3.md#the-dwc3-implementation).

Each poll handles every pending controller event, including a disconnect and a new connection. A new connection replaces the requests, so after every poll the loop checks that its request is still current, and stops with an error if it is not, without touching the old one.

### Timeouts

A transfer only finishes when the host takes part: the host must read for an IN transfer to complete, and must send for an OUT transfer. If gdb is not attached, nothing reads, and the transfer times out.

A timed-out request is not cancelled; it stays queued in the controller and still owns its buffer. So it is marked busy, and the next call waits on it again instead of queuing it a second time. Its completion can arrive during any later poll, which is why each request has its own completion flag, stored in the port rather than on the caller's stack. Before `gs_poll_put_char()` writes a new byte into the IN buffer, it waits for any earlier transfer to finish, and drops the new byte if it still has not. The gdb remote protocol protects every packet with a checksum and resends it when the checksum fails, so a dropped byte only costs a resend.

### Reading and writing bytes

`gs_poll_put_char()` sends one byte as a one-byte transfer. `gs_poll_get_char()` receives a whole packet, up to the endpoint's packet size, and hands it out a byte at a time on later calls before it polls again. With nothing received it returns `NO_POLL_CHAR`, which kgdb treats as "no data yet".

### While disconnected

When the host disconnects, the port loses its USB link. The polled operations then have nothing to transfer, but they keep calling `usb_gadget_poll()`, so that when the cable is plugged back in, the new connection can be processed even with the kernel stopped, and `gserial_connect()` sets the port up again. This has not yet been tested on the phone: unplugging and replugging the cable while the kernel is stopped is still to be tried.

## Limits

- **The port must have been connected once:** the host must have enumerated the phone and configured the ACM function before kgdboc binds. Nothing here can bring USB up for the first time from inside the debugger.
- **Reconnecting while stopped is untested** (see above). If it works, gdb has to attach again: On the host, the serial device disappears with the cable and comes back as a new device, so the old gdb session cannot continue on it.
- **The requests are made inside the debugger on a reconnect while the kernel is stopped,** with `GFP_ATOMIC`. That only goes wrong if the CPU running kgdb was itself stopped inside the memory allocator.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. After the build, `gs_poll_init`, `gs_poll_get_char` and `gs_poll_put_char` are in `System.map`.
