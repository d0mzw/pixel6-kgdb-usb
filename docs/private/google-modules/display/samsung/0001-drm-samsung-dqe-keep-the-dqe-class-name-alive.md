# drm: samsung: dqe: keep the dqe class name alive

| Patch | Details |
| --- | --- |
| File | `patches/private/google-modules/display/samsung/0001-drm-samsung-dqe-keep-the-dqe-class-name-alive.patch` |
| Project | `kernel/private/google-modules/display/samsung` (the display driver for Google's Tensor chips) |
| Changes | `exynos_drm_dqe.c` |

## The problem

Without this patch, about one boot in six crashes about a second in, and the phone restarts by itself. The next boot is normal, and Android records the crash:

```bash
adb shell getprop sys.boot.reason
```

```
kernel_panic,oops:_fatal_exception
```

The bug is in the stock display driver, not in the kgdb patches: the debug kernel built without any of them crashes the same way. It has nothing to do with debugging, but a phone that randomly reboots while it is being set up for debugging gets in the way.

### The crash

The kernel log of the crashed boot, which Android saves and `adb shell dumpsys dropbox --print SYSTEM_LAST_KMSG` prints, shows the oops:

```
Unable to handle kernel paging request at virtual address ffffffc00af0b568
...
Call trace:
 string+0xc8/0x120
 vsnprintf+0x438/0x668
 add_uevent_var+0xb0/0x148
 kobject_uevent_env+0x188/0x2ac
 kobject_synth_uevent+0x42c/0x488
 uevent_store+0x28/0x68
```

The process is `ueventd`, Android's device manager. Early in boot it replays an `add` event for every device by writing to each device's `uevent` file in sysfs (its *coldboot*). For each event the kernel formats `SUBSYSTEM=<name>`, where the name is that of the device's bus or class. Here that name pointed at memory that was no longer mapped. The address changed from boot to boot but always ended in `...568`.

A temporary check in `kobject_uevent_env()`, which logged the device instead of formatting an unreadable name, caught it on the next crash:

```
uevent: unreadable subsystem ffffffc00aef3568 for /devices/platform/1c300000.drmdecon/dqe0/atc
```

### The cause

`dqe0` is a device class that the display driver creates for its display quality engine (DQE), with one device in it, `atc`. `exynos_dqe_register()` creates it like this:

```c
char dqe_name[MAX_DQE_NAME_SIZE] = "dqe";
...
scnprintf(dqe_name, MAX_DQE_NAME_SIZE, "dqe%u", decon->id);
dqe->dqe_class = class_create(THIS_MODULE, dqe_name);
```

`dqe_name` is a local array, on the stack of the thread running the function. `class_create()` does not copy the name; in 6.1 it stores the pointer (`cls->name = name` in `drivers/base/class.c`). So once the function returns, the class name points into a dead stack frame.

This kernel is built with `VMAP_STACK`: each thread's stack is mapped on its own, and the mapping is removed when the thread exits. While the thread that probed the display is still alive, the name reads as whatever its stack holds by then. Once it has exited, reading the name faults. ueventd's coldboot reaches `dqe0/atc` about a second into boot, and whether the probing thread is gone by then varies, which is why only some boots crash. The same stack offset every time is why the address always ended in `...568`.

## What the patch changes

The name is allocated with `kasprintf()` instead, so it lives as long as the class. The class is never destroyed, so the name is never freed either. If the class cannot be created, the name is freed again.

## Limits

- **Only `dqe0` is fixed.** No other device showed an unreadable subsystem name while the check above was in place, but the check only ran for these test boots.
- **Stock Android ships a newer display driver** than the one this kernel is built with, and may already have a fix. This patch is for the pinned source.

## Verify

Apply and build it with the README's [Apply the patches](../../../../../README.md#apply-the-patches) and [Build](../../../../../README.md#build) sections, then package and flash. Reboot the phone several times and check the boot reason after each boot. Leave each boot at least a minute before the next reboot; rebooting straight after Android has booted can leave the phone stuck in recovery (README, [Known issues](../../../../../README.md#known-issues)).

```bash
adb reboot
adb wait-for-device
adb shell getprop sys.boot.reason
```

Each boot should report `reboot,shell`, never `kernel_panic,oops:_fatal_exception`. In testing, before the patch, 7 of 45 boots crashed; with it, none of 37 did, including 16 with the check above in place, which never fired.
