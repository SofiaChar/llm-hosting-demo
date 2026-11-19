# Central LLM hosting on Valohai: demo runbook

**What we're showing:** one Valohai execution hosts an open-source LLM and exposes it on a port. Other jobs and notebooks call it as an endpoint. Nobody else has to download, load or pay a GPU for their own copy of the model.

```
               ┌──────────────────────────────┐
               │ llm-server execution         │
               │ Ollama + qwen2.5:1.5b        │
               │ VH_EXPOSE_PORTS=11434:11434  │
               └──────────────▲───────────────┘
                              │ http://<private-ip>:11434  (SG allows TCP 11434 worker→worker)
        ┌─────────────────────┼─────────────────────┐
  batch job (team-a)   batch job (team-b)     Jupyter notebook
```

## Files

| File | What it does |
|---|---|
| `valohai.yaml` | Two steps: `llm-server` and `llm-batch-inference` |
| `server/start_server.sh` | Starts Ollama on 0.0.0.0:11434, pulls the model, prints the endpoint, then stays alive |
| `client/batch_inference.py` | Waits for the server, runs prompts through `/v1/chat/completions`, logs latency as metadata, saves `results-<client>.jsonl` |
| `notebooks/llm_client_demo.ipynb` | Same endpoint from a notebook, with plain `requests` and the OpenAI SDK |

**Why Ollama + Qwen2.5 1.5B:** it's a single binary in an official Docker image, the API is OpenAI-compatible, the model is about 1 GB and runs fine on CPU, and it uses a GPU automatically if the machine has one. If you need speed on CPU, switch to `qwen2.5:0.5b`.

---

## Prep checklist (do this BEFORE the demo, ~45 min)

### 1. Repo + project (5 min)
- [ ] Push this folder to a git repo (or use `vh exec run --adhoc` from the folder, so no repo is needed).
- [ ] Create or choose a Valohai project **on staging** and link the repo.
- [ ] Environments (already set in `valohai.yaml`; all in Staging AWS eu-west-1, so they share the VPC + worker security group):

  | Role | Environment slug | Why |
  |---|---|---|
  | LLM server | `staging-aws-eu-west-1-g4dn-xlarge` | T4 GPU gives fast answers live. On-demand, not spot |
  | Server fallback (no GPU) | `staging-aws-eu-west-1-c5-2xlarge` | 8 vCPU / 16 GB runs 1.5B fine on CPU |
  | Batch clients | `staging-aws-eu-west-1-t3-medium` | Cheap CPU box, which proves consumers need no GPU |
  | Notebook | `staging-aws-eu-west-1-t3-medium` (or `c5-xlarge`) | Same reason |

  Avoid the *Pro Trial*, Kubernetes/cluster, GCP/Azure and *Spot* environments for this demo. They're likely a different network or get reclaimed, and port exposure there works differently.
- [ ] In the server logs, check that Ollama found the GPU (look for `CUDA` / `Tesla T4` in the startup lines).

### 2. AWS security group (10 min). This is the step that usually breaks
- [ ] In the staging AWS account, open **EC2 → Security Groups** and find the group attached to Valohai workers (usually `valohai-sg-workers`; you can confirm on any running worker instance).
- [ ] **Add inbound rule:** Type `Custom TCP`, Port `11434`, Source = **the same security group** (self-reference, e.g. `sg-xxxx / valohai-sg-workers`). This lets any worker reach any other worker on 11434.
- [ ] *(Optional, to curl from your laptop)* add a second rule: TCP 11434 from **My IP** only. **Never** use `0.0.0.0/0`, because Ollama has no authentication.
- [ ] Note the rule IDs so you can remove them after the demo.

### 3. Dry run the server (10 min)
- [ ] Run step `llm-server` (UI: *Create execution*, or CLI `vh exec run llm-server --adhoc --environment <slug>`).
- [ ] In the logs, wait for the `LLM endpoint is READY` box. The first model pull takes 1–2 minutes.
- [ ] Copy `From other workers: http://10.x.x.x:11434`. The same value also appears in the execution's **metadata** as `llm_endpoint`.
- [ ] Check the execution has the port mapping. If the private IP prints as `unknown`, get the instance's private IP from the EC2 console instead.

### 4. Dry run one client (5 min)
- [ ] Run `llm-batch-inference` with `llm_endpoint` = the URL from step 3.
- [ ] Confirm it prints `LLM server is up`, then Q/A pairs, then summary metadata. Check that `results-team-a.jsonl` appears under outputs.

### 5. Dry run the notebook (10 min)
- [ ] Start a Valohai Notebook **in the same staging environment** (any Python image, e.g. `python:3.11` or a Jupyter image).
- [ ] Upload or open `notebooks/llm_client_demo.ipynb`, paste the endpoint into the first cell, and run all cells.

### 6. Decide: keep the server warm or restart live?
- **Recommended:** start `llm-server` ~15 min before the demo and leave it running, so you skip the pull and warm-up on stage.
- Keep a finished client execution from the dry run as a backup to show if something fails live.

---

## Demo script (~10 min)

1. **The problem (1 min).** "Every team that wants an LLM ends up downloading the model and paying for its own GPU in every job and notebook. That's slow, expensive and inconsistent."
2. **The idea (1 min).** Show the diagram: host the model once, everyone calls it as an endpoint.
3. **The server (2 min).** Open the running `llm-server` execution. Show the `VH_EXPOSE_PORTS: 11434:11434` env var in the YAML, the READY box, and the `llm_endpoint` metadata.
4. **Many consumers (3 min).** Launch 2–3 client executions at once with different `client_name` values (team-a, team-b, team-c), from the UI or from the CLI:
   ```bash
   for t in team-a team-b team-c; do
     vh exec run llm-batch-inference --adhoc \
       --llm-endpoint=http://10.x.x.x:11434 --client-name=$t
   done
   ```
   Point out that the client image is just `python:3.11-slim` with no model and no GPU, and it still gets LLM answers. Show the latency metadata chart and the results output.
5. **Notebook (2 min).** Run the notebook. The OpenAI-SDK cell shows that existing code only needs a new `base_url`.
6. **What's next (1 min).** Swap Ollama for vLLM on a GPU for higher throughput, add auth or a proxy, and move to a Valohai Deployment for a permanent endpoint. Several server replicas can sit behind a load balancer.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Client loops `waiting for LLM server... ConnectTimeout` | Security group rule missing, or the client is in a different VPC/environment. Check step 2. |
| `ConnectionRefused` | Port not mapped. Make sure `VH_EXPOSE_PORTS` is in the **step's `environment-variables`** (setting it with `export` does not work), and that Ollama is bound to `0.0.0.0` (the script does this). |
| `model ... not pulled yet` | The server is still downloading. The client retries for 10 min. |
| You used the public IP and it times out | Use the **private** IP between workers. The public IP only works for sources the SG allows. |
| Slow answers | You're on CPU. Use `qwen2.5:0.5b`, reduce `max_tokens`, or use a GPU environment (Ollama uses the GPU automatically). |
| Server execution stops on its own | Check the environment's max runtime and the logs for OOM. 1.5B needs ~3 GB RAM. |

## Cleanup after the demo
- [ ] **Stop the `llm-server` execution.** It runs (and bills) until you stop it.
- [ ] Remove the temporary SG rules if they shouldn't stay.
