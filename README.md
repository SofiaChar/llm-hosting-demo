# Shared LLM endpoint on Valohai

Host one open-source LLM in a single Valohai execution and let every other job and notebook call it as an endpoint.

**Why:** without this, every team that wants an LLM ends up downloading the model and paying for a GPU in every job and notebook. With a shared endpoint, you load the model once, on one GPU. Everyone else calls it over HTTP from small, cheap CPU machines, and they all get the same model version.

```
               ┌──────────────────────────────┐
               │ llm-server execution (GPU)   │
               │ Ollama + qwen2.5:1.5b        │
               │ VH_EXPOSE_PORTS=11434:11434  │
               └──────────────▲───────────────┘
                              │ http://<private-ip>:11434
        ┌─────────────────────┼─────────────────────┐
  batch job (team A)   batch job (team B)     Jupyter notebook
     CPU, no model        CPU, no model         CPU, no model
```

In our test, a client on a 2-vCPU machine with no GPU got answers in **~0.45 s on average** (about 130 tokens/s) from a server running on one NVIDIA T4. The whole client job took under 20 s from start to finish.

---

## What's in this repo

| File | What it does |
|---|---|
| `valohai.yaml` | Two steps: `llm-server` (hosts the model) and `llm-batch-inference` (calls it) |
| `server/start_server.sh` | Starts Ollama on port 11434, pulls the model, warms it up, then prints the endpoint and keeps running |
| `client/batch_inference.py` | Waits for the server, sends prompts to the OpenAI-compatible API, logs latency as Valohai metadata, saves `results-<client_name>.jsonl` |
| `notebooks/llm_client_demo.ipynb` | Calls the same endpoint from a notebook, with `requests` and the OpenAI SDK |

