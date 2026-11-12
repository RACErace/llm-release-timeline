"""Collection policy for first-party releases of language foundation models."""

import re
import unicodedata
from urllib.parse import urlsplit


LANGUAGE_TASKS = {
    "text-generation", "text2text-generation", "fill-mask",
    "image-text-to-text", "audio-text-to-text", "any-to-any",
}
EXCLUDED_NAME = re.compile(
    r"(?<![a-z0-9])(?:adapters?|peft|lora|qlora|gguf|ggml|gptq|awq|mlx|bnb|"
    r"bitsandbytes|exl2|nvfp4|fp[48]|int[248]|[248][- ]?bit|q[2-8](?:_[a-z0-9]+)*|"
    r"quantiz\w*|quantis\w*|distill\w*|merg(?:e|ed|ekit)|fine[- ]?tun\w*|instruct(?:ion[- ]tuned)?|"
    r"sft|dpo|rlhf|rerank\w*|embeddings?|reward[- ]model|draft(?:er)?|"
    r"asr|tts|ocr|text[- ]to[- ](?:image|video|speech)|image[- ](?:generation|edit)|"
    r"speech[- ](?:recognition|synthesis)|chat)(?![a-z0-9])|"
    r"(?<=[\dBb-])[-_]it(?:$|[-_])|微调(?:版|模型|权重)|量化(?:版|包|权重)|"
    r"蒸馏(?:版|模型)|(?:合并|适配器)权重|向量模型|重排模型",
    re.I,
)
NON_RELEASE = re.compile(
    r"\b(?:benchmarks?|leaderboards?|surveys?|datasets?|watermarking|evaluating|"
    r"evaluation of|scaling laws?|fine[- ]tuning (?:service|API|support))\b|"
    r"基准(?:测试|评测)|数据集|评测榜单", re.I,
)
# These are assertions about a checkpoint's origin, not training tips, usage
# examples, comparisons with another model, or an architecture it implements.
DERIVED_CLAIM = re.compile(
    r"\b(?:is|was|are|were|has been)\s+(?:(?:an?|the|our|new|specialized|domain[- ]specific)\s+){0,4}"
    r"(?:fine[- ]?tuned|instruction[- ]tuned|distilled|quantized|merged|adapted from)\b|"
    r"\b(?:fine[- ]?tun(?:ed|ing)|distill(?:ed|ing)|merg(?:ed|ing)|continu(?:ed|ing)[- ]pre[- ]?training)"
    r"\s+(?:on |from |of )?(?:the |a )?(?:[\w.-]+/[\w.-]+|(?:Qwen|Llama|Gemma|Mistral|DeepSeek|GLM)[-.\d])|"
    r"\b(?:model|LLM|checkpoint|assistant)\s+(?:is\s+)?(?:built on|based on|derived from|initialized from)\s+"
    r"(?:the\s+)?(?:[\w.-]+/[\w.-]+|[A-Za-z][\w.-]*\d|Llama\b|Gemma\b|Mistral\b)|"
    r"\b(?:uses?|using)\s+(?:an? |the )?(?:pre[- ]?trained )?(?:language )?(?:backbone|base model)\b|"
    r"(?:基于|采用|使用|沿用|初始化自)\s*(?:[A-Za-z][\w.-]*/[\w.-]+|"
    r"[A-Za-z][A-Za-z.\- ]{0,28}\d[\w.-]*)(?![\w.-]|\s*(?:架构|结构|训练框架))|"
    r"[A-Za-z][\w.-]*\d[\w.-]*\s*(?:为|作为)?\s*(?:底座|基座|骨干)|合并\s*(?:LoRA|适配器)|"
    r"(?:基于|在).{1,55}(?:基础上|模型上)?(?:进行|通过|经过)?(?:微调|蒸馏|继续预训练)|"
    r"(?:通过|由).{0,45}(?:微调|蒸馏|量化|合并)(?:得到|而成|训练而成)", re.I,
)
BASE_EVIDENCE = re.compile(
    r"\b(?:pre[- ]?train(?:ed|ing)\s+(?:from scratch|on|with|using|over)|"
    r"train(?:ed|ing)\s+from scratch|train(?:ed|ing)\s+on\s+(?:a\s+)?(?:causal|next[- ]token|masked language)\b|"
    r"train(?:ed|ing)\s+(?:on|with)\s+(?:about|approximately|over|~)?\s*[\d,.]+\s*(?:trillion|billion|[tb]n?|gt)\b|"
    r"(?:pre[- ]?trained|base|foundation)\s+(?:generative |large )?(?:text |language )?model\b|"
    r"training stage\s*:\s*pre[- ]?training)|从零(?:开始)?(?:预)?训练|(?:自主|独立|从头)预训练",
    re.I,
)


