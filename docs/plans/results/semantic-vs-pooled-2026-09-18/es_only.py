"""ES leg ALONE — no chunker, no bridge, no semantic anything.

If 'Unclosed client session' appears here, the warning in the semantic run came
from elastic_transport and not from the breakpoint bridge (which is httpx-only).
"""
import asyncio
from ragstack.stores.elasticsearch import ElasticsearchTextIndex

async def main():
    idx = ElasticsearchTextIndex(
        url="http://localhost:24043",
        index="ragstack_lib_sem_e2e_salesforce_sfr_embedding_4096_semantic_pooled_buff_34e3bcc9",
    )
    await idx.ensure_index()   # one real call, then exit without closing

asyncio.run(main())
print("--- exited; any warning below is the ES client, nothing else ran ---")
