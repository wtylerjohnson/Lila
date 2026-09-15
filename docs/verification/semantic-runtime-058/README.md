# Semantic runtime repair 058

The missing default encoder dependencies are declared in requirements.txt and installed in the operating virtual environment. All 49 previous package versions remained unchanged. The existing cached BGE model ran on MPS and the native dense/hybrid methods operated over 256 saved records. This repair does not populate the 397,819-record operating index or dispatch client releases.

Install from the operating checkout:

```sh
uv pip install --python .venv/bin/python -r requirements.txt
```

The exact diagnostic was run from the candidate with:

```sh
PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 /Users/wtjohnson/Lila/.venv/bin/python docs/verification/semantic-runtime-058/real_smoke.py --root /Users/wtjohnson/Lila --output /Users/wtjohnson/Documents/Codex/2026-09-08/give-me-a-fun-recap-of/outputs/SEMANTIC_RUNTIME_058/exact-script
```

The offline flags are required to use only cached model files. Source database hashes and protected-file hashes were checked independently before and after the diagnostic. No receipt boolean substitutes for those checks. The sample's keyword pool was zero; this is runtime proof, not comparative relevance or two-lane fusion quality proof.
