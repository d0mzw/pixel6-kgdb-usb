#!/bin/bash
# Apply every patch in patches/ to the matching project in kernel/.
#
# patches/<project>/*.patch goes to kernel/<project>, in file name order,
# on a new branch named kgdb. Run from anywhere after syncing kernel/.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
REPO=${REPO:-$HOME/bin/repo}

cd "$ROOT/kernel"
for project in $(cd "$ROOT/patches" && find . -name '*.patch' -printf '%h\n' | sort -u); do
	project=${project#./}
	echo "== $project"
	"$REPO" start kgdb "$project"
	git -C "$project" am "$ROOT/patches/$project"/*.patch
done
