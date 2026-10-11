# scripts/gdb: symbols: use a raw string for the module pattern

| Patch | Details |
| --- | --- |
| File | `patches/aosp/0005-scripts-gdb-symbols-use-a-raw-string-for-the-module-.patch` |
| Project | `kernel/aosp` (the GKI kernel source) |
| Changes | `scripts/gdb/linux/symbols.py` |

## The problem

The kernel ships Python helpers for gdb in `scripts/gdb/`, loaded through `vmlinux-gdb.py`. They add commands such as `lx-symbols`, `lx-dmesg` and `lx-ps`, and functions such as `$lx_task_by_pid()`. The `--kgdb` build copies them into the build output.

Python 3.12 and later warn about one line in them. With Ubuntu 26.04's `gdb-multiarch`, which embeds Python 3.14, every start prints:

```
symbols.py:85: SyntaxWarning: "\." is an invalid escape sequence. Such sequences will not work in the future. Did you mean "\\."? A raw string is also an option.
```

Line 85 builds a regular expression in an ordinary string, `".*/{0}\.ko(?:.debug)?$"`. In an ordinary Python string `\.` is not a valid escape. Python keeps the backslash, so the expression still works, but newer versions warn about it and will eventually reject it.

## What the patch changes

It adds an `r` prefix, making the pattern a raw string, in which a backslash is just a backslash. The regular expression is exactly the same. Mainline Linux made the identical change in commit `ded79af42f11`, "scripts/gdb/symbols: fix invalid escape sequence warning" (March 2024). The 6.1 kernel branch used here does not have it.

This patch is cosmetic: without it everything works, with a warning on every gdb start.

## Limits

None. The change only affects how Python reads the string.

## Verify

Apply and build it with the README's [Apply the patches](../../README.md#apply-the-patches) and [Build](../../README.md#build) sections. Starting gdb with `scripts/kgdb-attach.py` prints no `SyntaxWarning`.
