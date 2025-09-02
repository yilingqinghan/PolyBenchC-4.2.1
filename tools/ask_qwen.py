#!/usr/bin/env python3
# -*- coding: utf-8 -*-


import os
import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple

# -------- Paths & defaults --------
IR_DIR   = Path("ir")
OUT_DIR  = Path("ir_llm")
PROMPT_DIR = OUT_DIR  # store prompts for auditing
MODEL_DEFAULT = os.getenv("LLM_MODEL", "qwen-max")

# OpenAI-compatible (Dashscope) settings
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
DASHSCOPE_KEY   = os.getenv("DASHSCOPE_API_KEY")

# Optional Gemini token (placeholder for future provider option)
GEMINI_TOKEN = os.getenv("GEMINI_TOKEN")

# -------- Prompt template (user-provided, lightly hardened) --------
PROMPT_TEMPLATE = """You are a compiler optimization expert specializing in loop optimizations for LLVM IR (loop unrolling, vectorization, loop-invariant code motion, etc.) targeting AMD Ryzen 9 7950X. You should leverage AVX2/AVX512 and cache alignment features. The following test cases come from TSVC. Please output the optimized LLVM IR directly.

---
### Example

#### Input LLVM IR

```llvm
; Compute sum of squares of array elements
define i64 @sum_of_squares(i64* %arr, i32 %n) {
entry:
  %i = alloca i32
  %sum = alloca i64
  store i32 0, i32* %i
  store i64 0, i64* %sum
  br label %loop

loop:
  %i_val = load i32, i32* %i
  %cmp = icmp slt i32 %i_val, %n
  br i1 %cmp, label %body, label %exit

body:
  %idx = sext i32 %i_val to i64
  %elem = load i64, i64* getelementptr(%arr, %idx)
  %sq = mul i64 %elem, %elem
  %old = load i64, i64* %sum
  %new = add i64 %old, %sq
  store i64 %new, i64* %sum
  %i_next = add i32 %i_val, 1
  store i32 %i_next, i32* %i
  br label %loop

exit:
  %res = load i64, i64* %sum
  ret i64 %res7
}
```

#### Example Output (Optimized LLVM IR)
```
; Vectorized + loop-unrolled (factor 4), targeting AMD 7950X
define i64 @sum_of_squares(i64* %arr, i32 %n) {
entry:
  %n64 = sext i32 %n to i64
  %sum = alloca <4 x i64>, align 32
  %i = alloca i64, align 8
  store <4 x i64> zeroinitializer, <4 x i64>* %sum
  store i64 0, i64* %i
  %vec_bound = and i64 %n64, -4
  br label %loop

loop:
  %i_val = load i64, i64* %i
  %cmp = icmp ult i64 %i_val, %vec_bound
  br i1 %cmp, label %body_vec, label %scalar

body_vec:
  %base = getelementptr inbounds i64, i64* %arr, i64 %i_val
  %v = load <4 x i64>, <4 x i64>* %base, align 32
  %v_sq = mul <4 x i64> %v, %v
  %old_sum = load <4 x i64>, <4 x i64>* %sum, align 32
  %new_sum = add <4 x i64> %old_sum, %v_sq
  store <4 x i64> %new_sum, <4 x i64>* %sum, align 32
  %i_next = add i64 %i_val, 4
  store i64 %i_next, i64* %i
  br label %loop

scalar:
  %i_val2 = load i64, i64* %i
  %cmp2 = icmp ult i64 %i_val2, %n64
  br i1 %cmp2, label %body_s, label %exit

body_s:
  %idx = load i64, i64* %i
  %e_ptr = getelementptr i64, i64* %arr, i64 %idx
  %e = load i64, i64* %e_ptr
  %sq = mul i64 %e, %e
  %sum_s = alloca i64
  store i64 0, i64* %sum_s
  %old2 = load i64, i64* %sum_s
  %new2 = add i64 %old2, %sq
  store i64 %new2, i64* %sum_s
  %i_next2 = add i64 %idx, 1
  store i64 %i_next2, i64* %i
  br label %scalar

exit:
  ; Reduce vector accumulator to scalar
  %sum_vec = load <4 x i64>, <4 x i64>* %sum, align 32
  %e0 = extractelement <4 x i64> %sum_vec, i32 0
  %e1 = extractelement <4 x i64> %sum_vec, i32 1
  %e2 = extractelement <4 x i64> %sum_vec, i32 2
  %e3 = extractelement <4 x i64> %sum_vec, i32 3
  %t01 = add i64 %e0, %e1
  %t23 = add i64 %e2, %e3
  %vec_sum = add i64 %t01, %t23
  %scalar_sum = load i64, i64* %sum_s
  %result = add i64 %vec_sum, %scalar_sum
  ret i64 %result
}
```

### Now optimize the following loop-based LLVM IR directly

```llvm
{{INPUT_LLVM_IR}}
```

#### Optimization Goals
1. Optimize for performance.
2. Fully consider architecture-specific features when optimizing loops (AMD Ryzen 9 7950X; leverage AVX2/AVX512, cache alignment).
3. Don't modify metadata.
4. Keep the function signature identical and semantics preserved.

#### Output Format
Output ONLY the optimized LLVM IR (single function, from `define` to the matching `}`), WITHOUT any additional text or markdown.
"""

# -------- IR parsing: extract full define..{..} for kernel_* --------
FUNC_DEF_RE = re.compile(r'^\s*define\s+[^@]*@(?P<name>kernel_[A-Za-z0-9_]+)\s*\(', re.MULTILINE)

