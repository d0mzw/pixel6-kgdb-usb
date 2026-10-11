# soc: gs: keep the watchdogs from resetting a kgdb stop

| Patch | Details |
| --- | --- |
| File | `patches/private/google-modules/soc/gs/0001-soc-gs-keep-the-watchdogs-from-resetting-a-kgdb-stop.patch` |
| Project | `kernel/private/google-modules/soc/gs` (Google's drivers for its Tensor chips) |
| Changes | `drivers/watchdog/s3c2410_wdt.c`, `drivers/soc/google/debug/exynos-ehld.c` |

## The problem

A kernel stopped in kgdb looks hung to every watchdog. The [gs101 device tree patch](../../../devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md) explains the watchdogs on this chip and handles the ones configured in the device tree. This patch handles two that are configured in driver code: the hardware watchdog driver, `s3c2410_wdt`, and EHLD.

Each driver has a module parameter that does part of the job: `soft_noboot` for `s3c2410_wdt` and `noehld` for EHLD. On this phone, module parameters can be set both ways Android supports. In a test boot, `s3c2410_wdt.tmr_margin=45` on the kernel command line and `options s3c2410_wdt tmr_atboot=1` in `modules.options` both showed up in the loaded driver, read back through kgdb. But `soft_noboot` alone does not stop a reset on gs101, as explained below, so the driver needs a code change anyway.

## What the patch changes

### `s3c2410_wdt`: expiry raises an interrupt, not a reset

`soft_noboot` defaults to 1. With it set, the driver programs the watchdog's control register, `WTCON`, to raise an interrupt when the timer runs out (`INTEN`) instead of resetting the chip (`RSTEN`). The interrupt handler only logs `watchdog timer expired (irq)` and feeds the watchdog again.

On gs101 that is not enough. The reset also reaches the chip through the PMU, the power management unit, and `RSTEN` does not gate that path. The driver configures the PMU side with `pmu_reset_func(wdt, mask)`; on gs101 that is `s3c2410wdt_noncpu_int_en()`, which sets or clears the watchdog's bit in the PMU's `CLUSTERx_NONCPU_INT_EN` register. `true` masks the watchdog's reset request, `false` lets it through. Stock code passes `false` in three places:

- at probe, when the driver starts
- in `s3c2410wdt_dev_resume()`, after the device resumes from suspend
- in `s3c2410wdt_syscore_resume()`, after the system resumes

The patch passes `soft_noboot` instead at all three, so the PMU reset stays masked whenever `soft_noboot` is set, including after the phone has slept. It also corrects the parameter's description, which `modinfo` shows, to say the default is now 1.

### `exynos-ehld`: off

`noehld` defaults to 1, which disables EHLD at start-up. EHLD reads each core's instruction counter from outside the core, and a stopped core looks locked to it. The driver reads `noehld` once during initialisation, so changing it later through sysfs has no effect.

### Why defaults rather than parameters

With the PMU change in the code anyway, changing the defaults keeps everything in one place, and the debug kernel is safe however it is booted, whatever its command line and `modules.options` say.

## Limits

- **The hardware watchdog can no longer reset the phone.** With `soft_noboot` set, an expiry only logs, and the PMU path is masked, so a phone that hangs for real, outside the debugger, stays hung until Volume Down + Power. This is a debug kernel only: set both defaults back to 0, and restore the gs101 device tree, for any other use.
- **`scripts/package.sh` also leaves `ehld.ko` out of `modules.load`,** so EHLD is not loaded at all in this setup. The default here keeps it harmless if it is. See the [gs101 patch's limits](../../../devices/google/gs101/0001-gs101-dts-keep-the-watchdogs-from-resetting-a-kgdb-s.md#limits).

## Verify

Apply and build it with the README's [Apply the patches](../../../../../README.md#apply-the-patches) and [Build](../../../../../README.md#build) sections. On the phone, a kernel held in kgdb for well over the 30-second watchdog timeout resumes afterwards, and `uptime` keeps counting from before the stop: the phone did not restart.
