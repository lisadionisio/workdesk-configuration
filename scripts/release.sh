#!/usr/bin/env bash
# release.sh — build and publish a WorkDesk OS release.
#
# Reads version from config/VERSION, builds a tarball with this layout:
#
#   workdesk/        (snapshot of config/, excluding defaults/, state/, snapshots/)
#   manifest.json    ({"version": "1.3.0", "migrations": ["script.sh", ...]})
#   migrations/      (the actual scripts, copied from repo migrations/ dir)
#
# Then writes a SHA256 sidecar and creates a GitHub release with both files
# attached, via `gh release create`.
#
# Usage:
#   scripts/release.sh                    # build + publish
#   scripts/release.sh --dry-run          # build only; no upload
#   scripts/release.sh --publish-built    # publish the exact reviewed build
#   scripts/release.sh --notes-file FILE  # use FILE as release notes (default: auto)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

"$REPO_ROOT/tests/genericity-check.sh"

DRY_RUN=0
PUBLISH_BUILT=0
NOTES_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)    DRY_RUN=1; shift ;;
    --publish-built) PUBLISH_BUILT=1; shift ;;
    --notes-file) NOTES_FILE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done
(( DRY_RUN == 0 || PUBLISH_BUILT == 0 )) || { echo "Choose build-only or publish-built, not both." >&2; exit 1; }

[[ -f "config/VERSION" ]] || { echo "Missing config/VERSION" >&2; exit 1; }
VERSION="$(head -n 1 config/VERSION | tr -d '[:space:]')"
TAG="v$VERSION"
TARBALL_NAME="workdesk-os-${VERSION}.tar.gz"
DIST="$REPO_ROOT/dist"
TARBALL="$DIST/$TARBALL_NAME"
SHA_FILE="$TARBALL.sha256"
BUILD_RECEIPT="$TARBALL.build.json"
SOURCE_COMMIT="$(git rev-parse HEAD 2>/dev/null || true)"
SOURCE_CLEAN=0
if [[ -n "$SOURCE_COMMIT" ]] && [[ -z "$(git status --porcelain --untracked-files=normal)" ]]; then
  SOURCE_CLEAN=1
fi

if (( DRY_RUN == 0 )); then
  command -v gh >/dev/null || { echo "gh CLI required" >&2; exit 1; }
  [[ "$SOURCE_CLEAN" == 1 ]] || { echo "Publication requires a clean committed source tree." >&2; exit 1; }
  if gh release view "$TAG" >/dev/null 2>&1; then
    echo "Release $TAG already exists. Bump config/VERSION first." >&2
    exit 1
  fi
fi

if (( PUBLISH_BUILT )); then
  # The receipt binds the tested bytes to the reviewed commit, not a later rebuild.
  python3 - "$TARBALL" "$SOURCE_COMMIT" "$VERSION" <<'PY'
import hashlib, json, sys
from pathlib import Path
archive=Path(sys.argv[1])
receipt=json.loads(Path(str(archive)+'.build.json').read_text())
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
sidecar=Path(str(archive)+'.sha256').read_text().split()
if (receipt.get('source_commit')!=sys.argv[2] or receipt.get('version')!=sys.argv[3]
    or receipt.get('source_clean') is not True or receipt.get('sha256')!=digest
    or sidecar!=[digest, archive.name]):
    raise SystemExit('Built artifact does not match its reviewed commit, version or checksum.')
PY
else
# Stage the release tree in a temp dir.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/workdesk"

# Copy config/ into staging, excluding what shouldn't ship.
rsync -a \
  --exclude='defaults/' \
  --exclude='state/' \
  --exclude='snapshots/' \
  --exclude='.DS_Store' \
  --exclude='._*' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='operator-policy.md' \
  config/ "$STAGE/workdesk/"

# Migrations: if migrations/ exists at repo root, copy and list scripts in
# lexicographic order. Manifest is JSON so the runtime has one structured
# source of truth for release metadata.
MIGS_JSON="[]"
if [[ -d "migrations" ]]; then
  mkdir -p "$STAGE/migrations"
  shopt -s nullglob
  declare -a migs=()
  for f in migrations/*.sh; do
    cp "$f" "$STAGE/migrations/"
    migs+=("$(basename "$f")")
  done
  shopt -u nullglob
  if (( ${#migs[@]} > 0 )); then
    MIGS_JSON=$(printf '%s\n' "${migs[@]}" | LC_ALL=C sort \
      | python3 -c "import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
  fi
fi

python3 -c "
import json
print(json.dumps({'version': '$VERSION', 'migrations': $MIGS_JSON}, indent=2))
" > "$STAGE/manifest.json"

# Build the tarball with mode bits and symlinks preserved. macOS copyfile
# metadata is host-local and must not become synthesized AppleDouble entries.
mkdir -p "$DIST"
COPYFILE_DISABLE=1 tar -czpf "$TARBALL" -C "$STAGE" .

# SHA256 sidecar.
( cd "$DIST" && shasum -a 256 "$TARBALL_NAME" > "$TARBALL_NAME.sha256" )
python3 - "$TARBALL" "$SOURCE_COMMIT" "$SOURCE_CLEAN" "$VERSION" <<'PY'
import hashlib,json,sys
from pathlib import Path
archive=Path(sys.argv[1])
Path(str(archive)+'.build.json').write_text(json.dumps({
    'source_commit':sys.argv[2], 'source_clean':sys.argv[3]=='1',
    'version':sys.argv[4], 'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()
},indent=2)+'\n')
PY

echo "Built: $TARBALL"
echo "       $SHA_FILE"
echo "       sha256: $(awk '{print $1}' "$SHA_FILE")"
fi

if (( DRY_RUN )); then
  echo "Dry-run; skipping gh release create."
  exit 0
fi

# Notes: use --generate-notes if no file provided.
GH_ARGS=(release create "$TAG" "$TARBALL" "$SHA_FILE" --target "$SOURCE_COMMIT" --title "WorkDesk OS $TAG")
if [[ -n "$NOTES_FILE" ]]; then
  GH_ARGS+=(--notes-file "$NOTES_FILE")
else
  GH_ARGS+=(--generate-notes)
fi

gh "${GH_ARGS[@]}"
echo "Released: https://github.com/BenaliHQ/workdesk-configuration/releases/tag/$TAG"
