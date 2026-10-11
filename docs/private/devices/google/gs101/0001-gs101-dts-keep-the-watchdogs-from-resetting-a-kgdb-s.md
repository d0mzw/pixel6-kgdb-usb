# gs101: dts: keep the watchdogs from resetting a kgdb stop

| Patch | Details |
| --- | --- |
| File | `patches/private/devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.patch` |
| Project | `kernel/private/devices/google/gs101` (device tree for the gs101 chip, the Tensor G1) |
| Changes | `dts/gs101.dtsi`, `dts/gs101-debug.dtsi` |

## The problem

A watchdog is a timer that resets the chip unless software keeps restarting it, which is called *feeding* it. Software that hangs stops feeding, and the reset recovers the device. To a watchdog, a kernel stopped in kgdb is a hang: no CPU runs, so nothing feeds anything. On this phone an Android service, `gs_watchdogd`, feeds the hardware watchdog every half of its timeout, and the timeout is 30 seconds (`timeout-sec = <30>`). Without changes, the phone resets within about 30 seconds of stopping in kgdb.

Several mechanisms on this phone treat a stopped kernel as a hang. This patch, the [soc/gs patch](../../../google-modules/soc/gs/0001-soc-gs-keep-the-watchdogs-from-resetting-a-kgdb-stop.md) and the packaging step handle them together:

| Mechanism | What it is | Handled by |
| --- | --- | --- |
| `s3c2410_wdt` | The chip's two hardware watchdogs, `watchdog_cl0` and `watchdog_cl1` | soc/gs patch |
| Multistage mode | `watchdog_cl1` run as an early-warning stage for `watchdog_cl0`, wired to `hardlockup-debug` | This patch, and packaging |
| `hardlockup-watchdog` | A vendor module in which each CPU checks that another CPU's timer is still ticking | This patch, and packaging |
| EHLD | The Exynos Hardware Lockup Detector, which watches each core's instruction count from outside | soc/gs patch, and packaging |
| Kernel lockup detectors | Linux's own soft-lockup and hung-task checks | The kernel command line set by `scripts/package.sh` |

The device tree is the description of the hardware the kernel reads at boot. Each piece of hardware is a *node* with *properties*, and two of these mechanisms are configured only there.

## What the patch changes

### `gs101.dtsi`: no multistage watchdog

`watchdog_cl1` is marked `use_multistage_wdt`. In that mode, every time the driver starts a watchdog it also starts `watchdog_cl1` as an early stage, at 80% of the main watchdog's timeout and set to raise an interrupt rather than reset. The `hardlockup-debug` module registers that interrupt with the chip's secure firmware as a FIQ, a high-priority interrupt, and its handler saves the state of every CPU and then reboots the phone. The driver also skips the PMU reset mask (see the soc/gs patch) for a watchdog in multistage mode.

So in multistage mode a kgdb stop of more than about 24 seconds (80% of 30) can end in a reboot, whatever `soft_noboot` says. The patch removes the property. `watchdog_cl1` is then an ordinary watchdog, which only runs if something opens it, and gets the same treatment as `watchdog_cl0`.

### `gs101-debug.dtsi`: hardlockup-watchdog does not panic

The `hardlockup-watchdog` node is configured with `sampling_time = <4>`, `opportunity_count = <3>` and `panic = <1>`. Each CPU runs a timer every 4 seconds, and every third tick it checks the next CPU: if that CPU's timer has not advanced for 12 seconds, the CPU is declared locked and the module panics. While the kernel is stopped no CPU runs any check, so the alarm comes right after it resumes, when the first check sees how long the other CPUs were silent. The patch sets `panic = <0>`, which turns the panic into a kernel warning with a backtrace.

The module also has a `hardlockup_panic` parameter that would do the same from the kernel command line. The device tree keeps the setting with the rest of the gs101 debug configuration.

### What the patch does not do

It does not disable the watchdog nodes, which looks like the obvious fix and makes things worse. The bootloader starts the hardware watchdog before Linux runs. When the driver loads it stops that watchdog, and Android's `gs_watchdogd` starts it again later and feeds it. Disable the node and the driver never loads, nothing stops the bootloader's watchdog, and the phone resets about a minute into every boot.

## Limits

- **`hardlockup-debug.ko` must stay out of `modules.load`.** No patch changes it, and its handler reboots the phone whenever `watchdog_cl1`'s interrupt fires. Without multistage mode nothing starts `watchdog_cl1` alongside `watchdog_cl0`, but leaving the module out is what guarantees it. `scripts/package.sh` does this, together with `hardlockup-watchdog.ko` and `ehld.ko`, which the patches already make harmless.
- **Which of these measures are strictly needed was not tested one by one.** The working setup uses all of them.
- **For a production kernel**, restore `use_multistage_wdt` and `panic = <1>`.

## Verify

Apply and build it with the README's [Apply the patches](../../../../../README.md#apply-the-patches) and [Build](../../../../../README.md#build) sections. The Build check shows `panic = <0x00>` in the built device tree and no `use_multistage_wdt;`. On the phone, a kernel held in kgdb for several minutes resumes afterwards, and `uptime` keeps counting from before the stop: the phone did not restart.
