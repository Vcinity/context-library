# Context Library Plugin deployment

The Plugin is distributed from the Context Library monorepo. It is an
independently installable, read-only integration, but it shares the monorepo
product version with Core, Maintainer, and Manager.

The canonical Context Library remains a separate checkout. A Plugin deployment
must not copy canonical decision data into the monorepo checkout, the Plugin
installation, or a consumer repository.

## Deployment contents

A Plugin-only deployment needs only these paths from a tagged monorepo release:

```text
.agents/plugins/marketplace.json
plugins/context-library/
scripts/install_plugin.py
```

The sparse checkout does not need Python dependencies, Poetry, Node.js, the
Manager, the Maintainer service, or the frontend. The bundled MCP server uses
the host's `python3` and the read-only Plugin runtime.

The MCP manifest uses Codex's supported plugin-relative launch contract:
`cwd: "."` is resolved by Codex against the installed Plugin root, and the
server argument remains `./mcp/context_library_server.py`. It does not depend
on the caller's working directory or `${PLUGIN_ROOT}` interpolation. After
installation or cache refresh, start a new Codex thread and verify that the
Plugin MCP initializes and lists its tools. If the host reports a launch
failure, inspect the resolved Plugin path and reinstall the staged
marketplace; do not interpret a process-spawn error as a canonical-library
runtime condition.

Keep the deployment checkout separate from the canonical library and from any
consumer workspace. The projection safety checks reject overlapping roots.

## Sparse checkout

Use an immutable release tag, not a moving branch. Set the repository URL and
release to values approved for the deployment:

```sh
REPOSITORY_URL="<context-library-repository-url>"
RELEASE=v0.4.6
SOURCE_ROOT=/opt/context-library-plugin-source

git clone \
  --filter=blob:none \
  --no-checkout \
  --depth 1 \
  --branch "$RELEASE" \
  "$REPOSITORY_URL" \
  "$SOURCE_ROOT"

git -C "$SOURCE_ROOT" sparse-checkout init --no-cone
git -C "$SOURCE_ROOT" sparse-checkout set \
  .agents/plugins/marketplace.json \
  plugins/context-library \
  scripts/install_plugin.py
git -C "$SOURCE_ROOT" checkout --detach "$RELEASE"
```

For an existing sparse checkout, fetch the release tag and select it without
expanding the checkout:

```sh
git -C "$SOURCE_ROOT" fetch --depth 1 origin tag "$RELEASE"
git -C "$SOURCE_ROOT" checkout --detach "$RELEASE"
```

The checkout should contain the marketplace manifest and Plugin directory but
not the rest of the monorepo:

```sh
test -f "$SOURCE_ROOT/.agents/plugins/marketplace.json"
test -f "$SOURCE_ROOT/plugins/context-library/.codex-plugin/plugin.json"
test -f "$SOURCE_ROOT/scripts/install_plugin.py"
test ! -e "$SOURCE_ROOT/src"
test ! -e "$SOURCE_ROOT/frontend"
git -C "$SOURCE_ROOT" describe --exact-match --tags HEAD
```

Confirm the Plugin version before installing it:

```sh
python3 - "$SOURCE_ROOT/plugins/context-library/.codex-plugin/plugin.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
print(manifest["name"], manifest["version"])
PY
```

The reported version must match the Manager release deployed from the same
monorepo tag.

## Stage, configure, and install

Make the separately governed canonical checkout available to the user or
service that launches Codex. Prefer a read-only mount or equivalent filesystem
permissions. Install to a new explicit deployment destination:

```sh
MARKETPLACE_ROOT="/opt/context-library-plugin-${RELEASE}"

python3 "$SOURCE_ROOT/scripts/install_plugin.py" \
  --destination "$MARKETPLACE_ROOT" \
  --library-root "${CANONICAL_LIBRARY_ROOT}" \
  --marketplace-name "${MARKETPLACE_NAME}"
```