def find_kernel_functions(ir_text: str) -> Dict[str, Tuple[int, int]]:
    """
    Return {func_name: (start_idx, end_idx)}, where end_idx is the index after the matching '}'.
    """
    out: Dict[str, Tuple[int, int]] = {}
    for m in FUNC_DEF_RE.finditer(ir_text):
        fn = m.group("name")
        start = m.start()
        brace_open = ir_text.find("{", m.end())
        if brace_open == -1:
            continue
        depth = 0
        i = brace_open
        while i < len(ir_text):
            c = ir_text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    out[fn] = (start, end)
                    break
            i += 1
    return out

def sanitize_model_return(s: str) -> str:
    """
    Try to extract a single `define ... }` function block from model output, stripping extra commentary.
    """
    m = re.search(r'^\s*define\s.*?\}\s*$', s, flags=re.DOTALL | re.MULTILINE)
    if m:
        return m.group(0).strip() + "\n"
    return s.strip() + "\n"

# -------- LLM calls (OpenAI-compatible; Dashscope) --------
def call_openai_compat(messages: List[Dict], model: str) -> str:
    from openai import OpenAI  # pip install openai
    if not DASHSCOPE_KEY:
        raise RuntimeError("DASHSCOPE_API_KEY not set. export DASHSCOPE_API_KEY=...")
    client = OpenAI(api_key=DASHSCOPE_KEY, base_url=OPENAI_BASE_URL)
    completion = client.chat.completions.create(model=model, messages=messages)
    try:
        return completion.choices[0].message.content
    except Exception:
        raw = json.loads(completion.model_dump_json())
        return raw["choices"][0]["message"]["content"]

def call_llm(provider: str, messages: List[Dict], model: str) -> str:
    provider = provider.lower()
    if provider in ("openai", "dashscope", "ali", "qwen"):
        return call_openai_compat(messages, model)
    elif provider in ("gemini",):
        # Placeholder: if you have an OpenAI-compatible Gemini endpoint, reuse call_openai_compat
        if not GEMINI_TOKEN:
            raise RuntimeError("GEMINI_TOKEN not set. export GEMINI_TOKEN=...")
        raise NotImplementedError("Gemini provider not implemented. Use --provider=openai/dashscope or extend this.")
    else:
        raise ValueError(f"Unknown provider: {provider}")

# -------- Merge: replace functions inside the original IR --------
def merge_functions(original: str, replacements: Dict[str, str]) -> str:
    spans = find_kernel_functions(original)
    parts = []
    last = 0
    for fn, (s, e) in sorted(spans.items(), key=lambda kv: kv[1][0]):
        parts.append(original[last:s])
        parts.append(replacements.get(fn, original[s:e]))
        last = e
    parts.append(original[last:])
    return "".join(parts)

# ---------------- Main ----------------
def main():
    ap = argparse.ArgumentParser(description="Optimize kernel_* functions in LLVM IR via LLM using a strict prompt template.")
    ap.add_argument("--in-dir", default=str(IR_DIR), help="Input .ll directory (default: ir/)")
    ap.add_argument("--out-dir", default=str(OUT_DIR), help="Output directory (default: ir_llm/)")
    ap.add_argument("--provider", default="openai", help="Provider: openai/dashscope/gemini (default: openai=dashscope-compatible)")
    ap.add_argument("--model", default=MODEL_DEFAULT, help=f"Model name (default: {MODEL_DEFAULT})")
    ap.add_argument("--merge", action="store_true", help="Merge optimized functions back into full .ll (emit *_llmopt.ll)")
    ap.add_argument("--dry-run", action="store_true", help="Only generate prompts without calling the model")
    args = ap.parse_args()

    in_dir  = Path(args.in_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ll_files = sorted(in_dir.glob("*.ll"))
    if not ll_files:
        print(f"[info] no .ll files under {in_dir}")
        return

    for llp in ll_files:
        bench = llp.stem
        text  = llp.read_text(encoding="utf-8")
        spans = find_kernel_functions(text)
        if not spans:
            print(f"[skip] {llp.name}: no kernel_* found")
            continue

        prompts_audit = []
        replacements: Dict[str, str] = {}

        for func_name, (s, e) in spans.items():
            func_ir = text[s:e].strip()
            prompt = PROMPT_TEMPLATE.replace("{{INPUT_LLVM_IR}}", func_ir)

            prompts_audit.append({"function": func_name, "prompt": prompt})

            if args.dry_run:
                continue

            messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt},
            ]
            try:
                resp = call_llm(args.provider, messages, model=args.model)
            except Exception as ex:
                print(f"[error] LLM call failed: {bench}/{func_name}: {ex}")
                continue

            resp_fn = sanitize_model_return(resp)
            out_fn = out_dir / f"{bench}__{func_name}.opt.ll"
            out_fn.write_text(resp_fn, encoding="utf-8")
            print(f"[done] {bench}/{func_name} -> {out_fn.name}")
            replacements[func_name] = resp_fn

        # Save prompts for auditing/repro
        (PROMPT_DIR / f"{bench}.prompts.md").write_text(
            "\n\n".join([f"### {item['function']}\n\n```markdown\n{item['prompt']}\n```" for item in prompts_audit]),
            encoding="utf-8"
        )

        if args.merge and replacements:
            merged = merge_functions(text, replacements)
            merged_path = out_dir / f"{bench}_llmopt.ll"
            merged_path.write_text(merged, encoding="utf-8")
            print(f"[merge] {merged_path.name} generated")

    print("[done] all finished.")

if __name__ == "__main__":
    main()
