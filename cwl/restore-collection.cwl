#!/usr/bin/env cwl-runner
# Restore-collection workflow (#358, phase 2 of #353): rebuild a dormant
# collection's stores from its archive — the `versions/<n>/` directories the
# ingest workflows' archive step (cwl/archive-collection.cwl) wrote into the
# owner's Workspace — by running the loader in REPLAY mode.
#
# HOW THE VERSIONS GET HERE. The API submits this AS THE USER (the caller's
# BV-BRC token authenticates the submission) with `versions` given as `ws://`
# Directory locations. The engine's server-side staging pre-stages every one of
# them to local paths with that token before dispatch — so this tool reads
# plain directories and NO task ever sees the token (least privilege by
# construction; see #353 "Token handling").
#
# WHAT THE LOADER DOES WITH THEM (python/scripts/load_embeddings.py --replay).
# Every version is verified BEFORE the first write: each file's sha256 and byte
# size against its manifest, the vectors geometry, and the manifest's
# `spec_hash` against `spec_hash` below — the registry row's build-spec hash
# (ADR-0002's guard applied to an archive the user can edit). Any failure exits
# non-zero with an `ArchiveCorrupt:` / `SpecMismatch:` line and NOTHING is
# written, so the API marks the collection `lost` rather than half-restored.
# Then, in order: a chunk version deletes each of its documents' prior chunks
# and upserts both legs (deterministic ids — a re-run after a crash converges);
# a tombstone version deletes its doc ids from both legs. Streaming: the
# vectors are never materialised as Python lists (the #342 lesson).
#
# The collection's physical Qdrant collection and ES index come from the
# REGISTRY ENTRY named by `collection_id` (#263), never from the command line;
# the worker's environment must see the same registry the API does (as for
# load-embeddings.cwl).
#
# Single task, not a scatter: versions are ordered (later versions override
# earlier ones and tombstones must land after the chunks they remove), and the
# load is where Qdrant backpressure belongs.
#
# CONTAINERIZED (#135): runs in the ragstack-worker image; both docker keys are
# declared (GoWe reads dockerPull, cwltool --singularity reads dockerImageId —
# see cwl/README.md). Scripts live at /opt/ragstack/scripts.
#
#   CWL_SINGULARITY_CACHE=apptainer/images \
#     cwltool --singularity cwl/restore-collection.cwl restore.inputs.yml
cwlVersion: v1.2
class: Workflow

inputs:
  versions:
    type: Directory[]
    doc: "The archive version directories (ragstack-archive/1), in replay order
      — `ws:///<user>/home/.ragstack/collections/<id>/versions/<n>` each, which
      the engine pre-stages locally with the submitter's token."
  collection_id:
    type: string
    doc: "Registry collection id (#263): the physical Qdrant collection and ES
      index are resolved from its entry, and every manifest must name it."
  spec_hash:
    type: string
    doc: "The registry row's build-spec hash (ADR-0002). A version whose
      manifest carries a different one is refused before any write."
  qdrant_url:
    type: string
    default: "http://localhost:6333"
  es_url:
    type: string
    default: "http://localhost:9200"
  backpressure:
    type: boolean
    default: false
    doc: "Hold each upsert until the collection is green (#141). OFF by default;
      set true for a very large corpus on a capped Qdrant."
  bulk_refresh:
    type: boolean
    default: true
    doc: "Skip the per-write text-index refresh during the replay (one refresh
      at the end). Nothing searches a collection while it is `restoring`, so
      the default is the fast path."

steps:
  replay:
    doc: "Verify every version, then replay them in order into Qdrant/ES -> load summary."
    in:
      versions: versions
      collection_id: collection_id
      spec_hash: spec_hash
      qdrant_url: qdrant_url
      es_url: es_url
      backpressure: backpressure
      bulk_refresh: bulk_refresh
    out: [summary]
    run:
      class: CommandLineTool
      requirements:
        DockerRequirement:
          dockerPull: ragstack-worker.sif
          dockerImageId: ragstack-worker.sif
        NetworkAccess:
          networkAccess: true
      baseCommand: [python, /opt/ragstack/scripts/load_embeddings.py]
      inputs:
        versions:
          type: Directory[]
          inputBinding:
            prefix: --replay
            position: 1
        collection_id:
          type: string
          inputBinding:
            prefix: --collection-id
            position: 2
        spec_hash:
          type: string
          inputBinding:
            prefix: --spec-hash
            position: 3
        qdrant_url:
          type: string
          inputBinding:
            prefix: --qdrant-url
            position: 4
        es_url:
          type: string
          inputBinding:
            prefix: --es-url
            position: 5
        backpressure:
          type: boolean
          inputBinding:
            prefix: --backpressure
            position: 6
        bulk_refresh:
          type: boolean
          inputBinding:
            prefix: --bulk-refresh
            position: 7
      arguments:
        - {position: 8, prefix: --fail-on-error}
        - position: 9
          prefix: --out
          valueFrom: load-summary.json
      outputs:
        summary:
          type: File
          outputBinding:
            glob: load-summary.json

outputs:
  summary:
    type: File
    outputSource: replay/summary
