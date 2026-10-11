#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Flatten a Kleaf module tree into the layout Android ships.

Kleaf installs modules as lib/modules/<release>/<subdir>/.../foo.ko, with
relative paths in modules.load and modules.dep. Android ships them flat, as
lib/modules/foo.ko, and first-stage init only reads the flat layout: given
the nested one, it loads no modules at all.

Usage: flatten.py <src lib/modules/<release>> <dst lib/modules> <path prefix>

<path prefix> is where the modules live on the device, used in modules.dep:
/vendor/lib/modules for vendor_dlkm, /lib/modules for the ramdisk.

Modules named in the DROP_MODULES environment variable are left out of
modules.load, so they are never loaded.
"""
import os
import re
import shutil
import sys

src, dst, prefix = sys.argv[1:4]
drop = set(os.environ.get("DROP_MODULES", "").split())
os.makedirs(dst, exist_ok=True)


def read(name):
    path = os.path.join(src, name)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return f.read().splitlines()


def write(name, lines):
    with open(os.path.join(dst, name), "w") as f:
        f.write("".join(line + "\n" for line in lines))


# Every .ko, flattened. Two modules with the same file name cannot be
# flattened, so stop rather than overwrite one.
seen = {}
for root, _, files in os.walk(src):
    for name in files:
        if not name.endswith(".ko"):
            continue
        if name in seen:
            sys.exit(f"two modules named {name}:\n  {seen[name]}\n  {root}")
        seen[name] = root
        shutil.copy2(os.path.join(root, name), os.path.join(dst, name))

load = read("modules.load")
if load is None:
    sys.exit(f"no modules.load in {src}")
load = [os.path.basename(line) for line in load if line.strip()]
dropped = [m for m in load if m in drop]
load = [m for m in load if m not in drop]
write("modules.load", load)

dep = read("modules.dep")
if dep is not None:
    write("modules.dep", [
        re.sub(r"\S+\.ko", lambda m: prefix + "/" + os.path.basename(m.group(0)), line)
        for line in dep
    ])

# These name modules, not paths, so they copy over unchanged.
for name in ("modules.alias", "modules.softdep"):
    lines = read(name)
    if lines is not None:
        write(name, lines)

missing = [m for m in load if not os.path.exists(os.path.join(dst, m))]
if missing:
    sys.exit(f"modules.load names missing modules: {missing[:5]}")

print(f"{len(seen)} modules, {len(load)} in modules.load"
      + (f", dropped {' '.join(dropped)}" if dropped else ""))
