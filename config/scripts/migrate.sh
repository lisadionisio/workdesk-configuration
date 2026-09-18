#!/usr/bin/env bash
# migrate.sh — WorkDesk OS update engine.
#
# Three subcommands:
#   check                                 Fetch latest release, verify SHA256,
#                                         extract to staging, output plan as JSON.
#   apply <staging> <resolutions.json>    Backup, apply files, run migrations,
#                                         finalize atomically.
#   restore <backup-id>                   Roll back to a prior backup.
#
# Claude (via /migrate SKILL.md) orchestrates: runs check, narrates the plan,
# gathers conflict resolutions, runs apply. The engine owns invariants —
# checksums, atomicity, backup boundaries.
#
# Scope: only ever modifies config/. Schema migrations may modify files
# inside config/ (e.g. operator-profile.md) but never operator data zones
# (personal/, atlas/, gtd/, intel/, system/).
#
# Release tarball layout:
#   workdesk/        -> becomes config/ content
#   manifest.json    -> {"version": "1.3.0", "migrations": ["script.sh", ...]}
#   migrations/      -> the actual migration scripts

set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
IFS=$'\n\t'

# ---- Constants ----------------------------------------------------------------

REPO="BenaliHQ/workdesk-configuration"
RELEASES_LATEST="https://api.github.com/repos/$REPO/releases/latest"

VAULT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
WD="$VAULT/config"
DEFAULTS="$WD/defaults"
VERSION_FILE="$WD/VERSION"
BACKUP_BASE="$VAULT/.workdesk-backups"
TMP_BASE="$VAULT/.workdesk-migrate-tmp"

# ---- Helpers ------------------------------------------------------------------

log()  { printf '%s\n' "$*" >&2; }
fail() { log "ERROR: $*"; exit 1; }

require() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

