#!/usr/bin/env python3
"""Attach gdb to the Pixel 6 kernel over USB.

By default this reboots the phone, waits for Android to boot, waits for the
kernel to stop in kgdb (kgdb_break_after= on the kernel command line), and
then starts gdb-multiarch on the phone's USB serial port with vmlinux, the
kernel sources and the kernel's gdb scripts loaded.

    scripts/kgdb-attach.py          reboot, wait for the trap, attach
    scripts/kgdb-attach.py --now    the kernel is already stopped; attach

While the kernel is stopped the phone is frozen. `continue` or `detach` in
gdb resumes it. If gdb cannot reach it, hold Volume Down + Power for about
10 seconds to get back to the bootloader.
"""
import argparse
import mmap
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    sys.exit("tqdm is missing. Set up the virtual environment first:\n"
             "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt\n"
             "  source .venv/bin/activate")

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT / "kernel"
VMLINUX = TREE / "out/raviole/dist/vmlinux"
GDB_SCRIPTS = TREE / "bazel-bin/aosp/kernel_aarch64/gdb_scripts/vmlinux-gdb.py"
SOURCES = TREE / "aosp"

# kgdboc retries binding its tty every 10 s, up to 10 times, before it
# gives up and does not trap (see docs/aosp/0004-*.md).
KGDBOC_RETRY_WINDOW = 100

BAR_FORMAT = "{desc}: {percentage:3.0f}%|{bar}| {n:.0f}/{total:.0f} s"


def log(msg):
    tqdm.write(f"[{time.strftime('%H:%M:%S')}] {msg}")


def fail(msg):
    log(f"error: {msg}")
    sys.exit(1)


