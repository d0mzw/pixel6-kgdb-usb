# Context for agents

This file is for an AI agent (or a person) using this repository to debug the Android kernel on a Pixel 6 or to investigate a kernel issue. Read it before running anything. The [README](README.md) is the step-by-step setup guide; the documents under `docs/` explain each patch.

## What this project is

A debug build of the Pixel 6 (`oriole`) kernel that runs **kgdb over the USB cable**, with no UART or debug board. gdb on a Linux host talks to the phone's kernel through a USB serial port (`/dev/ttyACM0` on the host, `ttyGS3` on the phone), on the same cable as adb.

- Kernel: GKI 6.1 (`6.1.124-android14-11`), branch `android-gs-raviole-6.1-android16`, pinned in `manifest/pinned.xml`.
- Android: the stock factory image `oriole-cp1a.260405.005`, with only the kernel partitions replaced.
- The patches add polled I/O to the USB gadget serial driver so kgdb can use it, add a kgdb serial port without root (`kgdb_acm`), keep the gs101 watchdogs from resetting a stopped kernel, and let gdb stop a running kernel by connecting or by Ctrl+C.

## Layout

| Path | What it is |
| --- | --- |
| `README.md` | Setup, build, package, flash, debug, known issues, recovery |
| `patches/<project>/` | `git format-patch` series, one directory per project under `kernel/` |
| `docs/<project>/` | One document per patch, same name, explaining what and why |
| `scripts/apply-patches.sh` | Applies every patch series on a `kgdb` branch |
| `scripts/package.sh` | Builds flashable `images/` from the build output and the factory image; sets the debug kernel command line |
| `scripts/kgdb-attach.py` | Checks host and phone, then starts gdb on the serial port |
| `kernel/` | The synced kernel tree (not tracked) |
| `factory/` | The stock factory image (not tracked) |
| `images/` | Images to flash (not tracked) |

## Hard rules

These protect the phone. Follow them without exception.

- **Never run `fastboot flashing lock`**, and never suggest it. Locking with a custom kernel can leave the phone unable to boot.
- **Flash `boot`, `vendor_boot` and `vendor_dlkm` together, from one build and one `scripts/package.sh` run.** Mixed builds leave modules that do not match the kernel.
- **Before any flash, check that the factory image is on disk:** `ls factory/oriole-cp1a.260405.005/flash-all.sh`. It is the way back.
- **If the phone hangs** (frozen and gdb cannot reach it): hold Volume Down + Power for about 10 seconds to reach the bootloader, then reflash or choose Start. The watchdogs are disabled, so nothing else will recover it.
- **Do not run `svc usb setFunctions adb`** on the phone; it breaks adbd.
- Flashing the factory image with `flash-all.sh` wipes the phone; ask the user first.

## Python environment