The command copies only the marketplace manifest and Plugin into
`MARKETPLACE_ROOT`, creates `runtime-config.json` in that staged Plugin, and
then registers and installs it with Codex. It does not change `SOURCE_ROOT`.
The destination must not already exist; use a release-specific destination so
upgrades and rollbacks remain explicit. Omit `--marketplace-name` to retain the
public `context-library` marketplace name.

If the marketplace was staged manually, run the required post-install
configuration step before registering or reinstalling the Plugin:

```sh
python3 "$MARKETPLACE_ROOT/plugins/context-library/scripts/configure.py" \
  --library-root "$CANONICAL_LIBRARY_ROOT" \
  --output "$MARKETPLACE_ROOT/plugins/context-library/runtime-config.json"
test -r "$MARKETPLACE_ROOT/plugins/context-library/runtime-config.json"
```

This file configures `CONTEXT_LIBRARY_ROOT` for the bundled MCP server and
projection hook. A marketplace install without this step can fail with
`context library root is not configured`. After configuring an already
installed Plugin, reinstall it from the marketplace and start a new Codex
thread so the updated runtime configuration is loaded.

### Runtime diagnostics

Call the bundled `get_library_status` MCP tool first when diagnosing a fresh
or newly staged install. It is a status boundary, not an exception boundary:
it never raises, and instead reports a machine-readable `condition` with
safe, actionable `remediation`. Every other MCP tool (`list_project_packs`,
`read_project_artifact`, `search_decisions`, `get_task_context`,
`read_decision_audit`) remains exception-based and fails closed with the
same classification when the runtime is not usable.

| `condition`          | Meaning                                                            | Remediation                                                        |
| --------------------- | ------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `healthy`             | Configured root is resolvable, exists, and is readable.            | none                                                                  |
| `missing_config`      | No `runtime-config.json` and no `CONTEXT_LIBRARY_ROOT` override.    | Run `scripts/configure.py --library-root <path>` or set the override. |
| `malformed_config`    | `runtime-config.json` exists but fails schema validation.           | Regenerate it with `scripts/configure.py`.                            |
| `unreadable_config`   | `runtime-config.json` exists but cannot be read (permissions).      | Fix file permissions or regenerate it.                                |
| `missing_root`        | The configured library root path does not exist.                    | Verify the mount/path, then reconfigure if it moved.                  |
| `unreadable_root`     | The configured library root exists but is not readable.              | Grant the Plugin process read/traverse access to the root.            |

The trusted session-start hook performs this preflight before resolving
context policy. For any condition other than `healthy`, it emits a structured
blocking result (`status=blocked`, `disposition=stop`, and the condition) and
exits with status `2`. Fix the configuration/access, explicitly disable the
context policy, or uninstall the Plugin before continuing. This stop applies
even when a broken configuration declares `disabled`; a successfully
preflighted disabled policy remains silent.

`CONTEXT_LIBRARY_ROOT` always takes precedence over the bundled
`runtime-config.json`, including when the bundled file is malformed: an
active environment override is classified on its own, never blocked by a
broken bundled file it does not need.

From a full monorepo checkout, the equivalent Make target is:

```sh
make plugin-install \
  PLUGIN_DEST="$MARKETPLACE_ROOT" \
  LIBRARY_ROOT="$CANONICAL_LIBRARY_ROOT" \
  MARKETPLACE_NAME="$MARKETPLACE_NAME"
```

Use `--stage-only`, or `STAGE_ONLY=1` with Make, to create the configured
marketplace without changing Codex registration. Optional `--project` and
`--context-requirement` arguments are also exposed as `PROJECT` and
`CONTEXT_REQUIREMENT` Make variables.

The generated configuration is read by both the bundled MCP server and
projection commands. Do not put credentials or write-capable Manager settings
in it or in the Plugin manifest.

For an explicitly configured ZIP artifact, embed that generated file with:

```sh
poetry run python scripts/build_plugin.py \
  --runtime-config "$MARKETPLACE_ROOT/plugins/context-library/runtime-config.json"
```

Normal public `make plugin-build` artifacts exclude runtime configuration.
The configured path must exist on the machine where Codex runs.

