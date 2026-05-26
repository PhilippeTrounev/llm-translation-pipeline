# Getting API Keys

This guide is for someone who wants to use the translation pipeline without running large models locally.

For the normal cloud workflow, you only need:

1. An Ollama API key for translation.
2. A Mistral API key only if you need OCR for scanned or badly extracted PDFs.

AWS Bedrock credentials are optional. Use them only if you want to translate through AWS instead of Ollama.

## 1. Install the CLI

Install from the public repository.

### macOS

```bash
curl -fsSL https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/install/macos.sh | bash
```

### Linux

```bash
curl -fsSL https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/install/linux.sh | bash
```

### Windows PowerShell

```powershell
iwr -useb https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/install/windows.ps1 | iex
```

Restart the terminal if the `llm-translate` command is not found immediately.

Check the install:

```bash
llm-translate --help
```

## 2. Create a Project Folder

Create a folder for the documents you want to translate.

```bash
mkdir translation-project
cd translation-project
```

Download the sample environment file:

```bash
curl -fsSLO https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/.env_example
cp .env_example .env
```

On Windows PowerShell:

```powershell
mkdir translation-project
cd translation-project
iwr -useb https://raw.githubusercontent.com/PhilippeTrounev/llm-translation-pipeline/main/.env_example -OutFile .env_example
copy .env_example .env
```

Open `.env` in a text editor. Do not send this file to anyone and do not commit it to git.

## 3. Get an Ollama API Key

Use Ollama Cloud when you have an Ollama subscription or API access and do not want to download local models.

1. Sign in at <https://ollama.com>.
2. Open the API keys page: <https://ollama.com/settings/keys>.
3. Create a new API key.
4. Copy the key.
5. Paste it into `.env`.

Use this configuration:

```bash
TRANSLATION_PROVIDER=ollama
OLLAMA_HOST=https://ollama.com
OLLAMA_API_KEY=paste_your_ollama_key_here
OLLAMA_MODEL=gpt-oss:120b
```

Important:

- Direct Ollama Cloud API mode uses `https://ollama.com/api`.
- It does not require `ollama pull`.
- It does not require a powerful local machine.
- The local computer only sends text chunks to the API and saves the result.

Test it:

```bash
llm-translate setup --provider ollama --ollama-host https://ollama.com --model-id gpt-oss:120b
```

If the setup output says `OLLAMA_API_KEY: set`, the key is visible to the CLI.

## 4. Get a Mistral API Key, Only If OCR Is Needed

You need Mistral only when the PDF is scanned, image-based, or text extraction produces bad text.

If the document is already clean Markdown, DOCX, or selectable/copyable text, you can skip this section.

To create a Mistral key:

1. Open Mistral Studio API keys: <https://console.mistral.ai/api-keys>.
2. Click **Create new key**.
3. Give it a name such as `translation-ocr`.
4. Set an expiration date if you want one.
5. Create the key and copy it immediately.
6. Paste it into `.env`.

Use this configuration:

```bash
MISTRAL_API_KEY=paste_your_mistral_key_here
MISTRAL_OCR_MODEL=mistral-ocr-latest
```

Important:

- Mistral shows the full key only once.
- Store it in a password manager.
- Do not email the key or put it in GitHub.

Test OCR on a small page range first:

```bash
llm-translate ocr-mistral book.pdf --pages 0-2 --out-dir ocr_md
```

## 5. Optional: AWS Bedrock Instead of Ollama

Most users do not need this if Ollama Cloud is working.

Use Bedrock only if you want AWS-hosted translation models instead of Ollama. You need:

- an AWS account;
- AWS CLI installed and configured;
- permission to use Amazon Bedrock;
- access to the model you want to use.

Recommended `.env` values:

```bash
TRANSLATION_PROVIDER=bedrock
AWS_PROFILE=your_aws_profile
AWS_REGION=us-east-1
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=qwen.qwen3-32b-v1:0
```

Check AWS access:

```bash
aws bedrock list-foundation-models --region us-east-1
llm-translate setup --provider bedrock
```

Security notes:

- Do not use AWS root credentials.
- Do not put AWS access keys in project files.
- Prefer an AWS profile or temporary credentials.

## 6. Final `.env` Example for Ollama Cloud

For translation through Ollama Cloud, with optional Mistral OCR:

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

OLLAMA_HOST=https://ollama.com
OLLAMA_MODEL=gpt-oss:120b
OLLAMA_API_KEY=paste_your_ollama_key_here

MISTRAL_API_KEY=paste_your_mistral_key_here_if_ocr_is_needed
MISTRAL_OCR_MODEL=mistral-ocr-latest
```

## 7. First Translation Test

Put a small Markdown file in the project folder, for example `sample.md`.

Run one chunk first:

```bash
llm-translate translate sample.md --provider ollama --limit-chunks 1 --force
```

If the result looks acceptable, run the full document:

```bash
llm-translate translate sample.md --provider ollama
```

Convert Markdown output to DOCX if Pandoc is installed:

```bash
llm-translate to-docx md_ru --output-dir docx_ru
```

## 8. What Not To Share

Never share:

- `.env`
- API keys
- AWS credentials
- generated credential CSV files
- private source documents, unless you have permission

It is safe to share:

- `.env_example`
- command output that does not contain keys
- error messages after removing any key values

