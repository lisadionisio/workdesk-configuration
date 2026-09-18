# gws-env.sh — wraps the gws CLI so `gws auth login` can read its OAuth-app
# client_id/client_secret from Infisical at the moment of login, without the
# operator ever copy-pasting them.
#
# Source from ~/.zshrc:
#   source /path/to/Workdesk-OS/config/shell/gws-env.sh
#
# Executables are discovered on PATH at call time, then in supported user-local
# and Homebrew locations. WORKDESK_GWS_BIN and WORKDESK_INFISICAL_BIN can select
# explicit absolute executables. These settings select programs, not accounts.
# Account routing and credential-store compatibility still depend on the CLI
# version; executable discovery does not migrate or verify credentials.
#
# Normal API calls with --account EMAIL, WORKDESK_GWS_ACCOUNT, or the legacy
# GOOGLE_WORKSPACE_CLI_ACCOUNT selector use config/scripts/lib/gws_account.py.
# It reads $WORKDESK_STATE_HOME/gws-accounts.json (default ~/.local/state/workdesk),
# chooses the configured store, and checks the authenticated identity first.
# Calls without a selector retain the native CLI's default behavior.
#
# Authentication is a separate flow below. It retains the legacy --account login
# behavior and must not be treated as a modern multi-store OAuth setup helper.
# Modern stores need their reviewed per-account login/setup procedure.
# Infisical is consulted only for auth login, never for routine API reads.
#
# Multi-org (2026-07-24): each Google Workspace org has its own OAuth app in
# Infisical, keyed by the uppercase first label of the account's email domain:
#   alex@example.com           → PERSONAL_GOOGLE_WORKSPACE_EXAMPLE_CLIENT_ID / _CLIENT_SECRET
#   alex@client-co.example     → PERSONAL_GOOGLE_WORKSPACE_CLIENTCO_CLIENT_ID / _CLIENT_SECRET
# The wrapper derives the suffix from the `--account` argument to `gws auth
# login` (falling back to the operator-profile `email:` when --account is
# omitted) and injects that org's client credentials.
#
# Credential backup is layout-specific. The legacy token-push script does not
# back up modern keyring-backed stores; verify recovery coverage separately.
#
# IMPORTANT: the function must be fully self-contained — no references to
# variables set at source time. Some environments (e.g. Claude Code shell
# snapshots) capture shell FUNCTIONS but not shell VARIABLES, so any state
# the function needs is baked in as literals at definition time (the vault
# root path) or resolved lazily at call time (the Infisical project ID).

# Find WORKDESK_ROOT from this file's location.
__wd_self="${BASH_SOURCE[0]:-${(%):-%x}}"
__wd_root="$(cd "$(dirname "${__wd_self}")/../.." && pwd)"
unset __wd_self

