# GWS (Google Workspace CLI) — Tool Reference

`gws` is a Google Workspace CLI covering Gmail, Calendar, Drive, Docs, Sheets, and admin endpoints. Authenticates via OAuth2 against a per-operator Google Cloud OAuth app whose credentials live in Infisical.

This tool layer rides on top of the Infisical foundation (`config/rules/tools/infisical.md`). Don't attempt to set up gws on a machine that hasn't run `bootstrap-infisical.sh`.

## Access Method

### Explicit account routing for API commands

When the shell wrapper below is sourced, `gws ... --account EMAIL` selects an
explicit account using the host-local `gws-accounts.json` under
`WORKDESK_STATE_HOME` (default `~/.local/state/workdesk`). The wrapper also
accepts `WORKDESK_GWS_ACCOUNT` or the legacy `GOOGLE_WORKSPACE_CLI_ACCOUNT`
environment selector. An explicit flag wins over inherited selectors;
duplicate flags fail. This selection is a wrapper feature, not a promise
that the native binary honors the same flag.

The map must contain an exact email key with either
`{"mode":"legacy-account"}` for verified gws 0.4.1, or
`{"mode":"config-dir","config_dir":"/absolute/existing/store"}` for
verified gws 0.22.5. Keep this machine-specific map outside the synced vault.
The wrapper removes competing credential selectors and verifies the Drive
authenticated email before the requested API command. Unknown versions,
missing routes and mismatched identities fail without running that command.
The original arguments, output and exit status are preserved after routing.

Calls without an account selector retain native default-account behavior.
Jobs that launch the binary directly do not inherit this shell function;
they must use the verified environment from `scripts/lib/gws_account.py`
or their explicitly reviewed account adapter. Account verification does not
authorize sending or mutation; the operator's operation-approval rules still
apply.

Authentication is separate: the login branch below retains the legacy flow.
Do not use it as a modern multi-store OAuth setup procedure. Modern stores
need their reviewed per-account setup, and the legacy token-push script does
not establish backup coverage for modern keyring-backed credentials.

CLI binary: `gws`, installed by `setup-gws.sh` via `brew install googleworkspace-cli` (preferred) or `npm install -g @googleworkspace/cli` (fallback). Both methods come from the official repo at https://github.com/googleworkspace/cli — do NOT `brew install gws`, which installs an unrelated git-workspaces tool of the same name.

A shell wrapper at `config/shell/gws-env.sh` makes `gws auth login` pull the OAuth-app `client_id`/`client_secret` from Infisical at the moment of login — no env vars in your shell, no copy-pasting. Source from `~/.zshrc`:

```bash
source /path/to/Workdesk-OS/config/shell/gws-env.sh
```

The wrapper is a pass-through for every command except `gws auth login`, which it intercepts to inject the OAuth-app credentials (requires an active `infisical login` user session).

## Setup

Run once per machine, after `bootstrap-infisical.sh`:

```bash
bash config/scripts/setup-gws.sh
```

That script:
1. Verifies the Infisical user session works.
2. Installs gws if missing (`brew install googleworkspace-cli`, falling back to `npm install -g @googleworkspace/cli`).
3. Ensures `~/Library/Application Support/gws` is a real directory (repairing the legacy ramdisk symlink if one is found).
4. **If Infisical has a synced copy of your gws state** — restores it: encryption key, `accounts.json`, every account's encrypted refresh token (one `PERSONAL_GWS_CREDENTIALS_<ORG>_ENC_B64` per account listed in `accounts.json`), and a `client_secret.json` rebuilt from the primary org's `PERSONAL_GOOGLE_WORKSPACE_<ORG>_*` keys. No browser OAuth needed.
5. **If not** — walks you through `gws auth login` (browser), then a re-run of the script syncs the fresh state to Infisical.

Idempotent — re-run any time to repair drift or finish a two-pass first-auth.

## Where state lives

**The location and layout depend on the gws CLI version.**

**Pre-0.22** keeps OAuth state in `~/Library/Application Support/gws/` — a normal directory on disk (FileVault covers encryption at rest; sensitive files are `chmod 600`). Files in that directory:

