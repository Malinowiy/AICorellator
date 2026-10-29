"""Store families — one module per technology (ADR-0021 §1, §12).

A family is how a store is *reached* (`fs`, `s3`, `db`, ...), not what
the store means; the meaning is the read site's `source_type`. Each
family implements `base.Family` and is registered in
`store.registry.register_family`.

**Importing this package is what registers the families it ships**
(`fs`, `s3` and `db` today). The registration line lives here, in one
place, rather than in each family module, for the reason ADR-0011 gives
for detectors: a family that nobody imports must not run, and the
package every caller already imports is this one. `store/__init__.py`
imports this package, so `import aicorellator.store` — which the
pipeline, the stage and the tests all do — is enough to make the
families visible to the resolver. The resolver reads the registry when
it *runs*, so the order that matters is "import the package, then
resolve".
"""

from __future__ import annotations

from .db import DbFamily
from .fs import FsFamily
from .s3 import S3Family
from ..registry import register_family

#: The first family (B.3b-fs). The key and `FsFamily.name` are the same
#: string on purpose: the key is what config names and what a `store`
#: node's `family` attribute holds, and the registry warns if a class
#: ever declares a different one.
register_family("fs", FsFamily)

#: The second family (B.3b-s3): the object stores a `boto3` `s3` handle
#: names, by the `Bucket` / `Key` literals its calls carry. Same rule as
#: `fs`, and the same reason the line lives here.
register_family("s3", S3Family)

#: The third family (B.3b-db): the tables a `cursor.execute` statement
#: names, by the table its literal SQL string carries. Same rule as the
#: other two, and the same reason the line lives here.
register_family("db", DbFamily)

__all__ = ["DbFamily", "FsFamily", "S3Family"]
