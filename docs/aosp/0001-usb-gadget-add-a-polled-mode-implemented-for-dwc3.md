# usb: gadget: add a polled mode, implemented for dwc3

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0001-usb-gadget-add-a-polled-mode-implemented-for-dwc3.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `include/linux/usb/gadget.h`, `drivers/usb/dwc3/gadget.c` |

## The problem

When the phone is plugged into a computer, it is the USB *device* (in Linux terms, a *gadget*) and the computer is the *host*. The phone's USB controller is a Synopsys DesignWare USB3 block, driven by the `dwc3` driver.

Sending or receiving data on the phone works through requests. A function driver, such as the serial port driver, fills in a `struct usb_request` and calls `usb_ep_queue()`. The controller then moves the data on its own, and when it is finished it reports back through interrupts:

1. The controller writes an *event* into a ring buffer in memory and raises an interrupt.
2. The hard interrupt handler, `dwc3_interrupt()`, calls `dwc3_check_event_buf()`. That copies the new events aside, masks the interrupt, and asks for the threaded handler.
3. The threaded handler, `dwc3_thread_interrupt()`, takes `dwc->lock` and calls `dwc3_process_event_buf()`, which handles each event. For a finished transfer that ends in `dwc3_gadget_giveback()`, which calls the request's `complete()` callback.

kgdb cannot use this path. When the kernel stops in the debugger, interrupts are off on the CPU running kgdb and every other CPU is stopped. Step 1 still happens, because the hardware does it, but steps 2 and 3 never run, so a request is never reported complete.

## What the patch changes

It adds a way to run steps 2 and 3 by hand.

### A new gadget operation

Every USB device controller driver fills in a `struct usb_gadget_ops` table. The patch adds one entry to it:

```c
int (*udc_poll)(struct usb_gadget *gadget);
```

and a wrapper for callers, `usb_gadget_poll()`, which returns `-EOPNOTSUPP` if the controller does not implement it. The contract is that it processes pending controller events right now, without interrupts, and never sleeps.

### Keeping the KMI intact

On a GKI phone, the vendor modules Google ships are built against the stock kernel and are expected to load on any GKI kernel of the same line. That only works if the kernel's binary interface to modules, the KMI (Kernel Module Interface), does not change. Adding a field to `struct usb_gadget_ops` would change its size and break it.

GKI structures reserve space for exactly this. `struct usb_gadget_ops` ends with four `ANDROID_KABI_RESERVE(n)` entries, each an unused `u64`. The patch replaces the first with `ANDROID_KABI_USE(1, ...)`, which puts the new pointer in a union with the old `u64`. The size and layout of the structure stay the same, the macro checks at build time that the new member fits, and the symbol checksums modules are matched against are computed from the original declaration.

This project rebuilds every module from the same tree (see the README's [Package](../../README.md#package) section), so it would work without this care. Keeping the KMI intact means the patch does not force that.

### The dwc3 implementation

`dwc3_gadget_poll()` does what the two interrupt handlers would have done:

```c
dwc3_check_event_buf(evt);              /* step 2 */

spin_lock_irqsave(&dwc->lock, flags);   /* step 3 */
dwc3_process_event_buf(evt);
spin_unlock_irqrestore(&dwc->lock, flags);
```

Three details matter.

**It processes every pending event, not only the debugger's.** Completions for adb's requests, control requests from the host and connection changes are all handled inside the debugger, the same way the interrupt handler would have handled them.

**It refuses to poll a suspended controller.** When the controller is runtime-suspended, `dwc3_check_event_buf()` does not read events. It queues a resume, which runs from a workqueue, and disables the controller's interrupt until then. A workqueue cannot run while the kernel is stopped, so the poll returns `-EBUSY` instead.

**It takes `dwc->lock` normally,** with `spin_lock_irqsave()` rather than trying it and giving up. Trying looks safer, but it cannot work: `dwc3_gadget_giveback()` releases the lock before calling each `complete()` callback and takes it again afterwards. Run without the lock held, it releases a lock nobody holds and returns still holding it, which leaves `dwc->lock` taken for good and USB dead.

Taking the lock normally is safe in practice because of how kgdb stops the other CPUs. It sends each of them an ordinary inter-processor interrupt; this kernel has no NMIs, the interrupts that cannot be masked. Code holding `dwc->lock` holds it with interrupts off, so a CPU in that code cannot take the stop request until it has released the lock. A CPU that does not answer within about a second is left running, and kgdb carries on without it.

## Limits

- **Breakpoints inside dwc3 code.** If kgdb stops the kernel on the CPU that is itself holding `dwc->lock`, for example at a breakpoint in a dwc3 function that runs under the lock, the poll waits for that lock forever. Recover with Volume Down + Power.
- **A CPU that missed the stop request** keeps running, and may run dwc3 code at the same time as the poll. kgdb logs `Timed out waiting for secondary CPUs.` to the kernel log when that happens.
- **This transport shares fate with what it debugs.** If the bug is in dwc3, the USB PHY or their power domain, the debugger's link fails with it. A real UART does not have that coupling.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. After the build, `dwc3_gadget_poll` is in `System.map`.
