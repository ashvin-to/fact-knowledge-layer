"""
Benchmark comparing ONNX FastEmbed + USearch SIMD vector index
vs PyTorch SentenceTransformer + Python loop.
"""

import time
import numpy as np
from fastembed import TextEmbedding
from usearch.index import Index
from sentence_transformers import SentenceTransformer

def run_benchmark():
    # Generate 500 representative financial & corporate facts
    sample_facts = [
        f"Delhivery Limited — Express parcel volume ({i * 10} million packages)"
        for i in range(1, 251)
    ] + [
        f"Indian Economy — Real GDP growth rate ({6.0 + (i % 30) * 0.1} %)"
        for i in range(1, 251)
    ]
    
    print(f"--- 1. EMBEDDING BENCHMARK ({len(sample_facts)} facts) ---")
    
    # 1. PyTorch SentenceTransformer
    st_start = time.perf_counter()
    st_model = SentenceTransformer("all-MiniLM-L6-v2")
    st_load_time = time.perf_counter() - st_start
    
    st_enc_start = time.perf_counter()
    st_vectors = st_model.encode(sample_facts, convert_to_numpy=True, normalize_embeddings=True)
    st_enc_time = time.perf_counter() - st_enc_start
    print(f"[PyTorch sentence-transformers] Load: {st_load_time:.3f}s | Encode: {st_enc_time:.3f}s ({len(sample_facts)/st_enc_time:.1f} facts/sec)")

    # 2. FastEmbed (ONNX Runtime)
    fe_start = time.perf_counter()
    fe_model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2")
    fe_load_time = time.perf_counter() - fe_start
    
    fe_enc_start = time.perf_counter()
    fe_vectors = list(fe_model.embed(sample_facts))
    fe_vectors = np.array(fe_vectors, dtype=np.float32)
    norms = np.linalg.norm(fe_vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    fe_vectors = fe_vectors / norms
    fe_enc_time = time.perf_counter() - fe_enc_start
    print(f"[FastEmbed (ONNX)]             Load: {fe_load_time:.3f}s | Encode: {fe_enc_time:.3f}s ({len(sample_facts)/fe_enc_time:.1f} facts/sec)")
    print(f"-> Speedup: {st_enc_time / fe_enc_time:.2f}x faster encoding with FastEmbed!")

    print(f"\n--- 2. VECTOR CANDIDATE PAIR SEARCH (500 vectors, threshold=0.7) ---")
    
    # Python nested loop
    loop_start = time.perf_counter()
    loop_candidates = []
    n = len(sample_facts)
    for i in range(n):
        for j in range(i + 1, n):
            sim = float(np.dot(st_vectors[i], st_vectors[j]))
            if sim >= 0.7:
                loop_candidates.append((i, j, sim))
    loop_time = time.perf_counter() - loop_start
    print(f"[Python Nested Loop] Time: {loop_time*1000:.2f} ms | Found {len(loop_candidates)} candidate pairs")

    # USearch SIMD Index
    usearch_start = time.perf_counter()
    index = Index(ndim=384, metric="cos", dtype="f32")
    index.add(np.arange(n), fe_vectors)
    
    # Vectorized / SIMD batch search
    matches = index.search(fe_vectors, 10)
    usearch_candidates = []
    for i in range(n):
        keys = matches.keys[i]
        distances = matches.distances[i]
        for key, dist in zip(keys, distances):
            if key > i:
                sim = 1.0 - dist
                if sim >= 0.7:
                    usearch_candidates.append((i, key, sim))
    usearch_time = time.perf_counter() - usearch_start
    print(f"[USearch SIMD Index] Time: {usearch_time*1000:.2f} ms | Found {len(usearch_candidates)} candidate pairs")
    print(f"-> Speedup: {loop_time / usearch_time:.2f}x faster vector search with USearch!")

if __name__ == "__main__":
    run_benchmark()
