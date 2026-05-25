#!/usr/bin/env python3
"""Reusable Markdown translation pipeline with Bedrock and Ollama providers."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


DEFAULT_BEDROCK_MODEL = "qwen.qwen3-32b-v1:0"
DEFAULT_OLLAMA_MODEL = "qwen2.5:14b-instruct"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
OLLAMA_CLOUD_HOST = "https://ollama.com"

LANGUAGE_CODES = {
    "arabic": "ar",
    "chinese": "zh",
    "english": "en",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "japanese": "ja",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
}

BEDROCK_PRICE_MODELS = {
    "claude_3_haiku_bedrock": (0.25, 1.25),
    "claude_3_5_haiku_bedrock": (0.80, 4.00),
    "claude_haiku_4_5_global": (1.00, 5.00),
    "claude_haiku_4_5_regional": (1.10, 5.50),
    "qwen3_next_80b_bedrock_us": (0.15, 1.20),
    "qwen3_next_80b_bedrock_batch_us": (0.075, 0.60),
    "qwen3_32b_bedrock_standard": (0.1545, 0.6180),
    "qwen3_32b_bedrock_flex": (0.0773, 0.3090),
}

DEFAULT_DE_RU_GLOSSARY = """\
- Hegels Wissenschaft der Logik = «Наука логики» Гегеля
- Wissenschaft der Logik = «Наука логики»
- Felix Meiner Verlag = Felix Meiner Verlag
- Meiner = Meiner
- Lehre = учение
- Die objektive Logik = объективная логика
- Begriff = понятие
- begrifflich = понятийный
- Begreiffen/Begreifen = постижение / постигать
- Idee = идея
- Sein/Seyn = бытие
- Dasein/Daseyn = наличное бытие
- Wesen = сущность
- Erscheinung = явление
- Schein = видимость
- Wirklichkeit = действительность
- Aufheben/Aufhebung/aufgehoben = снятие / снимать / снятый
- Fürsichsein = для-себя-бытие
- Ansichsein = в-себе-бытие
- An-und-Fürsichsein = в-себе-и-для-себя-бытие
- Urteil/Urtheil = суждение
- Schluss/Schluß = умозаключение
"""


def load_env_file(path: Path) -> bool:
    if not path.exists():
        return False

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)
    return True


def env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value else default


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


def ollama_api_url(host: str, path: str) -> str:
    base = host.rstrip("/")
    if base.endswith("/api"):
        base = base[:-4]
    return f"{base}/api/{path.lstrip('/')}"


def ollama_headers(api_key: str = "") -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def is_ollama_cloud_host(host: str) -> bool:
    normalized = host.rstrip("/").removesuffix("/api")
    return normalized == OLLAMA_CLOUD_HOST


def source_or_dir(paths: list[Path], suffix: str = "*.md") -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob(suffix)))
        else:
            files.append(path)
    return files


def language_code(language: str) -> str:
    normalized = language.strip().lower()
    if normalized in LANGUAGE_CODES:
        return LANGUAGE_CODES[normalized]
    slug = re.sub(r"[^a-z0-9]+", "", normalized)
    return slug[:3] or "xx"


def read_glossary(path: Path | None, source_language: str, target_language: str) -> str:
    if path:
        return path.read_text(encoding="utf-8").strip()
    if source_language.lower() == "german" and target_language.lower() == "russian":
        return DEFAULT_DE_RU_GLOSSARY.strip()
    return ""


def build_system_prompt(source_language: str, target_language: str, style: str, glossary: str) -> str:
    prompt = f"""\
You are a careful literary and philosophical translator from {source_language} to {target_language}.

Translate the provided Markdown from {source_language} into {target_language}.