current_version() {
  if [[ -f "$VERSION_FILE" ]]; then
    head -n 1 "$VERSION_FILE" | tr -d '[:space:]'
  else
    echo "unknown"
  fi
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

# Compare two files (or absence) for content equality. Returns 0 if equal,
# 1 if different, 2 if either is missing.
files_equal() {
  local a="$1" b="$2"
  [[ -f "$a" && -f "$b" ]] || return 2
  cmp -s "$a" "$b"
}

# Walk a directory and emit relative paths (sorted). Excludes defaults/ subtree
# and any backup/staging directories that may have leaked in.
list_tree() {
  local root="$1"
  local exclude="${2:-}"
  ( cd "$root" && find . -type f -o -type l ) \
    | sed 's|^\./||' \
    | grep -v '^defaults/' \
    | grep -v '^state/migrate-' \
    | { if [[ -n "$exclude" ]]; then grep -v "^$exclude"; else cat; fi; } \
    | LC_ALL=C sort
}

# JSON-escape a string for embedding in the plan output. Only handles the
# characters we expect in file paths and short status strings.
json_escape() {
  printf '%s' "$1" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

# ---- check --------------------------------------------------------------------

cmd_check() {
  require curl
  require shasum
  require tar
  require python3

  local cur
  cur="$(current_version)"

  rm -rf "$TMP_BASE"
  mkdir -p "$TMP_BASE"

  local meta="$TMP_BASE/release.json"
  curl -fsSL "$RELEASES_LATEST" -o "$meta" \
    || fail "Could not fetch release metadata from GitHub. Check network and repo access."

  local new_tag tarball_url sha_url
  new_tag=$(python3 -c "import json; d=json.load(open('$meta')); print(d['tag_name'])")
  tarball_url=$(python3 -c "
import json
d=json.load(open('$meta'))
for a in d.get('assets', []):
    n=a.get('name','')
    if n.endswith('.tar.gz') and not n.endswith('.sha256'):
        print(a['browser_download_url']); break
")
  sha_url=$(python3 -c "
import json
d=json.load(open('$meta'))
for a in d.get('assets', []):
    if a.get('name','').endswith('.tar.gz.sha256'):
        print(a['browser_download_url']); break
")

  [[ -n "$tarball_url" ]] || fail "Latest release has no .tar.gz asset. Release tooling broken."
  [[ -n "$sha_url" ]]     || fail "Latest release has no .sha256 sidecar. Release tooling broken."

  local new_version="${new_tag#v}"

  if [[ "$cur" == "$new_version" ]]; then
    cat <<EOF
{"status":"up-to-date","current_version":$(json_escape "$cur"),"new_version":$(json_escape "$new_version")}
EOF
    return 0
  fi

  local tarball="$TMP_BASE/release.tar.gz"
  local sha_file="$TMP_BASE/release.tar.gz.sha256"
  curl -fsSL "$tarball_url" -o "$tarball" || fail "Tarball download failed."
  curl -fsSL "$sha_url"     -o "$sha_file" || fail "Checksum sidecar download failed."

  local expected actual
  expected=$(awk '{print $1}' "$sha_file")
  actual=$(sha256_file "$tarball")
  [[ "$expected" == "$actual" ]] \
    || fail "SHA256 mismatch. Expected $expected, got $actual. Aborting — do not trust this tarball."

  local extracted="$TMP_BASE/extracted"
  mkdir -p "$extracted"
  tar -xzpf "$tarball" -C "$extracted" \
    || fail "Tarball extraction failed."

  local new_wd="$extracted/workdesk"
  [[ -d "$new_wd" ]] || fail "Tarball missing 'workdesk/' directory. Release format wrong."

  build_plan "$cur" "$new_version" "$extracted" > "$extracted/reviewed-plan.json"
  cat "$extracted/reviewed-plan.json"
}

# Build and emit the plan as JSON, given the current version, new version,
# and path to the extracted release.
build_plan() {
  local cur="$1" new_version="$2" extracted="$3"
  local new_wd="$extracted/workdesk"

  local files_op  files_def  files_new
  files_op="$(list_tree  "$WD")"
  files_def="$(list_tree "$DEFAULTS")"
  files_new="$(list_tree "$new_wd")"

  local all
  all=$( ( printf '%s\n' "$files_op"; printf '%s\n' "$files_def"; printf '%s\n' "$files_new" ) \
         | grep -v '^$' | LC_ALL=C sort -u )

  local migrations="[]"
  if [[ -f "$extracted/manifest.json" ]]; then
    migrations=$(python3 -c "
import json
m=json.load(open('$extracted/manifest.json'))
print(json.dumps(m.get('migrations', [])))
")
  fi

  printf '{\n'
  printf '  "status":"update-available",\n'
  printf '  "current_version":%s,\n'  "$(json_escape "$cur")"
  printf '  "new_version":%s,\n'      "$(json_escape "$new_version")"
  printf '  "staging":%s,\n'          "$(json_escape "$extracted")"
  printf '  "migrations":%s,\n'       "$migrations"
  printf '  "files":{\n'

  local first=1
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    local op="$WD/$path"  def="$DEFAULTS/$path"  new="$new_wd/$path"
    local action
    if [[ -L "$op" ]]; then action=manual; else action=$(classify "$op" "$def" "$new"); fi
    if (( first )); then first=0; else printf ',\n'; fi
    local reviewed_hash=null
    [[ -f "$op" ]] && reviewed_hash="\"$(sha256_file "$op")\""
    printf '    %s:{"action":"%s","operator_sha256":%s}' "$(json_escape "$path")" "$action" "$reviewed_hash"
  done <<< "$all"

  printf '\n  }\n}\n'
}

# Classify a single file's state. Echoes one of:
#   no-op | clean-update | conflict | add | removed-in-release
#   operator-deleted-changed | operator-deleted-removed | operator-only
classify() {
  local op="$1" def="$2" new="$3"
  local op_exists def_exists new_exists
  [[ -f "$op"  ]] && op_exists=1  || op_exists=0
  [[ -f "$def" ]] && def_exists=1 || def_exists=0
  [[ -f "$new" ]] && new_exists=1 || new_exists=0

  if (( def_exists && op_exists && new_exists )); then
    if   files_equal "$op"  "$def" && files_equal "$def" "$new"; then echo "no-op"
    elif files_equal "$op"  "$def" && ! files_equal "$def" "$new"; then echo "clean-update"
    elif ! files_equal "$op" "$def" && files_equal "$def" "$new"; then echo "no-op"
    elif files_equal "$op"  "$new"; then echo "no-op"
    else echo "conflict"
    fi
  elif (( ! def_exists && new_exists && ! op_exists )); then
    echo "add"
  elif (( ! def_exists && new_exists && op_exists )); then
    files_equal "$op" "$new" && echo "no-op" || echo "conflict"
  elif (( def_exists && ! new_exists && op_exists )); then
    echo "removed-in-release"
  elif (( def_exists && ! new_exists && ! op_exists )); then
    echo "operator-deleted-removed"
  elif (( def_exists && new_exists && ! op_exists )); then
    if files_equal "$def" "$new"; then echo "operator-deleted-removed"
    else echo "operator-deleted-changed"
    fi
  elif (( ! def_exists && ! new_exists && op_exists )); then
    echo "operator-only"
  else
    echo "no-op"
  fi
}

# ---- apply --------------------------------------------------------------------

cmd_apply() {
  local staging="${1:-}" resolutions="${2:-}"
  [[ -n "$staging"     && -d "$staging"     ]] || fail "apply: staging dir missing or invalid."
  [[ -n "$resolutions" && -f "$resolutions" ]] || fail "apply: resolutions file missing."
  require rsync
  require python3
  mkdir -p "$TMP_BASE"

  local new_wd="$staging/workdesk"
  [[ -d "$new_wd" ]] || fail "apply: staging missing workdesk/ subdirectory."

  local prior_version
  prior_version="$(current_version)"

  # Determine new version: prefer manifest.json, fall back to staging/VERSION.
  local new_version="unknown"
  if [[ -f "$staging/manifest.json" ]]; then
    new_version=$(python3 -c "import json; print(json.load(open('$staging/manifest.json')).get('version','unknown'))")
  elif [[ -f "$new_wd/VERSION" ]]; then
    new_version=$(head -n 1 "$new_wd/VERSION" | tr -d '[:space:]')
  fi

  # 1. Backup config/ outside the directory to avoid recursion.
  mkdir -p "$BACKUP_BASE"
  local backup_id
  local backup
  backup="$(mktemp -d "$BACKUP_BASE/$(date '+%Y-%m-%d-%H%M%S')-XXXXXX")"
  backup_id="$(basename "$backup")"
  rsync -a "$WD/" "$backup/" || fail "Backup failed. Aborting before any writes."
  log "Backup created: $backup"

  # 2. Build authoritative plan from staging (so we apply against current state,
  #    not stale check output).
  local plan_json="$TMP_BASE/plan-apply.json"
  build_plan "$(current_version)" "$new_version" "$staging" > "$plan_json"

  # 3. Apply each file according to plan + resolutions.
  local rc=0
  python3 - "$plan_json" "$resolutions" "$WD" "$new_wd" "$backup_id" "$(dirname "${BASH_SOURCE[0]}")/checked-file-copy.py" <<'PYEOF' || rc=$?
import json, os, shutil, sys, importlib.util
plan_path, res_path, wd, new_wd, backup_id, primitive = sys.argv[1:7]
spec = importlib.util.spec_from_file_location("checked_copy", primitive)
checked = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checked)
plan = json.load(open(plan_path))
res  = json.load(open(res_path))
review_path = os.path.join(os.path.dirname(new_wd), "reviewed-plan.json")
reviewed = json.load(open(review_path))["files"] if os.path.isfile(review_path) else plan["files"]

def copy(src, dst, expected):
    try:
        checked.checked_copy(src, dst, expected)
    except (checked.ChangedTarget, OSError) as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.exit(3)

manual = [path for path, info in plan["files"].items() if info["action"] == "manual"]
if manual:
    sys.stderr.write("Symlink targets require manual reconciliation before any writes: " + ", ".join(manual) + "\n")
    sys.exit(3)
problems = []
for path, info in plan["files"].items():
    if path == "VERSION":
        continue  # Version advances only after every file and migration succeeds.
    action = info["action"]
    expected = reviewed.get(path, {}).get("operator_sha256")
    op_p  = os.path.join(wd, path)
    new_p = os.path.join(new_wd, path)
    if action == "no-op":
        continue
    elif action == "clean-update":
        copy(new_p, op_p, expected)
    elif action == "add":
        copy(new_p, op_p, expected)
    elif action == "conflict":
        r = res.get(path)
        if not r:
            problems.append(f"missing resolution for {path}")
            continue
        kind = r.get("resolution")
        if kind == "mine":
            pass
        elif kind == "theirs":
            copy(new_p, op_p, expected)
        elif kind == "merged":
            mp = r.get("merged_path")
            if not mp or not os.path.isfile(mp):
                problems.append(f"merged_path missing for {path}")
                continue
            copy(mp, op_p, expected)
        else:
            problems.append(f"unknown resolution '{kind}' for {path}")
    elif action == "removed-in-release":
        # Operator's copy stays. Defaults entry will be cleaned in finalize.
        pass
    elif action == "operator-deleted-removed":
        pass
    elif action == "operator-deleted-changed":
        # File deleted by operator, changed in release. Treat as conflict;
        # default to keeping deletion unless operator opted in via resolutions.
        r = res.get(path, {})
        if r.get("resolution") == "theirs":
            copy(new_p, op_p, expected)
    elif action == "operator-only":
        pass

if problems:
    sys.stderr.write("APPLY ERRORS:\n  " + "\n  ".join(problems) + "\n")
    sys.exit(2)
PYEOF
  if (( rc == 3 )); then
    fail "File apply stopped; preserve current files and backup $backup_id. Resolve the partial installation before retrying. No blanket restore was performed because another writer may have changed a target."
  fi
  if (( rc != 0 )); then
    fail "Apply aborted; backup $backup_id preserved. Version was not advanced. Reconcile changed files before per-file recovery; do not blanket-restore over concurrent edits."
  fi

  # 4. Run schema migrations from the staged release.
  if [[ -f "$staging/manifest.json" ]]; then
    local migrations_csv
    migrations_csv=$(python3 - "$staging/manifest.json" "$prior_version" "$new_version" <<'PYMIG'
import json,re,sys
manifest,prior,new = sys.argv[1:]
def version(value):
    return tuple(int(x) for x in value.split('.'))
selected=[]
for name in json.load(open(manifest)).get('migrations',[]):
    if '/' in name or '\\' in name or name in ('.','..'):
        raise ValueError('Migration must be a filename within staging/migrations')
    match=re.match(r'^\d+\.\d+\.\d+-to-(\d+\.\d+\.\d+)-',name)
    if match and not (version(prior) < version(match[1]) <= version(new)):
        continue
    selected.append(name)
print('\n'.join(selected))
PYMIG
)
    while IFS= read -r script; do
      [[ -z "$script" ]] && continue
      local script_path="$staging/migrations/$script"
      [[ -f "$script_path" ]] || fail "Migration script not found: migrations/$script"
      local migration_hash receipt_dir
      migration_hash="$(sha256_file "$script_path")"
      receipt_dir="$staging/completed-migrations"
      if [[ -f "$receipt_dir/$script" && "$(cat "$receipt_dir/$script")" == "$migration_hash" ]]; then
        log "Migration already completed in this staged attempt: $script"
        continue
      fi
      log "Running migration: $script"
      WORKDESK_VAULT="$VAULT" WORKDESK_WD="$WD" bash "$script_path" \
        || { log "Migration failed: $script. Preserving partial state..."
             fail "Migration aborted; backup $backup_id preserved and version not advanced. Reconcile partial state before per-file recovery." ; }
      mkdir -p "$receipt_dir"
      printf '%s\n' "$migration_hash" > "$receipt_dir/$script"
    done <<< "$migrations_csv"
  fi

  # 5. Atomic finalize: stage new defaults, swap, bump VERSION last.
  local new_defaults_tmp="$TMP_BASE/new-defaults"
  rm -rf "$new_defaults_tmp"
  rsync -a --exclude='defaults' --exclude='state/migrate-*' --exclude='snapshots' \
        "$new_wd/" "$new_defaults_tmp/" \
    || fail "Failed to stage new defaults snapshot."

  local old_defaults="$DEFAULTS.old-$backup_id"
  if [[ -d "$DEFAULTS" ]]; then
    mv "$DEFAULTS" "$old_defaults" || fail "Failed to move old defaults aside."
  fi
  mv "$new_defaults_tmp" "$DEFAULTS" || {
    log "Failed to swap defaults; rolling back."
    [[ -d "$old_defaults" ]] && mv "$old_defaults" "$DEFAULTS"
    fail "Finalize aborted."
  }
  rm -rf "$old_defaults"

  printf '%s\n' "$new_version" > "$VERSION_FILE"

  rm -rf "$TMP_BASE"

  cat <<EOF
{"status":"applied","new_version":$(json_escape "$new_version"),"backup_id":$(json_escape "$backup_id")}
EOF
}

# ---- restore ------------------------------------------------------------------

cmd_restore() {
  local id="${1:-}"
  [[ -n "$id" ]] || fail "restore: backup id required."
  local backup="$BACKUP_BASE/$id"
  [[ -d "$backup" ]] || fail "restore: backup not found: $backup"
  require rsync
  # --checksum: rsync's default size+mtime heuristic can miss content changes
  # when files have identical sizes and near-identical mtimes (common during
  # apply→restore cycles). Force content-based comparison.
  rsync -a --checksum --delete "$backup/" "$WD/" || fail "Restore failed."
  log "Restored config/ from $backup"
}

# ---- dispatch -----------------------------------------------------------------

cmd_source_runtime() {
  [[ $# -le 1 && ( $# -eq 0 || "$1" == "--apply" ) ]] || fail "source-runtime accepts only --apply"
  require python3
  python3 - "${1:-}" <<'PYRUNTIME'
import json, os, platform, subprocess, sys
from pathlib import Path

apply = sys.argv[1] == '--apply'
version = '6.0.3'
base = Path.home() / '.local/share/workdesk/runtimes'
target = base / ('source-processing-py' + str(sys.version_info.major) + '.' + str(sys.version_info.minor) + '-yaml' + version)
for path in [target, *target.parents]:
    if path.is_symlink():
        raise SystemExit('Runtime path contains a symlink; reconcile before installation')
python = target / 'bin/python3'
receipt = target / 'workdesk-runtime.json'
status = 'create'
if target.exists():
    if not python.is_file() or not receipt.is_file():
        raise SystemExit('Existing runtime is incomplete; preserve it and reconcile before retrying')
    saved = json.loads(receipt.read_text())
    probe = subprocess.run([str(python), '-c', 'import yaml; print(yaml.__version__)'], capture_output=True, text=True)
    if probe.returncode or probe.stdout.strip() != version or saved.get('package') != 'PyYAML==' + version:
        raise SystemExit('Existing runtime does not match its required dependency; reconcile without overwriting')
    status = 'ready'
plan = {'status': status, 'python': str(python), 'package': 'PyYAML==' + version,
        'scope': 'host-local isolated runtime; no vault or global agent config changes'}
if apply and status == 'create':
    base.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    subprocess.run([str(Path(sys.executable).resolve()), '-m', 'venv', '--symlinks', str(target)], check=True)
    subprocess.run([str(python), '-m', 'pip', '--isolated', 'install', '--disable-pip-version-check',
                    '--no-cache-dir', '--only-binary=:all:', 'PyYAML==' + version], check=True)
    subprocess.run([str(python), '-c', 'import yaml; assert yaml.__version__ == "6.0.3"'], check=True)
    saved = dict(plan, status='ready', base_python=sys.version, platform=platform.platform())
    receipt.write_text(json.dumps(saved, indent=2) + '\n')
    os.chmod(receipt, 0o600)
    plan['status'] = 'ready'
print(json.dumps(plan))
PYRUNTIME
}

case "${1:-}" in
  source-runtime) shift; cmd_source_runtime "$@" ;;
  private-overlay) shift; python3 "$(dirname "${BASH_SOURCE[0]}")/apply-private-config.py" --vault "$VAULT" "$@" ;;
  check)   shift; cmd_check   "$@" ;;
  apply)   shift; cmd_apply   "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  ""|-h|--help)
    cat <<EOF
migrate.sh — WorkDesk OS update engine.

Usage:
  migrate.sh source-runtime [--apply]
      Review or prepare the isolated host-local Python runtime for source completion.
      Existing mismatched or partial environments are preserved for reconciliation.

  migrate.sh check
      Fetch latest release, verify SHA256, extract to staging, output plan JSON.

  migrate.sh apply <staging-dir> <resolutions.json>
      Backup, apply files (with conflict resolutions), run schema migrations,
      finalize atomically. <staging-dir> is the path emitted by 'check'.

  migrate.sh private-overlay <package-dir> [--apply]
      Review/apply enumerated private config without changing product defaults or VERSION.
  migrate.sh private-overlay <receipt.json> --restore-file <path> --apply
      Restore one existing-file snapshot after checking the installed hash.

  migrate.sh restore <backup-id>
      Roll back config/ to a prior backup at \$VAULT/.workdesk-backups/<id>/.
EOF
    ;;
  *) fail "Unknown subcommand: $1. Try --help." ;;
esac
