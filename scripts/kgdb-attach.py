#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Attach gdb to the Pixel 6 kernel over USB.

This checks the phone and then starts gdb-multiarch on the phone's USB
serial port with vmlinux, the kernel sources and the kernel's gdb scripts
loaded. gdb's first packet stops the running kernel in kgdb.

    scripts/kgdb-attach.py          stop the running kernel and attach
    scripts/kgdb-attach.py --boot   reboot, attach as soon as the port is up
    scripts/kgdb-attach.py --now    skip the checks, attach straight away

While the kernel is stopped the phone is frozen. `continue` or `detach` in
gdb resumes it, and Ctrl+C stops it again. If gdb cannot reach it, hold
Volume Down + Power for about 10 seconds to get back to the bootloader.
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
    runs kgdb over USB."""
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
    for needed in ("kgdb_acm", "kgdboc="):
        if needed not in cmdline:
            fail(f"{needed} is not on the kernel command line; "
                 "flash vendor_boot from scripts/package.sh")
    log(f"  kgdboc={re.search(r'kgdboc=(\S+)', cmdline).group(1)}")
    delay = re.search(r"kgdb_break_after=(\d+)", cmdline)
    if delay:
        log(f"  kgdb_break_after={delay.group(1)}: the kernel also stops "
            "on its own that long after boot")


def reboot_to_port(port, timeout):
    """Reboot the phone and wait for its kgdb port to come back, which
    happens a few seconds into boot, well before Android has booted."""
    log("rebooting the phone")
    if adb("reboot", timeout=20) is None:
        fail("adb reboot failed")

    # The phone drops off USB within a few seconds of the reboot command.
    deadline = time.monotonic() + 30
    while os.path.exists(port):
        if time.monotonic() > deadline:
            fail(f"{port} did not go away after adb reboot")
        time.sleep(0.2)
    log(f"phone went down, waiting for {port}")

    start = time.monotonic()
    retries = 0
    last_check = start
    with tqdm(total=timeout, desc="booting", bar_format=BAR_FORMAT,
              leave=False) as bar:
        while not os.path.exists(port):
            now = time.monotonic()
            if now - start > timeout:
                bar.close()
                fail(f"{port} did not appear within {timeout} s; check the "
                     "cable, and see README, Recovery")
            # Some reboots end in the bootloader; see wait_for_android().
            if now - last_check > 5:
                last_check = now
                if in_bootloader():
                    if retries == BOOTLOADER_RETRIES:
                        bar.close()
                        fail(f"the phone is still in the bootloader after "
                             f"{retries} attempts to boot it; see README, Recovery")
                    retries += 1
                    log(f"the phone stopped in the bootloader instead of "
                        f"booting; running `fastboot reboot` (attempt "
                        f"{retries} of {BOOTLOADER_RETRIES})")
                    subprocess.run(["fastboot", "reboot"],
                                   stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=20)
                    start = time.monotonic()
                    bar.reset(total=timeout)
                    continue
            if int(now - start) != bar.n:
                bar.n = min(int(now - start), timeout)
                bar.refresh()
            time.sleep(0.05)
    log(f"{port} appeared after {time.monotonic() - start:.0f} s")


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
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--boot", action="store_true",
                      help="reboot the phone and stop the kernel as soon as "
                           "its USB port is up, early in boot")
    mode.add_argument("--now", action="store_true",
                      help="skip the checks and attach straight away, for "
                           "example to a kernel that is already stopped")
    p.add_argument("--port", default="/dev/ttyACM0",
                   help="host serial port for the phone (default: %(default)s)")
    p.add_argument("--gdb", default="gdb-multiarch",
                   help="gdb binary (default: %(default)s)")
    p.add_argument("--boot-timeout", type=int, default=180, metavar="SECONDS",
                   help="how long to wait for Android to boot (default: %(default)s)")
    args = p.parse_args()

    release = preflight(args)

    if args.now:
        log("skipping the phone checks")
    else:
        check_phone(release, args.port, args.boot_timeout)
        if args.boot:
            reboot_to_port(args.port, args.boot_timeout)

    wait_for_port(args.port)

    # gdb's first packet stops the kernel. Right after boot the port can
    # appear a moment before kgdboc binds to it, and that packet is lost,
    # so connect with a short timeout, which makes gdb resend it soon.
    gdb = [args.gdb, "-q",
           "-ex", "set confirm off",
           "-ex", "set pagination off",
           "-ex", "set architecture aarch64",
           "-ex", "set serial baud 115200",
           "-ex", f"directory {SOURCES}",
           "-ex", f"set substitute-path / {SOURCES}/",
           "-ex", f"source {GDB_SCRIPTS}",
           "-ex", "set remotetimeout 2",
           "-ex", f"target remote {args.port}",
           "-ex", "set remotetimeout 60",
           str(VMLINUX)]
    log(f"starting {args.gdb}; connecting stops the kernel, `continue` or "
        "`detach` resumes it, Ctrl+C stops it again")
    sys.stdout.flush()
    os.execvp(gdb[0], gdb)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        log("interrupted")
        sys.exit(130)