Rules:
- Preserve the original meaning as closely as possible. Do not simplify, summarize, explain, modernize, or add interpretation.
- Keep the author's conceptual distinctions stable. Prefer literal philosophical terminology when the target language remains readable.
- Keep proper names, publisher names, bibliography entries, URLs, ISBNs, and citation metadata conservative; translate only what is semantically necessary.
- Preserve Markdown structure, emphasis, links, headings, lists, block formatting, footnote markers, HTML subscripts/superscripts, and formulas.
- Translate prose, headings, captions, and footnotes. Do not translate URLs, citation keys, page references, mathematical notation, or code.
- Preserve paragraph count and paragraph order.
- {style}
- Return only the translated Markdown. No preface, notes, or commentary."""
    if glossary:
        prompt += "\n\nUse this glossary consistently unless local context clearly requires another established term:\n" + glossary.strip()
    return prompt


def split_blocks(text: str) -> list[str]:
    parts = re.split(r"(\n\s*\n)", text)
    blocks: list[str] = []
    current = ""
    for part in parts:
        current += part
        if re.fullmatch(r"\n\s*\n", part):
            blocks.append(current)
            current = ""
    if current:
        blocks.append(current)
    return blocks


def split_long_block(block: str, max_chars: int) -> list[str]:
    if len(block) <= max_chars:
        return [block]

    pieces: list[str] = []
    current = ""
    sentences = re.split(r"(?<=[.!?。！？])(\s+)", block)
    for idx in range(0, len(sentences), 2):
        sentence = sentences[idx]
        space = sentences[idx + 1] if idx + 1 < len(sentences) else ""
        candidate = sentence + space
        if len(current) + len(candidate) > max_chars and current:
            pieces.append(current)
            current = candidate
        else:
            current += candidate
    if current:
        pieces.append(current)

    final: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            final.append(piece)
            continue
        for start in range(0, len(piece), max_chars):
            final.append(piece[start : start + max_chars])
    return final


def chunk_markdown(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in split_blocks(text):
        for piece in split_long_block(block, max_chars):
            if len(current) + len(piece) > max_chars and current:
                chunks.append(current.rstrip() + "\n")
                current = piece
            else:
                current += piece
    if current.strip():
        chunks.append(current.rstrip() + "\n")
    return chunks


def clean_output(text: str) -> str:
    text = re.sub(r"(?is)^\s*<think>.*?</think>\s*", "", text)
    return text.strip() + "\n"


def output_stem(source: Path) -> str:
    stem = source.stem
    for suffix in ("_from_docx", "_source"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def translation_paths(chunks_dir: Path, source: Path, index: int, source_code: str, target_code: str):
    stem_dir = chunks_dir / source.stem
    return (
        stem_dir / f"{index:04d}.{source_code}.md",
        stem_dir / f"{index:04d}.{target_code}.md",
        stem_dir / f"{index:04d}.json",
    )


def assemble(source: Path, chunks_dir: Path, output_dir: Path, total_chunks: int, source_code: str, target_code: str) -> Path:
    stem_dir = chunks_dir / source.stem
    translated = []
    missing = []
    for idx in range(1, total_chunks + 1):
        _, target_path, _ = translation_paths(chunks_dir, source, idx, source_code, target_code)
        if target_path.exists():
            translated.append(target_path.read_text(encoding="utf-8").rstrip())
        else:
            missing.append(target_path)
    if missing:
        raise RuntimeError(f"Missing {len(missing)} translated chunks under {stem_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{output_stem(source)}_{target_code}.md"
    out_path.write_text("\n\n".join(translated).rstrip() + "\n", encoding="utf-8")
    return out_path


def estimate_tokens(chars: int, output_multiplier: float) -> tuple[int, int]:
    input_tokens = math.ceil(chars / 4)
    output_tokens = math.ceil(input_tokens * output_multiplier)
    return input_tokens, output_tokens


def estimate_usd(input_tokens: int, output_tokens: int, prices: tuple[float, float]) -> float:
    input_per_mtok, output_per_mtok = prices
    return (input_tokens / 1_000_000 * input_per_mtok) + (output_tokens / 1_000_000 * output_per_mtok)


class BedrockProvider:
    name = "bedrock"

    def __init__(self, model_id: str, region: str, system_prompt: str, max_tokens: int, temperature: float, retries: int):
        import boto3
        from botocore.config import Config

        self.model_id = model_id
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(read_timeout=900, connect_timeout=30, retries={"max_attempts": 3}),
        )

    def translate(self, text: str, source_language: str, target_language: str) -> dict[str, Any]:
        user_text = f"Translate this Markdown from {source_language} to {target_language}:\n\n{text}"
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                response = self.client.converse(
                    modelId=self.model_id,
                    system=[{"text": self.system_prompt}],
                    messages=[{"role": "user", "content": [{"text": user_text}]}],
                    inferenceConfig={"maxTokens": self.max_tokens, "temperature": self.temperature},
                )
                content = response["output"]["message"]["content"]
                translated = "\n".join(item["text"] for item in content if "text" in item)
                return {
                    "text": clean_output(translated),
                    "usage": response.get("usage", {}),
                    "stop_reason": response.get("stopReason"),
                    "raw": response,
                }
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self.retries:
                    break
                time.sleep(min(60, 2**attempt))
        raise RuntimeError(f"Bedrock translation failed: {last_error}") from last_error


class OllamaProvider:
    name = "ollama"

    def __init__(self, model_id: str, host: str, api_key: str, system_prompt: str, max_tokens: int, temperature: float, retries: int, timeout: int):
        self.model_id = model_id
        self.host = host.rstrip("/")
        self.api_key = api_key
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.retries = retries
        self.timeout = timeout

    def translate(self, text: str, source_language: str, target_language: str) -> dict[str, Any]:
        user_text = f"Translate this Markdown from {source_language} to {target_language}:\n\n{text}"
        payload = {
            "model": self.model_id,
            "stream": False,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_text},
            ],
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                request = urllib.request.Request(ollama_api_url(self.host, "chat"), data=body, headers=ollama_headers(self.api_key), method="POST")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    data = json.loads(response.read().decode("utf-8"))
                translated = data.get("message", {}).get("content", "")
                if not translated.strip():
                    raise RuntimeError(f"Ollama returned empty content: {data}")
                usage = {
                    "prompt_eval_count": data.get("prompt_eval_count"),
                    "eval_count": data.get("eval_count"),
                    "total_duration": data.get("total_duration"),
                }
                return {
                    "text": clean_output(translated),
                    "usage": {key: value for key, value in usage.items() if value is not None},
                    "stop_reason": data.get("done_reason") or ("done" if data.get("done") else "unknown"),
                    "raw": data,
                }
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == self.retries:
                    break
                time.sleep(min(60, 2**attempt))
        raise RuntimeError(f"Ollama translation failed: {last_error}") from last_error


def make_provider(args: argparse.Namespace, system_prompt: str):
    if args.provider == "bedrock":
        return BedrockProvider(
            model_id=args.model_id,
            region=args.region,
            system_prompt=system_prompt,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            retries=args.retries,
        )
    if args.provider == "ollama":
        return OllamaProvider(
            model_id=args.model_id,
            host=args.ollama_host,
            api_key=args.ollama_api_key,
            system_prompt=system_prompt,
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            retries=args.retries,
            timeout=args.timeout,
        )
    raise ValueError(f"Unsupported provider: {args.provider}")


def translate_one(provider, args: argparse.Namespace, source: Path, chunks_dir: Path, idx: int, total: int, chunk: str):
    _, target_path, meta_path = translation_paths(chunks_dir, source, idx, args.source_code, args.target_code)
    print(f"  {idx:04d}/{total:04d} translate ({len(chunk)} chars)")
    started = time.time()
    result = provider.translate(chunk, args.source_language, args.target_language)
    meta = {
        "source": str(source),
        "chunk": idx,
        "source_chars": len(chunk),
        "translated_chars": len(result["text"]),
        "provider": provider.name,
        "model_id": provider.model_id,
        "source_language": args.source_language,
        "target_language": args.target_language,
        "elapsed_seconds": round(time.time() - started, 2),
        "usage": result.get("usage", {}),
        "stop_reason": result.get("stop_reason"),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    target_path.write_text(result["text"], encoding="utf-8")
    return meta


def translate_file(provider, args: argparse.Namespace, source: Path) -> Path:
    text = source.read_text(encoding="utf-8")
    chunks = chunk_markdown(text, max_chars=args.max_chars)
    if args.limit_chunks is not None:
        chunks = chunks[: args.limit_chunks]

    print(f"{source.name}: {len(chunks)} chunks")
    pending: list[tuple[int, str]] = []
    for idx, chunk in enumerate(chunks, start=1):
        source_path, target_path, _ = translation_paths(args.chunks_dir, source, idx, args.source_code, args.target_code)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(chunk, encoding="utf-8")
        if target_path.exists() and not args.force:
            print(f"  {idx:04d}/{len(chunks):04d} skip existing")
            continue
        pending.append((idx, chunk))

    if args.workers <= 1:
        for idx, chunk in pending:
            meta = translate_one(provider, args, source, args.chunks_dir, idx, len(chunks), chunk)
            print(f"    {idx:04d} done in {meta['elapsed_seconds']}s; stop={meta['stop_reason']}; usage={meta['usage']}")
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(translate_one, provider, args, source, args.chunks_dir, idx, len(chunks), chunk): idx
                for idx, chunk in pending
            }
            for future in as_completed(futures):
                idx = futures[future]
                meta = future.result()
                print(f"    {idx:04d} done in {meta['elapsed_seconds']}s; stop={meta['stop_reason']}; usage={meta['usage']}")

    out_path = assemble(source, args.chunks_dir, args.output_dir, len(chunks), args.source_code, args.target_code)
    print(f"Assembled {out_path}")
    return out_path


def cmd_setup(args: argparse.Namespace) -> int:
    print("Translation pipeline setup")
    print(f"- .env loaded: {'yes' if args.env_loaded else 'no'} ({args.env_file})")
    print(f"- sample env: {'present' if Path('.env_example').exists() else 'missing'} (.env_example)")
    print()
    print("When to OCR")
    print("- If DOCX/text export reads cleanly, skip OCR and translate Markdown directly.")
    print("- If pages are scanned or text has broken ligatures/formulas, use Mistral OCR first, then clean/translate.")
    print()
    print("Provider choice")
    print("- bedrock: better for long book jobs, paid per token, works well with parallel chunks.")
    print("- ollama: local/private and no API token cost, but slower and quality depends on the pulled model.")
    print()

    if args.provider == "ollama":
        cloud_host = is_ollama_cloud_host(args.ollama_host)
        print("Ollama check")
        print(f"- mode: {'direct cloud API' if cloud_host else 'local Ollama API'}")
        print(f"- ollama binary: {shutil.which('ollama') or 'not found'}")
        print(f"- host: {args.ollama_host}")
        print(f"- model: {args.model_id}")
        if cloud_host:
            print(f"- OLLAMA_API_KEY: {'set' if args.ollama_api_key else 'missing'}")
        try:
            request = urllib.request.Request(ollama_api_url(args.ollama_host, "tags"), headers=ollama_headers(args.ollama_api_key))
            with urllib.request.urlopen(request, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            models = sorted(item.get("name", "") for item in data.get("models", []))
            model_label = "available models" if cloud_host else "installed models"
            print(f"- server: reachable; {model_label}: {', '.join(models) if models else '(none)'}")
            if cloud_host:
                print("- pull model: not needed for direct cloud API")
            elif args.model_id not in models:
                print(f"- pull model: ollama pull {args.model_id}")
        except Exception as exc:  # noqa: BLE001
            print(f"- server: not reachable ({exc})")
            if cloud_host:
                print("- for direct cloud API, set OLLAMA_HOST=https://ollama.com and OLLAMA_API_KEY in .env")
            else:
                print("- start server: ollama serve")
                print(f"- pull model: ollama pull {args.model_id}")
    else:
        print("Bedrock check")
        print(f"- aws cli: {shutil.which('aws') or 'not found'}")
        print(f"- region: {args.region}")
        print(f"- model: {args.model_id}")
        if os.environ.get("AWS_PROFILE"):
            print(f"- AWS_PROFILE: {os.environ['AWS_PROFILE']}")
        print("- verify access: aws bedrock list-foundation-models --region " + args.region)

    print()
    print("Typical commands")
    command_name = Path(sys.argv[0]).name
    command = "python3 scripts/translate_cli.py" if command_name.endswith(".py") else command_name
    print(f"{command} ocr-mistral volume_1.pdf --pages 0-9")
    print(f"{command} estimate docx_md_clean")
    print(f"{command} translate docx_md_clean/volume_1_from_docx.md --limit-chunks 1 --force")
    print(f"{command} to-docx md_ru/volume_1_ru.md --output-dir docx_ru")
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    files = source_or_dir(args.paths or [Path("docx_md_clean")])
    rows = []
    total_chars = 0
    total_words = 0
    for file in files:
        if not file.exists():
            print(f"Missing source: {file}", file=sys.stderr)
            continue
        text = file.read_text(encoding="utf-8")
        chars = len(text)
        words = len(text.split())
        input_tokens, output_tokens = estimate_tokens(chars, args.output_multiplier)
        rows.append((file.name, words, chars, input_tokens, output_tokens))
        total_chars += chars
        total_words += words

    total_input, total_output = estimate_tokens(total_chars, args.output_multiplier)
    print("Source files")
    print("file,words,chars,input_tokens_est,output_tokens_est")
    for row in rows:
        print(",".join(str(value) for value in row))
    print(f"TOTAL,{total_words},{total_chars},{total_input},{total_output}")
    print()

    print("Estimated translation cost")
    print("model,input_per_mtok,output_per_mtok,cost_usd")
    if args.provider == "ollama":
        print(f"{args.model_id},0.0000,0.0000,0.00")
        print("Note: Ollama has no API token bill, but uses local compute and will be much slower on large volumes.")
    else:
        for name, prices in BEDROCK_PRICE_MODELS.items():
            print(f"{name},{prices[0]:.4f},{prices[1]:.4f},{estimate_usd(total_input, total_output, prices):.2f}")
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    glossary = read_glossary(args.glossary_file, args.source_language, args.target_language)
    system_prompt = build_system_prompt(args.source_language, args.target_language, args.style, glossary)
    provider = make_provider(args, system_prompt)
    sources = source_or_dir(args.sources)
    if not sources:
        raise SystemExit("No Markdown sources found.")
    for source in sources:
        if not source.exists():
            print(f"Missing source: {source}", file=sys.stderr)
            continue
        translate_file(provider, args, source)
    return 0


def cmd_ocr_mistral(args: argparse.Namespace) -> int:
    try:
        from .mistral_ocr_to_md import DEFAULT_MODEL, Mistral, natural_key, ocr_pdf
    except ImportError:
        from mistral_ocr_to_md import DEFAULT_MODEL, Mistral, natural_key, ocr_pdf

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY is missing. Put it in .env or pass --env-file with a populated env file.")
    if Mistral is None:
        raise SystemExit("mistralai is missing. Install OCR dependencies with: python3 -m pip install -r requirements.txt")

    pdfs = args.pdfs or sorted(Path.cwd().glob("*.pdf"), key=natural_key)
    if not pdfs:
        raise SystemExit("No PDFs found.")

    model = args.model or os.environ.get("MISTRAL_OCR_MODEL", DEFAULT_MODEL)
    client = Mistral(api_key=api_key, timeout_ms=args.timeout_ms)
    for pdf in pdfs:
        suffix = "_pages_" + args.pages.replace(",", "_").replace("-", "-") if args.pages else ""
        out_md = args.out_dir / f"{pdf.stem}{suffix}.md"
        out_json = args.raw_dir / f"{pdf.stem}{suffix}.json"
        ocr_pdf(client, pdf, out_md, out_json, args.pages, args.chunk_pages, args.timeout_ms, args.keep_upload, model)
    return 0


def cmd_to_docx(args: argparse.Namespace) -> int:
    pandoc = shutil.which("pandoc")
    if not pandoc:
        raise SystemExit("pandoc is required for DOCX export.")

    sources = source_or_dir(args.sources)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        if not source.exists():
            print(f"Missing source: {source}", file=sys.stderr)
            continue
        out_path = args.output_dir / f"{source.stem}.docx"
        command = [
            pandoc,
            str(source),
            "--from=gfm+footnotes+tex_math_dollars",
            "--to=docx",
            "-o",
            str(out_path),
        ]
        if args.reference_doc:
            command.insert(-2, f"--reference-doc={args.reference_doc}")
        subprocess.run(command, check=True)
        print(f"Wrote {out_path}")
    return 0


def add_common_translation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", choices=["bedrock", "ollama"], default=env_str("TRANSLATION_PROVIDER", "bedrock"))
    parser.add_argument("--source-language", default=env_str("SOURCE_LANGUAGE", "German"))
    parser.add_argument("--target-language", default=env_str("TARGET_LANGUAGE", "Russian"))
    parser.add_argument("--source-code", default=env_str("SOURCE_LANGUAGE_CODE", language_code(env_str("SOURCE_LANGUAGE", "German"))))
    parser.add_argument("--target-code", default=env_str("TARGET_LANGUAGE_CODE", language_code(env_str("TARGET_LANGUAGE", "Russian"))))
    parser.add_argument("--style", default=env_str("TRANSLATION_STYLE", "Stay close to the source syntax and terminology without becoming unreadable."))
    parser.add_argument("--glossary-file", type=Path, default=Path(os.environ["TRANSLATION_GLOSSARY_FILE"]) if os.environ.get("TRANSLATION_GLOSSARY_FILE") else None)
    parser.add_argument("--region", default=env_str("BEDROCK_REGION", env_str("AWS_REGION", "us-east-1")))
    parser.add_argument("--ollama-host", default=env_str("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
    parser.add_argument("--ollama-api-key", default=env_str("OLLAMA_API_KEY", ""))
    parser.add_argument("--model-id", default=env_str("MODEL_ID", ""))
    parser.add_argument("--max-chars", type=int, default=env_int("TRANSLATION_MAX_CHARS", 18_000))
    parser.add_argument("--max-tokens", type=int, default=env_int("TRANSLATION_MAX_TOKENS", 9_000))
    parser.add_argument("--temperature", type=float, default=env_float("TRANSLATION_TEMPERATURE", 0.1))
    parser.add_argument("--retries", type=int, default=env_int("TRANSLATION_RETRIES", 4))
    parser.add_argument("--timeout", type=int, default=env_int("TRANSLATION_TIMEOUT_SECONDS", 900))


def build_parser(env_file: Path, env_loaded: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable OCR/Markdown translation pipeline.")
    parser.add_argument("--env-file", type=Path, default=env_file)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Show provider guidance and local readiness checks.")
    add_common_translation_args(setup)
    setup.set_defaults(func=cmd_setup, env_loaded=env_loaded)

    estimate = subparsers.add_parser("estimate", help="Estimate translation tokens and rough provider cost.")
    estimate.add_argument("paths", nargs="*", type=Path, help="Markdown files or directories. Defaults to docx_md_clean.")
    estimate.add_argument("--output-multiplier", type=float, default=env_float("TRANSLATION_OUTPUT_MULTIPLIER", 1.15))
    estimate.add_argument("--provider", choices=["bedrock", "ollama"], default=env_str("TRANSLATION_PROVIDER", "bedrock"))
    estimate.add_argument("--model-id", default=env_str("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL))
    estimate.set_defaults(func=cmd_estimate, env_loaded=env_loaded)

    translate = subparsers.add_parser("translate", help="Translate Markdown files with Bedrock or Ollama.")
    translate.add_argument("sources", nargs="+", type=Path, help="Markdown files or directories.")
    add_common_translation_args(translate)
    translate.add_argument("--output-dir", type=Path, default=Path(env_str("TRANSLATION_OUTPUT_DIR", "md_ru")))
    translate.add_argument("--chunks-dir", type=Path, default=Path(env_str("TRANSLATION_CHUNKS_DIR", "translation_chunks")))
    translate.add_argument("--limit-chunks", type=int, default=None, help="Translate only first N chunks per file for testing.")
    translate.add_argument("--force", action="store_true", help="Retranslate chunks even if target chunk files already exist.")
    translate.add_argument("--workers", type=int, default=env_int("TRANSLATION_WORKERS", 1))
    translate.set_defaults(func=cmd_translate, env_loaded=env_loaded)

    ocr = subparsers.add_parser("ocr-mistral", help="Run Mistral OCR on PDFs and write page-marked Markdown.")
    ocr.add_argument("pdfs", nargs="*", type=Path, help="PDF files. Defaults to all PDFs in cwd.")
    ocr.add_argument("--out-dir", type=Path, default=Path(env_str("MISTRAL_OCR_OUTPUT_DIR", "mistral_ocr_md")))
    ocr.add_argument("--raw-dir", type=Path, default=Path(env_str("MISTRAL_OCR_RAW_DIR", "mistral_ocr_md/raw")))
    ocr.add_argument("--pages", help="Zero-based page indexes/ranges, e.g. '10' or '0-9,20'.")
    ocr.add_argument("--chunk-pages", type=int, default=env_int("MISTRAL_OCR_CHUNK_PAGES", 100))
    ocr.add_argument("--timeout-ms", type=int, default=env_int("MISTRAL_OCR_TIMEOUT_MS", 600_000))
    ocr.add_argument("--model", default=env_str("MISTRAL_OCR_MODEL", ""))
    ocr.add_argument("--keep-upload", action="store_true")
    ocr.set_defaults(func=cmd_ocr_mistral, env_loaded=env_loaded)

    to_docx = subparsers.add_parser("to-docx", help="Convert translated Markdown files to DOCX with pandoc.")
    to_docx.add_argument("sources", nargs="+", type=Path, help="Markdown files or directories.")
    to_docx.add_argument("--output-dir", type=Path, default=Path(env_str("DOCX_OUTPUT_DIR", "docx_out")))
    to_docx.add_argument("--reference-doc", type=Path, default=Path(os.environ["PANDOC_REFERENCE_DOC"]) if os.environ.get("PANDOC_REFERENCE_DOC") else None)
    to_docx.set_defaults(func=cmd_to_docx, env_loaded=env_loaded)
    return parser


def normalize_provider_defaults(args: argparse.Namespace) -> None:
    if getattr(args, "provider", None) == "ollama":
        if not args.model_id:
            args.model_id = env_str("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)
    elif getattr(args, "provider", None) == "bedrock":
        if not args.model_id:
            args.model_id = env_str("BEDROCK_MODEL_ID", DEFAULT_BEDROCK_MODEL)


def main() -> int:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--env-file", type=Path, default=Path(".env"))
    pre_args, _ = pre_parser.parse_known_args()
    env_loaded = load_env_file(pre_args.env_file)
    parser = build_parser(pre_args.env_file, env_loaded)
    args = parser.parse_args()
    normalize_provider_defaults(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
