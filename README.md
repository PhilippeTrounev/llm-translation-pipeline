# LLM Translation Pipeline

A reusable command-line pipeline for turning long PDFs or document exports into translated Markdown and DOCX files.

The tool is designed for book-length translation work where you want resumable chunks, conservative prompts, model/provider switching, and a clean path from OCR or extracted text to final `.docx`.

## What It Does

- Estimates rough token usage and provider cost before you run a full translation.
- Translates Markdown in resumable chunks with checkpoint files.
- Supports AWS Bedrock models and Ollama through either local Ollama or direct Ollama Cloud API.
- Runs Mistral OCR when PDFs are scanned or normal text extraction is bad.
- Converts translated Markdown to DOCX with Pandoc.
- Loads configuration from `.env` without printing secrets.

## What It Does Not Include

This repository intentionally does not include source PDFs, DOCX files, generated translations, OCR outputs, or `.env` secrets. Keep those in your own project directory.

## Installation

Python 3.10+ is required.

### macOS

```bash
curl -fsSL https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/install/macos.sh | bash
```

For DOCX export, install Pandoc:

```bash
brew install pandoc
```

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/install/linux.sh | bash
```

For DOCX export, install Pandoc with your package manager, for example:

```bash
sudo apt-get install pandoc
```

### Windows PowerShell

```powershell
iwr -useb https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/install/windows.ps1 | iex
```

For DOCX export, install Pandoc from <https://pandoc.org/installing.html>.

### From Source

```bash
git clone https://github.com/PhilippeTrounev/llm-translation-pipeline.git
cd llm-translation-pipeline
python3 -m pip install -e ".[all]"
```

Then verify:

```bash
llm-translate --help
llm-translate setup
```

You can also run the CLI directly from a source checkout without installing it:

```bash
python3 setup_cli.py --help
python3 setup_cli.py setup
```

## Configuration

Create a `.env` file in the directory where you run the CLI:

```bash
curl -fsSLO https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/.env_example
cp .env_example .env
```

The important variables are:

```bash
TRANSLATION_PROVIDER=ollama
SOURCE_LANGUAGE=German
TARGET_LANGUAGE=Russian
SOURCE_LANGUAGE_CODE=de
TARGET_LANGUAGE_CODE=ru
TRANSLATION_OUTPUT_DIR=md_ru
TRANSLATION_CHUNKS_DIR=translation_chunks
TRANSLATION_MAX_CHARS=18000
TRANSLATION_MAX_TOKENS=9000
TRANSLATION_TEMPERATURE=0.1
```

Never commit `.env`.

For a step-by-step guide to creating the needed API keys, see [docs/GETTING_KEYS.md](docs/GETTING_KEYS.md).

## Provider Setup

### Ollama Cloud API

Use this when you have an Ollama subscription/API key and do not want to pull local models.

```bash
TRANSLATION_PROVIDER=ollama
OLLAMA_HOST=https://ollama.com
OLLAMA_API_KEY=your_api_key_here
OLLAMA_MODEL=gpt-oss:120b
```

Run:

```bash
llm-translate setup --provider ollama --ollama-host https://ollama.com --model-id gpt-oss:120b
llm-translate translate input.md --provider ollama --ollama-host https://ollama.com --model-id gpt-oss:120b --limit-chunks 1 --force
```

### Local Ollama

Use this when you want local/private inference.

```bash
ollama serve
ollama pull qwen2.5:14b-instruct
```

`.env`:

```bash
TRANSLATION_PROVIDER=ollama
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:14b-instruct
```

### AWS Bedrock

Use this for reliable full-volume runs with AWS-managed models.

```bash
TRANSLATION_PROVIDER=bedrock
AWS_PROFILE=your_profile
AWS_REGION=us-east-1
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=qwen.qwen3-32b-v1:0
```

Check AWS access:

```bash
aws bedrock list-foundation-models --region us-east-1
```

### Mistral OCR

Use OCR only when a PDF is scanned or normal text extraction produces broken text.

```bash
MISTRAL_API_KEY=your_key_here
MISTRAL_OCR_MODEL=mistral-ocr-latest
```

## Typical Workflow

If you already have clean Markdown from DOCX, skip OCR:

```bash
llm-translate estimate ./markdown
llm-translate translate ./markdown/book.md --limit-chunks 1 --force
llm-translate translate ./markdown --workers 4
llm-translate to-docx ./md_ru --output-dir ./docx_ru
```

If you need OCR:

```bash
llm-translate ocr-mistral ./book.pdf --pages 0-9 --out-dir ./ocr_md
llm-translate translate ./ocr_md/book_pages_0-9.md --limit-chunks 1 --force
```

## Commands

```bash
llm-translate setup
llm-translate estimate PATH...
llm-translate translate PATH...
llm-translate ocr-mistral PDF...
llm-translate to-docx PATH...
```

Use `--help` on any command for flags:

```bash
llm-translate translate --help
```

## Translation Prompt Behavior

The default prompt asks the model to:

- preserve meaning closely;
- avoid summaries, explanations, invented material, and modernization;
- preserve Markdown, links, footnotes, emphasis, formulas, HTML subscripts/superscripts, and paragraph order;
- translate prose, headings, captions, and footnotes;
- leave URLs, citation keys, page references, mathematical notation, and code conservative.

German to Russian includes a default philosophical glossary. For other language pairs, pass your own glossary:

```bash
llm-translate translate input.md --source-language German --target-language English --target-code en --glossary-file glossary.md
```

## Cost Estimates

The estimate command uses a rough character-to-token approximation and an output multiplier. It is meant for planning, not billing reconciliation.

```bash
llm-translate estimate ./markdown --provider bedrock
llm-translate estimate ./markdown --provider ollama
```

Ollama Cloud subscription usage depends on your plan. Local Ollama has no API token bill but uses local compute.

## Resuming and Checkpoints

Translations are stored per chunk under `TRANSLATION_CHUNKS_DIR`. If a run stops, re-run the same command and existing target chunks are skipped. Use `--force` to overwrite them.

The assembled file is written to `TRANSLATION_OUTPUT_DIR`.

## Security Notes

- `.env` is ignored by git and should never be committed.
- Do not publish copyrighted source texts or generated translations unless you have the right to do so.
- Keep large OCR/chunk/output folders out of public repos.

## License

MIT