Environment variables remain higher-precedence runtime overrides:

- `CONTEXT_LIBRARY_ROOT`
- `CONTEXT_LIBRARY_PROJECT`
- `CONTEXT_LIBRARY_CONTEXT_REQUIREMENT`

If the consumer uses projection, select the project explicitly in the
consumer's committed `.context-library/config.json`, or set
`CONTEXT_LIBRARY_PROJECT` for a deliberate session override. An explicit
context policy is required before projection writes are allowed. Automatic
session-start projection includes only current explicit universal constraints;
use the read-only task-context MCP tool for scoped or conditional guidance
after an explicit task signal.

Codex installs and enables the Plugin from either the `/plugin` menu or the
installer above. Plugin command hooks require a separate trust decision: open
`/hooks`, review and trust the Context Library hook, and then start a new Codex
thread. A newly installed `SessionStart` hook cannot run retroactively in the
installation thread. Until it is trusted, Codex intentionally skips automatic
projection, while the skill and read-only MCP server remain available.

The marketplace registration supplies code distribution only. It does not
grant canonical write authority and does not replace the Manager review path.

## Shared read-only marketplace

When an administrator publishes one marketplace for several local Codex
users, the marketplace root must be readable and searchable by every user who
will install it. Codex reads `.agents/plugins/marketplace.json` before the
Plugin can be installed; an unreadable manifest produces a permission-denied
error.

The administrator should stage the release in a shared, read-only location,
ensure its directories are searchable and its files are readable by consumers,
run the post-install configuration step above, and then register that existing
root:

```sh
MARKETPLACE_ROOT=/srv/shared/context-library-plugin-v0.4.6

codex plugin marketplace add "$MARKETPLACE_ROOT"
codex plugin add context-library@context-library
```

Consumers should not run the staging installer into a path they do not own.
They need read access to the shared marketplace root and write access to their
own Codex plugin cache. If staging used a custom marketplace name, replace
the suffix in the `codex plugin add` command with that name.

## Verify the installation

From a consumer workspace with the Plugin runtime configuration present (or
`CONTEXT_LIBRARY_ROOT` set as an override):

```sh
python3 "$MARKETPLACE_ROOT/plugins/context-library/projection.py" check \
  --root "$PWD"
```

If the consumer has an explicit required or optional context policy and the
projection is missing or stale, run:

```sh
python3 "$MARKETPLACE_ROOT/plugins/context-library/projection.py" sync \
  --root "$PWD"
python3 "$MARKETPLACE_ROOT/plugins/context-library/projection.py" check \
  --root "$PWD"
```

Also verify that the MCP process can read the configured root using the
Plugin's read-only smoke test when the full monorepo is available, or call
the bundled `get_library_status` MCP tool directly and confirm its
`condition` is `healthy`. See "Runtime diagnostics" above for the full
condition and remediation contract. A deployed Plugin must never be used to
create, repair, migrate, or publish canonical files.

## Upgrade and rollback

Upgrade by selecting a newer tag in the same sparse source checkout, installing
to a new release-specific destination, and starting a new Codex thread:

```sh
RELEASE=v<new-version>
git -C "$SOURCE_ROOT" fetch --depth 1 origin tag "$RELEASE"
git -C "$SOURCE_ROOT" checkout --detach "$RELEASE"
MARKETPLACE_ROOT="/opt/context-library-plugin-${RELEASE}"
python3 "$SOURCE_ROOT/scripts/install_plugin.py" \
  --destination "$MARKETPLACE_ROOT" \
  --library-root "$CANONICAL_LIBRARY_ROOT"
```

If validation fails, register and reinstall the previously staged marketplace:

```sh
MARKETPLACE_ROOT=/opt/context-library-plugin-v<previous-version>
codex plugin marketplace add "$MARKETPLACE_ROOT"
codex plugin add context-library@context-library
```

Do not roll the Plugin to a different monorepo release from the Manager unless
the release documentation explicitly defines that compatibility. Keep the
canonical data checkout unchanged during Plugin upgrades.