# Function template. Quoted heredoc → no expansion here; @@WD_ROOT@@ is
# substituted with the resolved vault root just before eval, so the defined
# function carries the path as a literal.
__wd_gws_def="$(cat <<'WD_GWS_EOF'
gws() {
  # Resolve executables at call time: shell snapshots may omit source-time state.
  # Explicit overrides must identify an absolute executable; never eval them.
  local __real="${WORKDESK_GWS_BIN:-}" __candidate
  if [[ -z "${__real}" ]]; then
    if [[ -n "${ZSH_VERSION:-}" ]]; then
      __real="$(whence -p gws 2>/dev/null)"
    else
      __real="$(type -P gws 2>/dev/null)"
    fi
    if [[ -z "${__real}" ]]; then
      for __candidate in "$HOME/.local/bin/gws" /opt/homebrew/bin/gws /usr/local/bin/gws; do
        if [[ -x "${__candidate}" && ! -d "${__candidate}" ]]; then __real="${__candidate}"; break; fi
      done
    fi
  fi
  if [[ "${__real}" != /* || ! -x "${__real}" || -d "${__real}" ]]; then
    echo "ERROR: gws executable unavailable; set WORKDESK_GWS_BIN to its absolute path." >&2
    return 127
  fi
  local __root=@@WD_ROOT@@
  if [[ "${1:-}" = "auth" && "${2:-}" = "login" ]]; then
    local __infisical="${WORKDESK_INFISICAL_BIN:-}"
    if [[ -z "${__infisical}" ]]; then
      if [[ -n "${ZSH_VERSION:-}" ]]; then
        __infisical="$(whence -p infisical 2>/dev/null)"
      else
        __infisical="$(type -P infisical 2>/dev/null)"
      fi
      if [[ -z "${__infisical}" ]]; then
        for __candidate in "$HOME/.local/bin/infisical" "$HOME/.homebrew/bin/infisical" /opt/homebrew/bin/infisical /usr/local/bin/infisical; do
          if [[ -x "${__candidate}" && ! -d "${__candidate}" ]]; then __infisical="${__candidate}"; break; fi
        done
      fi
    fi
    if [[ "${__infisical}" != /* || ! -x "${__infisical}" || -d "${__infisical}" ]]; then
      echo "ERROR: Infisical executable unavailable; set WORKDESK_INFISICAL_BIN to its absolute path." >&2
      return 127
    fi
    # Read the operator's Infisical project ID from operator-profile.md
    # frontmatter, lazily — auth login is rare, and call-time reads never
    # go stale or rely on source-time state.
    local __pid
    __pid="$(
      awk '
        /^---[[:space:]]*$/ { c++; if (c==1) {fm=1; next}; if (c==2) exit }
        fm && /^infisical-project-id:/ {
          sub(/^infisical-project-id:[[:space:]]*/, "")
          gsub(/^"|"$/, "")
          print; exit
        }
      ' "${__root}/config/operator-profile.md" 2>/dev/null
    )"
    if [[ -z "${__pid}" ]]; then
      echo "ERROR: infisical-project-id missing from operator-profile.md frontmatter." >&2
      echo "Run config/scripts/bootstrap-infisical.sh first." >&2
      return 1
    fi
    # Which org's OAuth app? Take the email from --account, falling back to
    # the operator-profile `email:`. Suffix = uppercase first label of the
    # email domain (alex@example.com → EXAMPLE).
    local __acct="" __prev="" __arg
    for __arg in "$@"; do
      if [[ "${__prev}" = "--account" ]]; then __acct="${__arg}"; break; fi
      __prev="${__arg}"
    done
    if [[ -z "${__acct}" ]]; then
      __acct="$(
        awk '
          /^---[[:space:]]*$/ { c++; if (c==1) {fm=1; next}; if (c==2) exit }
          fm && /^email:/ {
            sub(/^email:[[:space:]]*/, "")
            gsub(/^"|"$/, "")
            print; exit
          }
        ' "${__root}/config/operator-profile.md" 2>/dev/null
      )"
    fi
    if [[ "${__acct}" != *@*.* ]]; then
      echo "ERROR: cannot determine account email for gws auth login (pass --account EMAIL)." >&2
      return 1
    fi
    local __dom="${__acct#*@}"
    local __sfx
    __sfx="$(printf '%s' "${__dom%%.*}" | tr '[:lower:]' '[:upper:]' | tr -cd 'A-Z0-9_')"
    # Fall back to the unsuffixed key names when the domain-suffixed pair is
    # absent. Vaults provisioned before the 2026-07-24 multi-org change store
    # the OAuth app as PERSONAL_GOOGLE_WORKSPACE_CLIENT_ID/_CLIENT_SECRET, so
    # a suffixed-only lookup resolves empty and `gws auth login` fails with an
    # opaque OAuth error. (Field-reported and verified end-to-end 2026-08-03.)
    "${__infisical}" run \
      --projectId="${__pid}" \
      --env=prod \
      --command="GOOGLE_WORKSPACE_CLI_CLIENT_ID=\${PERSONAL_GOOGLE_WORKSPACE_${__sfx}_CLIENT_ID:-\$PERSONAL_GOOGLE_WORKSPACE_CLIENT_ID} GOOGLE_WORKSPACE_CLI_CLIENT_SECRET=\${PERSONAL_GOOGLE_WORKSPACE_${__sfx}_CLIENT_SECRET:-\$PERSONAL_GOOGLE_WORKSPACE_CLIENT_SECRET} $(printf '%q' "${__real}") $(printf '%q ' "$@")"
    return $?
  fi
  # Explicit account calls are routed through the host-local map, then checked
  # against the authenticated Drive principal before the requested API call.
  local __route_account="${WORKDESK_GWS_ACCOUNT:-${GOOGLE_WORKSPACE_CLI_ACCOUNT:-}}" __scan
  for __scan in "$@"; do
    if [[ "${__scan}" = "--account" || "${__scan}" = --account=* ]]; then __route_account="explicit"; fi
  done
  if [[ -n "${__route_account}" ]]; then
    "${WORKDESK_PYTHON:-python3}" "${__root}/config/scripts/lib/gws_account.py" "${__real}" "$@"
    return $?
  fi
  "${__real}" "$@"
}
WD_GWS_EOF
)"

__wd_root="$(printf '%q' "${__wd_root}")"
eval "${__wd_gws_def//@@WD_ROOT@@/${__wd_root}}"
unset __wd_gws_def __wd_root