def adb(*args, timeout=10):
    """Run an adb command and return its stdout, or None if it failed.

    stdin is closed so adb cannot swallow input meant for gdb later."""
    try:
        r = subprocess.run(["adb", *args], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def in_bootloader():
    """True if the phone sits in the bootloader's fastboot mode. fastbootd,
    the userspace fastboot, also answers `fastboot devices`, so ask."""
    try:
        r = subprocess.run(["fastboot", "devices"], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=5)
    except subprocess.TimeoutExpired:
        return False
    if "fastboot" not in r.stdout:
        return False
    r = subprocess.run(["fastboot", "getvar", "is-userspace"],
                       stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=5)
    return "is-userspace: no" in r.stderr + r.stdout


def alive():
    """True if the kernel is running. `adb devices` keeps listing a frozen
    phone, so run a command on it instead."""
    return adb("shell", "true", timeout=8) is not None


def vmlinux_release():
    """The release string compiled into vmlinux, from its linux_banner."""
    with open(VMLINUX, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        i = m.find(b"Linux version ")
        if i < 0:
            return None
        return m[i + 14:m.find(b" ", i + 14)].decode()


def preflight(args):
    log("checking the host")
    for tool in ("adb", "fastboot", args.gdb):
        path = shutil.which(tool)
        if not path:
            fail(f"{tool} not found on PATH")
        log(f"  {tool}: {path}")
    for what, path in (("vmlinux", VMLINUX), ("gdb scripts", GDB_SCRIPTS),
                       ("kernel sources", SOURCES)):
        if not path.exists():
            fail(f"{what} not found at {path}; build the kernel first")
        log(f"  {what}: {path.relative_to(ROOT)}")
    release = vmlinux_release()
    log(f"  vmlinux release: {release}")
    return release


# Some reboots end in the bootloader instead of Android; another
# `fastboot reboot` gets it going. Give up after this many.
BOOTLOADER_RETRIES = 3


def wait_for_android(timeout):
    """Wait until adb answers, booting the phone out of the bootloader if
    it stops there. Fails after `timeout` seconds."""
    start = time.monotonic()
    retries = 0
    with tqdm(total=timeout, desc="booting", bar_format=BAR_FORMAT,
              leave=False) as bar:
        while not alive():
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                bar.close()
                fail(f"the phone did not boot within {timeout} s; check the "
                     "cable, and see README, Recovery")
            if in_bootloader():
                if retries == BOOTLOADER_RETRIES:
                    bar.close()
                    fail(f"the phone is still in the bootloader after "
                         f"{retries} attempts to boot it; see README, Recovery")
                retries += 1
                log(f"the phone stopped in the bootloader instead of booting; "
                    f"running `fastboot reboot` (attempt {retries} of "
                    f"{BOOTLOADER_RETRIES})")
                subprocess.run(["fastboot", "reboot"], stdin=subprocess.DEVNULL,
                               capture_output=True, timeout=20)
                start = time.monotonic()
                bar.reset(total=timeout)
                continue
            bar.n = min(elapsed, timeout)
            bar.refresh()
            time.sleep(1)
    log(f"booted after {time.monotonic() - start:.0f} s")


def check_phone(release, port, timeout):
    """Check the running kernel is the one we have symbols for and that it
    is set up to trap. Returns kgdb_break_after in seconds."""
    log("checking the phone")
    if not alive():
        # A stopped kernel keeps its USB serial port but stops answering
        # adb. A booting phone has no USB device at all yet.
        if os.path.exists(port):
            fail(f"adb does not answer but {port} is present, so the kernel "
                 "is probably already stopped in kgdb; attach with --now")
        log("adb does not answer yet, waiting for the phone to boot")
        wait_for_android(timeout)
    model = adb("shell", "getprop", "ro.product.device")
    running = adb("shell", "uname", "-r")
    log(f"  device: {model}")
    log(f"  running kernel: {running}")
    if running != release:
        fail(f"the phone runs {running} but vmlinux is {release}; "
             "flash the images from this build (README, Flash)")
    cmdline = adb("shell", "cat", "/proc/cmdline") or ""
    for needed in ("kgdb_acm", "kgdboc=", "kgdb_break_after="):
        if needed not in cmdline:
            fail(f"{needed} is not on the kernel command line; "
                 "flash vendor_boot from scripts/package.sh")
    tty = re.search(r"kgdboc=(\S+)", cmdline).group(1)
    delay = int(re.search(r"kgdb_break_after=(\d+)", cmdline).group(1))
    log(f"  kgdboc={tty}, kgdb_break_after={delay}")
    return delay


def reboot(timeout):
    log("rebooting the phone")
    if adb("reboot", timeout=20) is None:
        fail("adb reboot failed")

    # The phone drops off USB within a few seconds of the reboot command.
    deadline = time.monotonic() + 30
    while alive():
        if time.monotonic() > deadline:
            fail("the phone did not go down after adb reboot")
        time.sleep(1)
    log("phone went down, waiting for Android to boot")
    wait_for_android(timeout)


def wait_for_trap(delay):
    """Wait until the kernel stops. Progress is the phone's uptime against
    kgdb_break_after; after that, kgdboc may still be retrying its bind."""
    uptime = float((adb("shell", "cat", "/proc/uptime") or "0").split()[0])
    log(f"phone uptime {uptime:.0f} s, kernel stops at {delay} s")
    limit = delay + KGDBOC_RETRY_WINDOW
    t0 = time.monotonic() - uptime
    with tqdm(total=delay, desc="until the kernel stops", bar_format=BAR_FORMAT,
              leave=False) as bar:
        noted = False
        while alive():
            up = time.monotonic() - t0
            if up > limit:
                bar.close()
                fail(f"the kernel did not stop by {limit} s of uptime; kgdboc "
                     f"never bound its tty. Check `adb logcat -b kernel | grep kgdboc`")
            # Noticing a frozen phone takes up to one adb timeout, so only
            # report a delay once kgdboc's first retry is due.
            if up > delay + 10 and not noted:
                log("past kgdb_break_after, kgdboc is still binding its tty "
                    "(retries every 10 s)")
                noted = True
            bar.n = min(up, delay)
            bar.refresh()
            time.sleep(2)
    log(f"kernel stopped (noticed at {time.monotonic() - t0:.0f} s of uptime)")


def wait_for_port(port, timeout=20):
    start = time.monotonic()
    while not os.path.exists(port):
        if time.monotonic() - start > timeout:
            fail(f"{port} did not appear; is the phone connected?")
        time.sleep(0.5)
    if not os.access(port, os.R_OK | os.W_OK):
        fail(f"no permission to open {port}; add the udev rule (README, Debug)")
    log(f"serial port {port} is ready")


def main():
    p = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--now", action="store_true",
                   help="the kernel is already stopped; attach without rebooting")
    p.add_argument("--port", default="/dev/ttyACM0",
                   help="host serial port for the phone (default: %(default)s)")
    p.add_argument("--gdb", default="gdb-multiarch",
                   help="gdb binary (default: %(default)s)")
    p.add_argument("--boot-timeout", type=int, default=180, metavar="SECONDS",
                   help="how long to wait for Android to boot (default: %(default)s)")
    args = p.parse_args()

    release = preflight(args)

    if args.now:
        if alive():
            fail("the kernel is still running, so there is nothing to attach to; "
                 "run without --now to reboot and wait for the trap")
        log("the kernel is not responding to adb; assuming it is stopped in kgdb")
    else:
        delay = check_phone(release, args.port, args.boot_timeout)
        reboot(args.boot_timeout)
        wait_for_trap(delay)

    wait_for_port(args.port)

    gdb = [args.gdb, "-q",
           "-ex", "set confirm off",
           "-ex", "set pagination off",
           "-ex", "set architecture aarch64",
           "-ex", "set serial baud 115200",
           "-ex", "set remotetimeout 60",
           "-ex", f"directory {SOURCES}",
           "-ex", f"set substitute-path / {SOURCES}/",
           "-ex", f"source {GDB_SCRIPTS}",
           "-ex", f"target remote {args.port}",
           str(VMLINUX)]
    log(f"starting {args.gdb}; `continue` or `detach` resumes the phone")
    sys.stdout.flush()
    os.execvp(gdb[0], gdb)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log("interrupted")
        sys.exit(130)
