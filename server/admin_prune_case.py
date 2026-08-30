"""Manual maintenance CLI — not routed, never called from a request path.

Deletes one uploaded case document from Firestore (`the_stand_uploaded_cases`)
by id. Use to prune a stale rehearsal/test upload out of the demo-visible
grid, e.g. after re-uploading a corrected file. See `UploadedCaseStore.delete_case`
in firestore_store.py.

Usage: python -m server.admin_prune_case <case_id>
"""

import asyncio
import sys

from server.firestore_store import UploadedCaseStore


async def _delete(case_id: str) -> None:
    store = UploadedCaseStore()
    await store.delete_case(case_id)
    print(f"Deleted uploaded case (if it existed): {case_id}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m server.admin_prune_case <case_id>")
        sys.exit(1)
    asyncio.run(_delete(sys.argv[1]))


if __name__ == "__main__":
    main()
