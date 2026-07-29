# Demo Decision Register

<a id="old-root"></a>
### Use the former root convention

- Decision: Use the former root convention.
- Provenance: explicit

<a id="current-root"></a>
### Use the current root convention

- Decision: Use the current root convention.
- Provenance: explicit
- Supersedes: old-root
- Confidence: high
- Review: approved

<a id="ui-scope"></a>
### Keep UI behavior local

- Decision: UI code must use the product-owned authentication interface.
- Provenance: explicit
- Derivation: condensed
- Affected Layers: ui

<a id="unmapped-scope"></a>
### Keep storage behavior local

- Decision: Storage code must use the storage service interface.
- Provenance: explicit
- Affected Layers: storage

<a id="inferred-api"></a>
### A possible API helper

- Decision: A pre-session helper API may be useful.
- Provenance: inferred
- Confidence: medium

<a id="assumed-cache"></a>
### A possible cache

- Decision: A local cache is probably required.
- Provenance: assumed
- Review: needs-confirmation

<a id="mixed-synthesis"></a>
### Combine explicit and inferred context

- Decision: The current convention and helper may need a shared adapter.
- Provenance: inferred
- Derivation: synthesized
- Sources: current-root, inferred-api

<a id="nested-synthesis"></a>
### Reuse a synthesized conclusion

- Decision: A second adapter may reuse the first synthesized conclusion.
- Provenance: inferred
- Derivation: synthesized
- Sources: mixed-synthesis, current-root
