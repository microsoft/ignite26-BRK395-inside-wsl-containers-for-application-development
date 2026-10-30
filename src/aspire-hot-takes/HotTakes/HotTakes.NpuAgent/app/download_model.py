"""Download an OpenVINO model from Hugging Face using only the standard library.

Usage: python3 download_model.py OpenVINO/Qwen2.5-1.5B-Instruct-int4-ov /models
"""
import json
import os
import sys
import urllib.request

HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co")


def main(repo: str, root: str) -> None:
    dest = os.path.join(root, repo.split("/")[-1])
    os.makedirs(dest, exist_ok=True)
    with urllib.request.urlopen(f"{HF}/api/models/{repo}", timeout=30) as r:
        files = [s["rfilename"] for s in json.load(r)["siblings"]]
    for name in files:
        if name.startswith(".") or name.endswith((".md", ".gitattributes")):
            continue
        out = os.path.join(dest, name)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            print(f"  ✓ {name} (cached)")
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        print(f"  ↓ {name}", flush=True)
        urllib.request.urlretrieve(f"{HF}/{repo}/resolve/main/{name}", out + ".part")
        os.replace(out + ".part", out)
    print(f"Model ready at {dest}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "/models")