All Python work in this repository runs in the virtual environment `.venv/` at the repository root, never in the system Python. Today it only holds `tqdm` (for `scripts/kgdb-attach.py`'s progress bars), pinned in `requirements.txt`, but every script added later should use it too.

- Create it once, if it is missing: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`
- Activate it before running scripts: `source .venv/bin/activate`, or call `.venv/bin/python` directly.
- A new dependency goes into `requirements.txt` with an exact version (`package==x.y.z`), and is installed with `.venv/bin/pip install -r requirements.txt`. Do not `pip install` anything outside `.venv/`.
- `.venv/` is ignored by git; only `requirements.txt` is committed.

## Typical state

Unless told otherwise, assume the README has been followed: the kernel tree is synced and patched, built, packaged into `images/`, and flashed; the phone runs the debug kernel with Android booted; the host has `adb`, `fastboot`, `gdb-multiarch`, the udev rule for `/dev/ttyACM*`, and the Python virtual environment in `.venv/`. Check rather than assume:

```bash
adb shell uname -r                                    # 6.1.124-android14-11
adb shell cat /proc/cmdline | tr ' ' '\n' | grep kgdb # kgdb_acm, kgdboc=ttyGS3
ls -l /dev/ttyACM0
```

Build, package and flash commands are in the README's Build, Package and Flash sections. A build takes a few minutes; the build is reproducible, so `System.map` addresses match between identical trees.

## Getting into the debugger

| Way in | How | When the kernel stops |
| --- | --- | --- |
| Break in | `scripts/kgdb-attach.py`, or plain `target remote /dev/ttyACM0` | As soon as gdb's first packet arrives |
| Ctrl+C | In an attached gdb, after `continue` | Within about 2 seconds |
| At boot | `scripts/kgdb-attach.py --boot` | About 5 seconds into boot, when the USB port comes up |
| Fixed time | `kgdb_break_after=<seconds>` added to `DEBUG_CMDLINE` in `scripts/package.sh`, then package and flash | At that uptime, every boot |
| Reattach | `scripts/kgdb-attach.py --now` | Attaches without checks, for a kernel already stopped |

Nothing before the USB port comes up (about 4 to 5 seconds into boot) can be debugged.

## Driving gdb as an agent

An interactive terminal is best (the README's demo was recorded that way). Without one, use batch mode. The essential points:

- **Always end with `detach`.** If gdb exits or is killed while the kernel is stopped, the phone stays frozen. Recover by attaching again and detaching:

  ```bash
  gdb-multiarch -q -nx -batch -ex 'set remotetimeout 5' -ex 'target remote /dev/ttyACM0' -ex detach kernel/out/raviole/dist/vmlinux
  ```

- **A one-shot inspection** (stop, look, resume):

  ```bash
  timeout 120 gdb-multiarch -q -nx -batch \
      -ex 'set debuginfod enabled off' \
      -ex 'set remotetimeout 5' \
      -ex 'target remote /dev/ttyACM0' \
      -ex 'bt 6' \
      -ex 'print some_global' \
      -ex detach \
      kernel/out/raviole/dist/vmlinux
  ```

  Give `timeout` enough room; a timeout that kills gdb mid-session leaves the phone stopped (see above).

- **Source and helper scripts:** add `-ex "directory kernel/aosp" -ex "set substitute-path / $PWD/kernel/aosp/" -ex "source kernel/bazel-bin/aosp/kernel_aarch64/gdb_scripts/vmlinux-gdb.py"` for source lines and the `lx-*` commands, as `scripts/kgdb-attach.py` does.
- **Breakpoints:** `break file.c:line`, then `continue`. In batch mode `continue` blocks until the breakpoint hits; pick a breakpoint that will hit, or drive gdb from a script that can send SIGINT to the gdb process (gdb turns it into Ctrl+C on the wire).
- **Piping commands works too:** `printf 'bt 3\ndetach\nquit\n' | scripts/kgdb-attach.py`.
- **While the kernel is stopped, adb hangs.** `adb devices` still lists the phone, but `adb shell` blocks. Put a `timeout` on every adb call made around a debug session.

### What works and what does not

- Tested and working: `bt`, `print`, `x`, breakpoints, `continue`, Ctrl+C, `detach`, `$lx_task_by_pid(pid)`, `lx-dmesg`. Other `lx-*` commands have not been tried over this link.
- `$lx_current()` does not: kgdb does not send `sp_el0`. Use `$lx_task_by_pid()` with the ID from `[Switching to Thread N]`.
- User-space memory cannot be read (arm64 PAN). Read the kernel's copy, such as `struct filename::name` instead of the user pointer.
- `lx-dmesg` reads the whole log over the serial port and takes minutes on a full log. Prefer targeted reads.
- Kernel uptime from a stopped kernel: `print ((unsigned long)jiffies - 4294892296UL) / 250` gives seconds (HZ is 250, `INITIAL_JIFFIES` is `-300 * HZ`). Jiffies do not advance while the kernel is stopped.
- In optimised code, caller frames often show stale argument values; trust frame #0 and locals that gdb does not mark `<optimized out>`.
- After Ctrl+C, gdb prints a line for every thread that started or ended while the kernel ran; that is normal.

## Investigating crashes

kgdb does **not** catch oopses or panics here: GKI sets `PANIC_TIMEOUT=-1` and Android sets `panic_on_oops`, so a crash reboots the phone. To investigate one after the fact, without root:

```bash
adb shell getprop sys.boot.reason            # e.g. kernel_panic,oops:_fatal_exception
adb shell getprop persist.sys.boot.reason.history
adb shell dumpsys dropbox --print SYSTEM_LAST_KMSG > last_kmsg.txt
```

`SYSTEM_LAST_KMSG` holds the tail of the crashed boot's kernel log, including the oops, registers and call trace. Symbolise kernel addresses with `kernel/out/raviole/dist/System.map` or `gdb vmlinux` (`info symbol 0x...`, `list *0x...`). Addresses above `_end` in `System.map` are in module space. The kernel log of the running boot is not readable from the adb shell on this user build; read it through gdb if needed.

To catch a bug live, set a breakpoint on the code path before it crashes. Breakpoints are handled by kgdb even though the crash itself is not; a breakpoint on `die` or `panic` should therefore stop the kernel before it reboots, but that has not been tested.

## Known issues

See the README's Known issues for details.

- **About 1 boot in 6 crashes** about a second in, in `ueventd` formatting a device's `SUBSYSTEM=` from unmapped module memory; the phone restarts by itself. It happens with and without the break-in patch; the module responsible is not known. Check `sys.boot.reason` before blaming a change on a reboot.
- **A reboot sometimes ends in the bootloader.** Run `fastboot reboot` again; `kgdb-attach.py` does this itself.
- **Untested:** system suspend with the kgdb port's request always queued, changing the USB mode (rebinding the gadget) while kgdb is in use, and unplugging the cable while the kernel is stopped.

## Changing the project

If a change is meant for the repository rather than a one-off experiment:

- Kernel changes are commits on the `kgdb` branch of the project under `kernel/`, exported with `git format-patch` into `patches/<project>/`. Check that the series applies cleanly to the pinned commit (for example with `git worktree add` at the pinned revision and `git am`).
- Every patch has a document in `docs/<project>/` with the same name and the same sections: a header table, The problem, What the patch changes, Limits, Verify.
- The README's expected output is real output, per command, from actual runs. Re-run commands rather than editing output by hand.
- The docs are public: no em dashes, tables with header rows, claims verified against the source or on the phone.
- Do not commit or push unless asked.