| File | What | Synced copy in Infisical |
|---|---|---|
| `client_secret.json` | OAuth app creds used at login time (client_id/secret/project_id) | Rebuilt on restore from the primary org's `PERSONAL_GOOGLE_WORKSPACE_<ORG>_*` keys |
| `.encryption_key` | Per-install random key that wraps the refresh tokens | `PERSONAL_GWS_ENCRYPTION_KEY` |
| `credentials.<b64-email>.enc` | Encrypted OAuth refresh token + its OAuth client (self-contained per account) | `PERSONAL_GWS_CREDENTIALS_<ORG>_ENC_B64` (stored base64; `<ORG>` = uppercase first label of the email domain, e.g. `EXAMPLE`, `CLIENTCO`) |
| `accounts.json` | Registered accounts list | `PERSONAL_GWS_ACCOUNTS_JSON` |
| `token_cache.<b64-email>.json` | Short-lived access token (~1h) | Not synced — regenerated by `gws` from the refresh token |

**gws 0.22+** moved state to `~/.config/gws/` (XDG) with a different model: a single `credentials.enc` for the whole install (synced to Infisical as `PERSONAL_GWS_CREDENTIALS_<ORG>_ENC_B64`, `<ORG>` derived from the operator email domain), the encryption key in the macOS Keychain (service `gws-cli`, never synced — it is machine-bound), and no `accounts.json`. Consequence: on 0.22+ a new machine cannot restore auth from Infisical alone — first auth is always `gws auth login` there; the push script then keeps Infisical's copy of `credentials.enc` current for same-machine recovery. `config/scripts/lib/gws-layout.sh` is the shared layout detector every script in this layer uses.

Infisical is the backup/restore layer, not the runtime dependency: day-to-day `gws` calls never touch Infisical. Only login interception, push-after-re-auth, and restore-on-new-machine do.

## Common Commands

| Command | What it does | Example |
|---|---|---|
| `gws auth status` | Show current auth state | `gws auth status` |
| `gws auth login --account EMAIL` | OAuth login flow (opens browser) | `gws auth login --account you@example.com` |
| `gws <svc> <res> <method> --params '...'` | Generic Google API call | `gws calendar events list --params '{"calendarId":"primary"}'` |
| `gws schema <svc>.<res>.<method>` | Inspect API surface | `gws schema gmail.users.messages.list` |

### Creating drafts / sending with a request body or attachment

Generic write calls (`drafts create`, `messages send`, `messages insert`) need the request body and any media passed through the **right flags** — `--params` is query-string only:

- `--json '<JSON>'` — the request **body** (e.g. the Draft resource: `{"message":{"raw":"<base64url-MIME>","threadId":"..."}}`).
- `--upload <path>` — a local file to attach as **media** (multipart upload).
- `--params '{"userId":"me"}'` — URL/query params only.

Putting the body in `--params` sends an empty body and Google returns **`411 Length Required`**. To draft a reply with an attachment: build a multipart MIME message (text part + attachment part) in code, base64url-encode it as `raw`, set `threadId` (plus `In-Reply-To`/`References` headers) for threading, and pass the whole Draft resource via `--json`. Drafting is always allowed; the *send* stays gated on the operator's code phrase (see the outbound-communications rule).

### Large bodies and array params (limits learned 2026-07-24, Gmail message migration)

- **`--json` bodies are argv-limited** (~1 MB total on macOS). A Gmail `messages.import`/`insert` whose base64url `raw` exceeds that silently fails (empty output — the exec hits `E2BIG`). For big messages, hit the upload endpoint directly with `curl`: `POST https://gmail.googleapis.com/upload/gmail/v1/users/me/messages/import?uploadType=media&internalDateSource=dateHeader` with `Content-Type: message/rfc822` and the decoded `.eml` as the body, then apply labels with a `messages.modify` call. Don't use gws `--upload` for this — it sends `application/octet-stream`, which Gmail rejects.
- **Array-valued `--params` fields don't filter reliably** (`labelIds`, `metadataHeaders` were both no-ops). Use the string equivalents instead: `q: "label:actions"` for label filtering, `format: "metadata"` without `metadataHeaders` (returns all headers).

### Recovering the CLI's access token

