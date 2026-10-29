"""Store families — one module per technology (ADR-0021 §1, §12).

A family is how a store is *reached* (`fs`, `s3`, `db`, ...), not what
the store means; the meaning is the read site's `source_type`. Each
family implements `base.Family` and is registered in
`store.registry.register_family`.

The package is empty in B.3b-core on purpose: the core ships the
interface, the registry and the resolver, and the first family (`fs`)
lands on top of them in B.3b-fs. Adding one is a file here plus a line
in the registry — nothing in the core changes.
"""
