#!/usr/bin/env bash
# resolve-secret.sh — resolve one runtime secret without source-time side effects.
#
# wd_resolve_secret <SECRET_NAME>
# Prints the secret value. Resolution: env var of same name, then Infisical
# using the project from operator-config.sh. Returns non-zero with an actionable
# message on stderr if neither source is available.

# shellcheck disable=SC1091,SC2034
wd_resolve_secret() {
  local secret_name="${1:-}"
  local value
  if [[ -z "$secret_name" ]]; then
    printf 'wd_resolve_secret requires a secret name\n' >&2
    return 2
  fi
  value="${!secret_name:-}"
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return 0
  fi
  local lib_dir OPERATOR_CONFIG_LENIENT=1
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  source "$lib_dir/operator-config.sh" 2>/dev/null || true

  if [[ -n "${INFISICAL_PERSONAL_PROJECT_ID:-}" ]] && command -v infisical >/dev/null 2>&1; then
    if value="$(INFISICAL_DISABLE_UPDATE_CHECK=true infisical secrets get "$secret_name" \
      --projectId="$INFISICAL_PERSONAL_PROJECT_ID" --env=prod --plain \
      </dev/null 2>/dev/null)"; then
      if [[ -n "$value" ]]; then
        printf '%s' "$value"
        return 0
      fi
    fi
  fi

  printf 'No %s available. Either export %s, or configure Infisical (bash config/scripts/bootstrap-infisical.sh) and store the key there.\n' \
    "$secret_name" "$secret_name" >&2
  return 1
}
