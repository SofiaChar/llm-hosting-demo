"""
Batch inference job that uses a centrally hosted LLM instead of loading its own model.

Talks to the llm-server execution through Ollama's OpenAI-compatible API.
Valohai passes parameters as --name=value.
"""
import argparse
import glob
import json
import os
import sys
import time

import requests

DEFAULT_PROMPTS = [
    "In one sentence, what is MLOps?",
    "Give three tips for reducing GPU costs when training models.",
    "Summarize in two sentences why teams share one hosted LLM instead of each hosting their own.",
    "Write a haiku about machine learning pipelines.",
    "Classify the sentiment of this review as positive, negative or neutral: 'The setup took a while, but the results are great.'",
]


def normalize_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint.startswith("http"):
        endpoint = "http://" + endpoint
    return endpoint


def wait_for_server(endpoint: str, model: str, timeout_s: int) -> None:
    """Block until the server answers and the model has been pulled."""
    deadline = time.time() + timeout_s
    last_error = None
    while time.time() < deadline:
        try:
            r = requests.get(f"{endpoint}/api/tags", timeout=5)
            r.raise_for_status()
            names = [m.get("name", "") for m in r.json().get("models", [])]
            if any(n == model or n.startswith(model + ":") or n.split(":")[0] == model for n in names):
                print(f"LLM server is up at {endpoint}, model '{model}' available")
                return
            last_error = f"server up, but model '{model}' not pulled yet (have: {names})"
        except requests.RequestException as e:
            last_error = repr(e)
        print(f"waiting for LLM server... {last_error}")
        time.sleep(5)
    sys.exit(
        f"LLM server not reachable at {endpoint} after {timeout_s}s: {last_error}\n"
        "Check: (1) the llm-server execution is running and printed READY, "
        "(2) you used its PRIVATE IP, (3) the security group allows inbound TCP 11434 from the workers."
    )


def load_prompts() -> list:
    inputs_dir = os.environ.get("VH_INPUTS_DIR", "/valohai/inputs")
    files = glob.glob(os.path.join(inputs_dir, "prompts", "*"))
    if files:
        with open(files[0], encoding="utf-8") as f:
            prompts = [line.strip() for line in f if line.strip()]
        if prompts:
            print(f"Loaded {len(prompts)} prompts from {files[0]}")
            return prompts
    print(f"No prompts input given, using {len(DEFAULT_PROMPTS)} built-in demo prompts")
    return DEFAULT_PROMPTS


def chat(endpoint: str, model: str, prompt: str, max_tokens: int) -> dict:
    r = requests.post(
        f"{endpoint}/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
        timeout=300,
    )
    r.raise_for_status()
    return r.json()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--llm_endpoint", required=True)
    p.add_argument("--model", default="qwen2.5:1.5b")
    p.add_argument("--client_name", default="team-a")
    p.add_argument("--max_tokens", type=int, default=200)
    p.add_argument("--wait_timeout", type=int, default=600)
    args = p.parse_args()

    endpoint = normalize_endpoint(args.llm_endpoint)
    wait_for_server(endpoint, args.model, args.wait_timeout)

    outputs_dir = os.environ.get("VH_OUTPUTS_DIR", "./outputs")
    os.makedirs(outputs_dir, exist_ok=True)
    out_path = os.path.join(outputs_dir, f"results-{args.client_name}.jsonl")

    prompts = load_prompts()
    total_latency, total_tokens = 0.0, 0
    with open(out_path, "w", encoding="utf-8") as out:
        for i, prompt in enumerate(prompts):
            t0 = time.time()
            resp = chat(endpoint, args.model, prompt, args.max_tokens)
            latency = time.time() - t0
            answer = resp["choices"][0]["message"]["content"].strip()
            tokens = resp.get("usage", {}).get("completion_tokens", 0) or 0
            total_latency += latency
            total_tokens += tokens

            print(f"\n--- [{args.client_name}] prompt {i + 1}/{len(prompts)} ---")
            print(f"Q: {prompt}")
            print(f"A: {answer}")
            # JSON line on stdout = Valohai metadata (shows up as charts in the UI)
            print(json.dumps({"prompt_index": i, "latency_s": round(latency, 2), "completion_tokens": tokens}))

            out.write(json.dumps({
                "client": args.client_name,
                "model": args.model,
                "prompt": prompt,
                "answer": answer,
                "latency_s": round(latency, 3),
                "completion_tokens": tokens,
            }) + "\n")

    print(json.dumps({
        "client": args.client_name,
        "num_prompts": len(prompts),
        "avg_latency_s": round(total_latency / len(prompts), 2),
        "tokens_per_s": round(total_tokens / total_latency, 1) if total_latency else 0,
    }))
    print(f"\nSaved results to {out_path}")


if __name__ == "__main__":
    main()
