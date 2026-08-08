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
```

The sparse checkout does not need Python dependencies, Poetry, Node.js, the
Manager, the Maintainer service, or the frontend. The bundled MCP server uses
the host's `python3` and the read-only Plugin runtime.

Keep the deployment checkout separate from the canonical library and from any
consumer workspace. The projection safety checks reject overlapping roots.

## Sparse checkout

Use an immutable release tag, not a moving branch. Set the repository URL and
release to values approved for the deployment:

```sh
REPOSITORY_URL="<context-library-repository-url>"
RELEASE=v0.3.3
PLUGIN_ROOT=/opt/context-library-plugin

git clone \
  --filter=blob:none \
  --no-checkout \
  --depth 1 \
  --branch "$RELEASE" \
  "$REPOSITORY_URL" \
  "$PLUGIN_ROOT"

git -C "$PLUGIN_ROOT" sparse-checkout init --no-cone
git -C "$PLUGIN_ROOT" sparse-checkout set \
  .agents/plugins/marketplace.json \
  plugins/context-library
git -C "$PLUGIN_ROOT" checkout --detach "$RELEASE"
```

For an existing sparse checkout, fetch the release tag and select it without
expanding the checkout:

```sh
git -C "$PLUGIN_ROOT" fetch --depth 1 origin tag "$RELEASE"
git -C "$PLUGIN_ROOT" checkout --detach "$RELEASE"
```

The checkout should contain the marketplace manifest and Plugin directory but
not the rest of the monorepo:

```sh
test -f "$PLUGIN_ROOT/.agents/plugins/marketplace.json"
test -f "$PLUGIN_ROOT/plugins/context-library/.codex-plugin/plugin.json"
test ! -e "$PLUGIN_ROOT/src"
test ! -e "$PLUGIN_ROOT/frontend"
git -C "$PLUGIN_ROOT" describe --exact-match --tags HEAD
```

Confirm the Plugin version before installing it:

```sh
python3 - "$PLUGIN_ROOT/plugins/context-library/.codex-plugin/plugin.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    manifest = json.load(stream)
print(manifest["name"], manifest["version"])
PY
```

The reported version must match the Manager release deployed from the same
monorepo tag.

## Configure the canonical read root

Make the separately governed canonical checkout available to the user or
service that launches Codex. Prefer a read-only mount or equivalent filesystem
permissions. Configure it once in the Plugin source before installing:

```sh
python3 "$PLUGIN_ROOT/plugins/context-library/scripts/configure.py" \
  --library-root "${CANONICAL_LIBRARY_ROOT}"
```

The command creates an untracked `runtime-config.json` in the Plugin directory.
That file is part of the local marketplace source, so installs from the
`/plugin` menu and `codex plugin add` receive the same configuration. It is
read by both the bundled MCP server and projection commands. Do not put
credentials or write-capable Manager settings in it or in the Plugin manifest.

For an explicitly configured ZIP artifact, embed that generated file with:

```sh
poetry run python scripts/build_plugin.py \
  --runtime-config "$PLUGIN_ROOT/plugins/context-library/runtime-config.json"
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
context policy is required before projection writes are allowed.

## Register the marketplace and install

Register the sparse monorepo checkout as a local marketplace, then install the
Plugin entry declared by `.agents/plugins/marketplace.json`:

```sh
codex plugin marketplace add "$PLUGIN_ROOT"
codex plugin add context-library@context-library
```

Codex installs and enables the Plugin from either the `/plugin` menu or the
command above. Plugin command hooks require a separate trust decision: open
`/hooks`, review and trust the Context Library hook, and then start a new Codex
thread. A newly installed `SessionStart` hook cannot run retroactively in the
installation thread. Until it is trusted, Codex intentionally skips automatic
projection, while the skill and read-only MCP server remain available.

The marketplace registration supplies code distribution only. It does not
grant canonical write authority and does not replace the Manager review path.

## Verify the installation

From a consumer workspace with the Plugin runtime configuration present (or
`CONTEXT_LIBRARY_ROOT` set as an override):

```sh
python3 "$PLUGIN_ROOT/plugins/context-library/projection.py" check \
  --root "$PWD"
```

If the consumer has an explicit required or optional context policy and the
projection is missing or stale, run:

```sh
python3 "$PLUGIN_ROOT/plugins/context-library/projection.py" sync \
  --root "$PWD"
python3 "$PLUGIN_ROOT/plugins/context-library/projection.py" check \
  --root "$PWD"
```

Also verify that the MCP process can read the configured root using the
Plugin's read-only smoke test when the full monorepo is available. A deployed
Plugin must never be used to create, repair, migrate, or publish canonical
files.

## Upgrade and rollback

Upgrade by selecting a newer tag in the same sparse checkout, reinstalling the
marketplace entry, and starting a new Codex thread:

```sh
RELEASE=v<new-version>
git -C "$PLUGIN_ROOT" fetch --depth 1 origin tag "$RELEASE"
git -C "$PLUGIN_ROOT" checkout --detach "$RELEASE"
codex plugin marketplace add "$PLUGIN_ROOT"
codex plugin add context-library@context-library
```

If validation fails, roll back to the previously approved tag and reinstall:

```sh
git -C "$PLUGIN_ROOT" checkout --detach v<previous-version>
codex plugin marketplace add "$PLUGIN_ROOT"
codex plugin add context-library@context-library
```

Do not roll the Plugin to a different monorepo release from the Manager unless
the release documentation explicitly defines that compatibility. Keep the
canonical data checkout unchanged during Plugin upgrades.
