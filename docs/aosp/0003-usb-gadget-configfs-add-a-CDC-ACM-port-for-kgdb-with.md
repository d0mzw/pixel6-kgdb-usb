# usb: gadget: configfs: add a CDC-ACM port for kgdb with kgdb_acm

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0003-usb-gadget-configfs-add-a-CDC-ACM-port-for-kgdb-with.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `drivers/usb/gadget/configfs.c` |

## The problem

What the phone offers over USB is decided by userspace. Android builds its USB gadget through configfs, a filesystem under `/config/usb_gadget/` where each *function* (adb, MTP, a serial port, ...) is a directory, and a function is offered to the host by linking it into the active *configuration*. Android's init scripts and USB HAL do this, normally offering adb and nothing else.

The polled serial port from the previous patch only helps if a CDC-ACM serial function is in that active configuration. On a production build there is no way to put one there without root:

- configfs is writable only by root.
- `svc usb setFunctions` offers no ACM function, and the `sys.usb.config` property cannot be set from the shell.
- Older Android had `/sys/class/android_usb/android0/functions` for this. It no longer exists.

Android's init scripts do create ACM function instances of their own (for example `acm.gs6` and `acm.uwb0` under `/config/usb_gadget/g1/functions/`), which take serial lines `ttyGS0` to `ttyGS2`. They are not in the configuration offered to the host, so nothing is behind them.

## What the patch changes

With `kgdb_acm` on the kernel command line, the kernel adds an ACM function itself. The code is only built when `CONFIG_KGDB` is on. `configfs_composite_bind()` runs whenever userspace binds the gadget to the controller, and for each configuration it adds the functions userspace linked. The patch calls `kgdb_acm_attach()` right after that, which adds one more:

1. On first use, create an `acm` function instance with `usb_get_function_instance("acm")`. It takes the lowest free serial line, `ttyGS3` on this phone.
2. Create a function from it and add it to the configuration with `usb_add_function()`.
3. Log the line number, as `kgdb_acm: ttyGS<line> attached to config <n>`.

The host then sees a second interface next to adb, and creates a serial device for it, `/dev/ttyACM0` if the host has no other ACM device. kgdboc is pointed at the phone's side with `kgdboc=ttyGS3`.

### Keeping adb working

adb is the only way to control a phone without root, so losing USB would be expensive. The patch cannot take the gadget down:

- It runs after `usb_gadget_check_config()`, the step that can fail the bind.
- Every failure only logs a warning and returns. The gadget binds with or without the ACM port.

### Rebinding

Android rebinds the gadget whenever the USB mode changes, for example when switching between charging only and file transfer. On unbind, configfs's `purge_configs_funcs()` normally moves every function back onto the configuration's function list, and the next bind adds them all again in its own loop, before `usb_gadget_check_config()`. That would put the kgdb function in the step that can fail the bind.

So the patch has `purge_configs_funcs()` release the kgdb function instead, and every bind adds a new one through `kgdb_acm_attach()`, after the check. The function instance is created once and kept, so the port keeps the same line.

The sessions so far have all been on the first bind after boot. A USB mode change followed by a debugging session has not been tested yet.

Without `kgdb_acm` on the command line, nothing changes.

## Limits

- **Line 3 is specific to this phone:** it is the lowest line free after Android's own ACM instances. The log message reports it.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. After the build, `kgdb_acm_setup` is in `System.map`. On the phone, `/dev/ttyGS3` exists, and the host has a new `/dev/ttyACM*` device.
