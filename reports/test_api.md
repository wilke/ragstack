# RAGStack Test Document

This is a small test document used to validate the API ingest and retrieve endpoints.

RAGStack combines a vector database (Qdrant) with a large language model. The vector database stores embeddings of source passages. At query time, the user's question is embedded and the most similar passages are retrieved as context for the LLM, which then generates an answer grounded in those passages.

Hybrid retrieval combines dense vector search with sparse BM25 keyword search. The two ranked lists are fused with Reciprocal Rank Fusion, and the top candidates are optionally reranked with a cross-encoder before being passed to the language model.