When a raw HTTP call needs gws's bearer token (e.g. the upload endpoint above): `gws auth export` does NOT work — it has the same non-account-specific-path bug as `gws auth status` and returns "No encrypted credentials found" even when calls authenticate fine. The reliable path (verified 2026-07-24): make any cheap gws call to freshen the cache, then decrypt `token_cache.<b64-email>.json` yourself — **AES-256-GCM**, key = base64-decoded `.encryption_key`, 12-byte nonce prefixed to the blob, no AAD. The decrypted JSON maps scope-strings to token entries with a `token` field. (Python: `cryptography.hazmat.primitives.ciphers.aead.AESGCM(key).decrypt(blob[:12], blob[12:], None)`.)

## When to re-run `gws-push-tokens-to-infisical.sh`

The script at `config/scripts/gws-push-tokens-to-infisical.sh` mirrors the local refresh-token state into Infisical (via your `infisical login` session). Re-run when:

- You re-authenticate (`gws auth login`) — the refresh token changes.
- You add a new Google account to gws — `accounts.json` and `credentials.<new-email>.enc` need pushing. The push script sweeps every `credentials.*.enc` in the state dir automatically, deriving each account's key suffix from its email domain (as of 2026-07-24; multi-account is fully wired).
- You rotate the encryption key (rare; happens if you `gws auth logout --all` and re-login).

If you skip the push, local gws keeps working — but Infisical's synced copy goes stale, and the next machine (or state wipe) that restores from it gets a dead token and has to do browser OAuth anyway.

## Trust boundary

`PERSONAL_GWS_ENCRYPTION_KEY` + any `PERSONAL_GWS_CREDENTIALS_<ORG>_ENC_B64` together are equivalent to plaintext access to that account's Gmail/Drive/Calendar. Anyone with read access to these two Infisical keys can impersonate your Workspace identity. Scope your personal Infisical project accordingly — it should be readable by you alone, never shared with contractors or client projects.

## Known Limitations

### Transcript importer account selection

`pull-gemini-transcripts.sh` also requires an explicit account and uses the
same verified routing component. Its separate checkpoint and log directory is
`${WORKDESK_STATE_HOME:-$HOME/.local/state/workdesk}/<vault-hash>/gemini-transcripts/<account-hash>/`.
The old synced `config/state/pull-gemini.json` is preserved but never adopted
automatically. Calendar pagination completes before source publication; failed
pages, malformed page envelopes and repeated page tokens preserve prior success.
Single-document recovery requires `--doc-id ID --force` and never advances
enumeration progress. `--force` does not authorize replacing or duplicating an
existing source in intake or the transcript archive: reconcile that source
explicitly instead. Publication uses an atomic no-replace operation; an occupied
destination is preserved and the staged candidate retained for review.
Source identity is read only from frontmatter, including ordinary quoted IDs;
mentions in transcript prose do not count. Duplicate ID records, unreadable
inventories, symlinks or ambiguous identity syntax require reconciliation.
Dry runs never change the checkpoint, including failures.
This account boundary does not certify source publication, nested document
coverage or the completeness of calendar attachment discovery.

`pull-google-transcripts.sh` requires `--account you@example.com` or
`WORKDESK_GWS_ACCOUNT`, plus the host-local route documented above. Set
`WORKDESK_GWS_BIN` to the absolute executable when the scheduler's PATH does not
include it. Every direct API call verifies that route and the authenticated
principal through `config/scripts/lib/gws_account.py`; shell startup files are
not required. This verifies identity, not approval for an outbound operation.

Checkpoints and logs live under
`${WORKDESK_STATE_HOME:-$HOME/.local/state/workdesk}/<vault-hash>/google-transcripts/<account-hash>/`.
The checkpoint also records its account and rejects mismatches. Unattributed
older checkpoints remain untouched and are not automatic fallbacks. Before
cutover, reconcile the old checkpoint's account from execution evidence and
choose an explicit initial lookback for each account. A new account defaults
to one day; that is not historical coverage. A lookback above seven days needs
`--backfill`. Keep one active scheduled owner and serialize runs for a vault.

