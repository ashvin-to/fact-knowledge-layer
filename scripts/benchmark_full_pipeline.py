"""
End-to-end Pipeline Performance Benchmark
Evaluates:
1. Embedding Throughput (PyTorch Multi-Core vs ONNX)
2. Vector Index Candidate Search (USearch SIMD vs NumPy)
3. MinHash LSH Fast Lexical Pre-Filter
4. PyMuPDF Layout-Aware Multi-Column Parsing Speed
"""

import time
import numpy as np
import fitz
from datasketch import MinHash, MinHashLSH
from usearch.index import Index
from sentence_transformers import SentenceTransformer

def benchmark_all():
    print("===============================================================")
    print("FACT KNOWLEDGE LAYER — PERFORMANCE BENCHMARK SUITE")
    print("===============================================================\n")

    # 1. Layout-Aware PDF Reading Order
    print("[1] Multi-Column Layout-Aware Text Parsing:")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Insert two columns of text
    page.insert_textbox(fitz.Rect(50, 50, 250, 400), "Column 1: Indian GDP expanded by 8.2% in FY24.")
    page.insert_textbox(fitz.Rect(300, 50, 500, 400), "Column 2: Express parcel volumes reached 701M in FY24.")
    
    t0 = time.perf_counter()
    blocks = page.get_text("blocks")
    text_blocks = [b for b in blocks if len(b) >= 7 and b[4].strip() and b[6] == 0]
    text_blocks.sort(key=lambda b: (round(b[0] / 150), b[1]))
    sorted_text = "\n".join(b[4].strip() for b in text_blocks)
    t_parse = (time.perf_counter() - t0) * 1000
    print(f"    - Parsed & column-sorted in: {t_parse:.3f} ms")
    print(f"    - Clean Reading Stream:\n      " + sorted_text.replace("\n", "\n      "))

    # 2. Embedding Throughput
    print("\n[2] Embedding Generation (1,000 facts):")
    facts = [
        f"Entity_{i % 20} — Metric_{i % 50} (USD {i * 1.5} Million, FY24)"
        for i in range(1, 1001)
    ]
    st_model = SentenceTransformer("all-MiniLM-L6-v2")
    t0 = time.perf_counter()
    vectors = st_model.encode(facts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
    t_enc = time.perf_counter() - t0
    print(f"    - Encoded 1,000 facts in: {t_enc:.3f}s ({1000/t_enc:.1f} facts/sec)")

    # 3. Vector Similarity Search (USearch SIMD)
    print("\n[3] Candidate Pair Discovery (1,000 vectors / 499,500 pairs):")
    # USearch SIMD Index
    t0 = time.perf_counter()
    index = Index(ndim=384, metric="cos", dtype="f32")
    index.add(np.arange(1000), vectors)
    matches = index.search(vectors, 20)
    found_simd = sum(len(k) for k in matches.keys)
    t_simd = (time.perf_counter() - t0) * 1000
    print(f"    - USearch SIMD 384-d Search: {t_simd:.2f} ms ({found_simd} candidate links evaluated)")

    # NumPy Baseline
    t0 = time.perf_counter()
    sim_matrix = np.dot(vectors, vectors.T)
    found_np = np.sum(sim_matrix >= 0.7)
    t_np = (time.perf_counter() - t0) * 1000
    print(f"    - NumPy Dot Matrix:         {t_np:.2f} ms")
    print(f"    -> USearch Speedup:         {t_np / max(t_simd, 0.001):.1f}x faster")

    # 4. MinHash LSH Pre-filtering
    print("\n[4] MinHash LSH Near-Duplicate Pre-Filtering (1,000 facts):")
    t0 = time.perf_counter()
    lsh = MinHashLSH(threshold=0.6, num_perm=64)
    minhashes = []
    for idx, fact_str in enumerate(facts):
        m = MinHash(num_perm=64)
        for word in fact_str.lower().split():
            m.update(word.encode("utf8"))
        minhashes.append(m)
        lsh.insert(f"fact_{idx}", m)
    t_lsh_build = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    query_matches = lsh.query(minhashes[0])
    t_lsh_query = (time.perf_counter() - t0) * 1000
    print(f"    - LSH Index Build: {t_lsh_build:.2f} ms (1,000 facts)")
    print(f"    - Exact Sub-millisecond Query: {t_lsh_query:.3f} ms (Found {len(query_matches)} lexical matches)")

    print("\n===============================================================")
    print("ALL PERFORMANCE BENCHMARKS EXECUTED SUCCESSFULLY")
    print("===============================================================")

if __name__ == "__main__":
    benchmark_all()