**The stack:** [Ollama](https://ollama.com) with **Qwen2.5 1.5B** by default. It ships as a single official Docker image (`ollama/ollama`), has an OpenAI-compatible API, and uses a GPU automatically when one is available. You can swap in a different model with the `model` parameter (see [Choosing a model](#choosing-a-model)).

---

## Prerequisites

1. **A Valohai project** linked to a copy of this repo, or the Valohai CLI (`pip install valohai-cli`) to run it ad hoc from a local folder.
2. **Two environments in the same cloud network:**
   - **Server:** a GPU environment is recommended (for example AWS `g4dn.xlarge` with an NVIDIA T4). Use **on-demand rather than spot**, so the server isn't reclaimed mid-session. A CPU machine with at least 8 vCPU and 16 GB RAM also works, just more slowly.
   - **Clients and notebooks:** any small CPU environment (for example AWS `t3.medium`).

   Run `vh environments` to list your environment slugs, then put them in the two `environment:` fields in `valohai.yaml`.
3. **A network rule that lets workers talk to each other on port 11434.** This is usually a one-time change by whoever manages your cloud account:

   | Cloud | Rule |
   |---|---|
   | **AWS** | EC2 → Security Groups → the Valohai workers group (often `valohai-sg-workers`) → add an inbound rule: **Custom TCP, port 11434, source = that same security group** |
   | **GCP** | VPC network → Firewall → create an ingress rule on the Valohai VPC: **TCP 11434**, source tag and target tag = the worker tag (often `valohai-worker`) |
   | **Azure** | The workers' Network Security Group → add an inbound rule: **TCP 11434**, source = the workers' VNet or subnet, action Allow |

   > ⚠️ Ollama has **no authentication**. Never open port 11434 to the internet (`0.0.0.0/0`). Allow it only from your workers' network.

   Your network may already allow traffic between workers. To check, skip ahead to step 2 below: if the client connects, you're fine.

---

## Step 1: Start the LLM server

From the UI, create an execution of step **`llm-server`**. From the CLI:

```bash
vh exec run llm-server --adhoc
```

Watch the logs. The first start takes a few minutes, because it pulls the Docker image, downloads the model (~1 GB) and warms it up on the GPU. When it's ready you'll see:

```
============================================================
 LLM endpoint is READY
   Model:              qwen2.5:1.5b
   From other workers: http://10.0.3.138:11434
   ...
============================================================
```

Copy the **`From other workers`** URL. It's also saved on the execution's **Metadata** tab as `llm_endpoint`.

The server keeps running until you stop it. While it runs, the model stays loaded, so every later request is fast.

### How the port is exposed
`valohai.yaml` sets the environment variable `VH_EXPOSE_PORTS: "11434:11434"` (format `<host-port>:<container-port>`). The Valohai agent maps that container port onto the worker machine. You can see it in the log as `Mapping <ip>:11434 to container port 11434`.

> `VH_EXPOSE_PORTS` must be defined under the step's `environment-variables` in `valohai.yaml`. Setting it with `export` inside the command has no effect.

---

## Step 2: Run inference jobs against it

Create an execution of step **`llm-batch-inference`** and set **`llm_endpoint`** to the URL from step 1. From the CLI:

```bash
vh exec run llm-batch-inference --adhoc --llm_endpoint=http://10.0.3.138:11434
```

Expected log:

```
LLM server is up at http://10.0.3.138:11434, model 'qwen2.5:1.5b' available
--- [team-a] prompt 1/5 ---
Q: In one sentence, what is MLOps?
A: MLOps stands for Machine Learning Operations, ...
{"prompt_index": 0, "latency_s": 0.37, "completion_tokens": 39}
...
Saved results to /valohai/outputs/results-team-a.jsonl
```

**Use your own prompts:** attach a `.txt` file (one prompt per line) to the `prompts` input.

**Several consumers at once:** launch a few executions with different `client_name` values. They all share the same model:

```bash
for t in team-a team-b team-c; do
  vh exec run llm-batch-inference --adhoc --llm_endpoint=http://10.0.3.138:11434 --client_name=$t
done
```

The server handles 4 requests in parallel by default (`OLLAMA_NUM_PARALLEL`) and queues the rest.

---

## Step 3: Use it from a notebook

1. Start a Valohai Notebook **in one of your client environments** (any Python image works).
2. Open `notebooks/llm_client_demo.ipynb` and paste the endpoint into the first cell.
3. Run all cells.

Because the API is OpenAI-compatible, existing code usually only needs a new `base_url`:

```python
from openai import OpenAI
client = OpenAI(base_url="http://10.0.3.138:11434/v1", api_key="not-needed")
client.chat.completions.create(model="qwen2.5:1.5b", messages=[{"role": "user", "content": "Hello!"}])
```

---

## Choosing a model

Set the `model` parameter on `llm-server` to any tag from the [Ollama library](https://ollama.com/library). Use the same value for `model` on the clients.

| Model | Size | Good for |
|---|---|---|
| `qwen2.5:0.5b` | ~0.4 GB | Fastest; fine on CPU-only servers |
| `qwen2.5:1.5b` (default) | ~1 GB | Good balance for demos and simple tasks |
| `llama3.2:3b` | ~2 GB | Better answers, still fits a T4 easily |
| `qwen2.5:7b` / `llama3.1:8b` | ~5 GB | Noticeably smarter; needs a GPU with ≥ 8 GB |

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Client keeps printing `waiting for LLM server... ConnectTimeout` | The network rule for port 11434 is missing, or the client runs in a different network. Check [Prerequisites](#prerequisites) step 3. |
| `ConnectionRefused` | The port isn't mapped. Check that `VH_EXPOSE_PORTS` is under the step's `environment-variables`, and that the server log shows the `Mapping …:11434` line. |
| Client says `model ... not pulled yet` | The server is still downloading. The client retries for up to 10 minutes. |
| Using the public IP times out | Use the **private** IP (`From other workers`) between workers. The public IP only works for sources your firewall explicitly allows. |
| Endpoint shows `http://unknown:11434` | Look up the worker's private IP in your cloud console. The instance ID is in the first lines of the execution log. |
| First request takes a minute or more | One-time GPU warm-up. The server does this before printing READY, so later requests are fast. |
| Answers are slow (several seconds each) | The server is probably running on CPU. Use a GPU environment or a smaller model, or lower `max_tokens`. |
| The endpoint changed | Each new server execution can land on a different machine. Copy the new URL from the READY box or the Metadata tab. |

---

## Good to know

- **Cost:** the server bills for as long as the execution runs. **Stop it when you're done.**
- **Security:** there's no authentication on the endpoint. Keep it reachable only inside your network. For production, put an authenticating proxy in front of it.
- **Going further:**
  - For higher throughput, swap Ollama for [vLLM](https://docs.vllm.ai) on a larger GPU. It's also OpenAI-compatible, so the clients don't change.
  - For a permanent endpoint, run several server replicas behind a load balancer, or use a Valohai Deployment.