All listing pages must validate before any document export. A successful
enumeration records its start time as the next watermark, with a one-second
overlap on catch-up. `--dry-run` never changes checkpoints. A single-file pull
requires both `--file-id` and `--force`; it never advances the enumeration
watermark, and an existing note is preserved for explicit reconciliation.

- **Pushes need a live Infisical session.** If your `infisical login` session has expired, push scripts log `FAILED to push` to `system/log/gws-push.log` and keep going — local gws is unaffected. Re-run `infisical login`, then the push.
- **One OAuth app per Workspace org.** Each account's encrypted credential is self-contained (carries its own client_id/secret), so accounts from different orgs coexist in one gws install. `client_secret.json` and the wrapper's env-var injection only matter at `gws auth login` time — the wrapper picks the org's app from the `--account` email domain.
- **Suffix derivation is domain-based.** `alex@example.com` → `EXAMPLE`, `alex@client-co.example` → `CLIENTCO`. Two accounts on the *same* domain would collide — add a disambiguating scheme before that ever happens.

## Common Mistakes

- **Re-authenticating without re-running `gws-push-tokens-to-infisical.sh`.** Local state gets the new token, but Infisical still has the old one — a future restore hands back a dead token. Always pair `gws auth login` with the push script.
- **Setting `GOOGLE_WORKSPACE_CLI_CLIENT_ID` permanently in `~/.zshrc` instead of via the wrapper.** Puts the value in plaintext in your shell history and dotfiles. Use the wrapper at `config/shell/gws-env.sh`.
- **Treating `~/Library/Application Support/gws/` as disposable.** It's the only live copy of your auth state; Infisical holds a synced backup only as current as the last successful push. Check `system/log/gws-push.log` before wiping it.
- **Passing a request body in `--params`.** `--params` is query-only; the body goes in `--json` (and media in `--upload`). Body-in-`--params` returns `411 Length Required`. Bit us building a Gmail draft-with-attachment on 2026-07-21.
- **Trusting `gws auth status`/`gws auth export` as proof of auth.** They can report `storage: none` / "no encrypted credentials found" while ordinary calls authenticate fine, because the credential is stored per-account (`credentials.<b64email>.enc`) and those subcommands look at the non-account-specific path. Confirm with a real read (`gws calendar +agenda --today`) before concluding auth is broken.
- **Running `gws auth login --help`.** The binary ignores `--help` after `login` and starts a REAL login flow — it opens a browser consent URL and blocks on a localhost callback. In a script or agent context it hangs until killed. Use `gws auth --help` for the flag reference. (Bit us 2026-07-24.)

## Detection clause

Surface proactively when:

- The operator references something only Google can answer (calendar event, email, Drive doc) and `gws auth status` would resolve it — propose using gws instead of guessing.
- The operator says they re-authenticated gws but didn't push tokens — remind them to run `gws-push-tokens-to-infisical.sh`.
- A new Google account needs adding — store its org's OAuth app as `PERSONAL_GOOGLE_WORKSPACE_<ORG>_CLIENT_ID/_CLIENT_SECRET/_PROJECT_ID`, log in with `gws auth login --account <email>` via the wrapper, then run the push script (it sweeps all accounts automatically).
- `gws auth status` shows "not authenticated" on a machine that used to work — check whether the state dir (`~/Library/Application Support/gws/` pre-0.22, `~/.config/gws/` on 0.22+) still has its state files; if missing, run `setup-gws.sh` (restore-from-Infisical works on the pre-0.22 layout; 0.22+ needs a fresh `gws auth login`).
- `~/Library/Application Support/gws` turns out to be a symlink to `/Volumes/wd-ramdisk/` — that's the retired pre-2026-07 layout; run `setup-gws.sh` to repair it.

## Sources

- gws CLI: https://github.com/googleworkspace/cli (`brew install googleworkspace-cli` or `npm install -g @googleworkspace/cli`)
- Foundation rule: [[infisical]]
- Architecture note: until 2026-07-06 this layer used the Infisical Agent + RAM-disk pattern (machine identity, boot-time render, `~/Library/Application Support/gws` symlinked to `/Volumes/wd-ramdisk/gws`). Retired in favor of on-disk state + user-session push/restore.