def normalized(text):
    return unicodedata.normalize("NFKC", text).replace("‑", "-").replace("_", "-")


def excluded_name(name):
    return bool(EXCLUDED_NAME.search(normalized(name)))


def paper_url(url):
    target = urlsplit(url)
    host = (target.hostname or "").lower()
    return (
        host in {"arxiv.org", "openreview.net", "aclanthology.org", "doi.org"}
        or host.endswith(".arxiv.org")
        or (host == "huggingface.co" and target.path.startswith("/papers/"))
    )


def announcement_in_scope(title):
    return not (excluded_name(title) or NON_RELEASE.search(title) or DERIVED_CLAIM.search(title))


def check_new_record(row, *, historical_source=False):
    if not announcement_in_scope(row["name"]) or DERIVED_CLAIM.search(row["note"]):
        raise ValueError("仅收录发布方的 LLM 基模型，请核对模型谱系与预训练依据")
    if not historical_source and all(paper_url(row[k]) for k in ("url", "url2") if row.get(k)):
        raise ValueError("新增记录须提供发布方公告、模型卡或官方模型仓，论文可作补充证据")


def hub_metadata_in_scope(row):
    if excluded_name(row["id"].split("/", 1)[-1]):
        return False
    tags = {t.casefold() for t in row.get("tags") or ()}
    card = row.get("cardData") or {}
    if (
        card.get("base_model") or row.get("base_model") or row.get("base_model_relation")
        or any(t.startswith("base_model:") for t in tags)
        or {"peft", "adapter-transformers", "gguf", "mergekit", "quantized"} & tags
    ):
        return False
    tasks = {row["pipeline_tag"]} if row.get("pipeline_tag") else set(row.get("tasks") or ())
    if tasks and not tasks.intersection(LANGUAGE_TASKS):
        return False
    return not any(
        f["rfilename"].rsplit("/", 1)[-1] == "adapter_config.json"
        for f in row.get("siblings") or ()
    )


def base_training_evidence(row, card):
    # A family card often also documents instruction-tuned siblings. The
    # checkpoint's own training-stage field takes precedence over family prose.
    if card.startswith("---") and re.search(r"^base_model\s*:", card.split("---", 2)[1], re.M):
        return ""
    quant = (row.get("config") or {}).get("quantization_config")
    if quant:
        precision = str(quant.get("quant_method") or "")
        if not re.fullmatch(r"fp[48]", precision, re.I) or not re.search(
            r"\b" + precision + r"(?: mixed[- ]precision)? training\b|\btrained (?:natively )?in " + precision,
            card, re.I,
        ):
            return ""
    # Shared release cards may describe an explicitly named base checkpoint
    # and a post-trained sibling whose repository name has no "Instruct" suffix.
    name = row["id"].split("/", 1)[1]
    if re.search(re.escape(name) + r"[-_]base\b", card, re.I) and re.search(
        r"post[- ]training|instruction[- ]tuning|chat model", card, re.I
    ):
        return ""
    card = re.sub(r"```.*?(?:```|$)", "", card, flags=re.S)
    card = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", card).replace("**", "")
    stage = re.search(r"\bTraining Stage\s*:\s*([^\n|]+)", card, re.I)
    if stage and re.search(r"post[- ]?training|fine[- ]?tun|distill|align|reinforcement", stage[1], re.I):
        return ""
    if not stage and DERIVED_CLAIM.search(card):
        return ""
    tasks = {row.get("pipeline_tag")} | set(row.get("tasks") or ())
    if not tasks.intersection(LANGUAGE_TASKS) and not re.search(
        r"\b(?:language model(?:s|ing)?|LLMs?|causal decoder|next[- ]token)\b|语言模型", card, re.I
    ):
        return ""
    for line in card.splitlines():
        if match := BASE_EVIDENCE.search(line):
            start = max(0, match.start() - 65)
            return line[start : match.end() + 100].strip()[:260]
    return ""
