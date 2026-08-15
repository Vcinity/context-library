# Versioned Contracts

Core Pydantic models are the executable source of truth. Run:

```sh
make contracts
make contracts-check
```

Generated schemas under `schemas/` are committed release artifacts. Shared
positive and negative fixtures under `fixtures/` are exercised by Core,
Maintainer, Manager, and Plugin tests.

The synthetic RB-02 corpus is validated at the file/exit-status boundary:

    poetry run python scripts/validate_retrieval_corpus.py \
      contracts/fixtures/retrieval-benchmark-corpus-v1.json

The validator consumes the versioned RB-01 task and gold contracts, checks
cross-case identity and classification invariants, and never reads or writes
canonical data.

The public harvester integration boundary is documented in
[HARVESTER_LIBRARY_V1.md](HARVESTER_LIBRARY_V1.md) and is executable through
the `context-library/harvest-batch` Core contract.

Every payload identifies both a named schema family and its version. A bare
global `schema_version` is not sufficient compatibility information.
