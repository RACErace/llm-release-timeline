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
    r"quantiz\w*|quantis\w*|distill\w*|merg(?:e|ed|ekit)|fine[- ]?tun\w*|"
    r"checkpoint[- ]?\d+|epoch[- ]?\d+|rerank\w*|embeddings?|reward[- ]model|draft(?:er)?|"
    r"asr|tts|ocr|text[- ]to[- ](?:image|video|speech)|image[- ](?:generation|edit)|"
    r"speech[- ](?:recognition|synthesis))(?![a-z0-9])|"
    r"量化(?:版|包|权重)|"
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
    r"\b(?:by|from) merging\s+(?:(?:multiple|several|two|the|trained|\d+)\s+)*"
    r"(?:LoRA\b|adapters?\b|checkpoints?\b)|"
    r"(?:基于|采用|使用|沿用|初始化自)\s*(?:[A-Za-z][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+|"
    r"[A-Za-z][A-Za-z.\- ]{0,28}\d[A-Za-z0-9_.-]*)(?![A-Za-z0-9_.-]|\s*(?:的\s*)?(?:架构|结构|训练框架))|"
    r"[A-Za-z][\w.-]*\d[\w.-]*\s*(?:为|作为)?\s*(?:底座|基座|骨干)|合并\s*(?:LoRA|适配器)|"
    r"(?:通过|由)合并[^，。；\n]{0,32}(?:LoRA|适配器|权重|检查点)[^，。；\n]{0,12}(?:得到|而成|生成)|"
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
    return not (excluded_name(title) or NON_RELEASE.search(title) or REPACK.search(title)) and (
        not DERIVED_CLAIM.search(title) or describes_own_family({"name": title}, title, ())
    )


def family_name(name):
    # Official repositories sometimes spell Llama as Meta-Llama in Hub metadata.
    name = re.sub(r"^Meta-Llama", "Llama", normalized(name), flags=re.I)
    match = re.match(r"[^\W\d_]+", name)
    return match[0].casefold() if match else ""


NAMED_PARENT = re.compile(
    r"(?:基于|沿用|源自|采用|使用|初始化自|基础模型为|底座为|based on|derived from|fine[- ]?tuned from|built on)"
    r"\s*(?:(?:其|此前发布的|the |our )\s*)*(?:[\w.-]+/)?(?P<family>[a-z]+)"
    r"(?=[\s.\-]*\d|[\s-]+(?:base|模型|底座))", re.I,
)
PARENT_BACKBONE = re.compile(
    r"(?P<family>[a-z]+)[ .-]*\d+[a-z0-9_.-]*\s*(?:为|作为)?\s*(?:底座|基座|骨干)",
    re.I,
)


def describes_own_family(row, text, catalogue):
    if catalogue is None:  # the prepare/merge preflight has not read the base tree yet
        return True
    family = family_name(row["name"])
    parents = {
        m["family"].casefold()
        for pattern in (NAMED_PARENT, PARENT_BACKBONE) for m in pattern.finditer(text)
    }
    related = parents or {family}
    known = {(family_name(r["name"]), r["org"]) for r in catalogue}
    for parent in related:
        owners = {org for name, org in known if name == parent}
        if owners and row.get("org") not in owners:
            return False
        if not owners and not (parents and parent == family):
            return False
    return True


REPACK = re.compile(
    r"(?:合并.{0,16}(?:LoRA|适配器|权重)|(?:蒸馏|量化)(?:版|包|而成|得到))|"
    r"\b(?:by merging|quantized (?:from|version of)|distilled from)\b", re.I,
)


def check_new_record(row, *, historical_source=False, catalogue=()):
    text = row["name"] + "。" + row["note"]
    if excluded_name(row["name"]) or NON_RELEASE.search(row["name"]) or REPACK.search(text) or (
        DERIVED_CLAIM.search(text) and not describes_own_family(row, text, catalogue)
    ):
        raise ValueError("请核对是否为语言基模型或原开发方正式发布的同族版本")
    if not historical_source and all(paper_url(row[k]) for k in ("url", "url2") if row.get(k)):
        raise ValueError("新增记录须提供发布方公告、模型卡或官方模型仓，论文可作补充证据")


def hub_parents(row):
    parents = set()
    relations = set()
    for value in ((row.get("cardData") or {}).get("base_model"), row.get("base_model")):
        if value:
            parents.update([value] if isinstance(value, str) else value)
    for tag in row.get("tags") or ():
        if tag.startswith("base_model:"):
            *kind, parent = tag.split(":")[1:]
            parents.add(parent)
            relations.update(kind)
    if relation := row.get("base_model_relation"):
        relations.add(relation)
    return parents, {kind.casefold() for kind in relations}


def own_hub_family(repo, parents):
    owner, name = repo.split("/", 1)
    family = family_name(name)
    parent_names = set()
    for parent in parents:
        parent_owner, sep, parent_name = parent.partition("/")
        if not sep or parent_owner.casefold() != owner.casefold() or family_name(parent_name) != family:
            return False
        parent_names.add(re.sub(r"^meta-llama", "llama", parent_name.casefold()))
    return bool(family) and len(parent_names) == 1


def hub_metadata_in_scope(row):
    if excluded_name(row["id"].split("/", 1)[-1]):
        return False
    tags = {t.casefold() for t in row.get("tags") or ()}
    parents, relations = hub_parents(row)
    if (
        ((parents or relations) and not own_hub_family(row["id"], parents))
        or relations - {"finetune", "fine-tune", "pretrain"}
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


def documented_version(repo, card):
    """Identify the named release on a shared publisher card that also lists siblings."""
    owner, name = repo.split("/", 1)
    family = family_name(name)
    if not family:
        return ""
    family_base = re.escape(family) + r"[-.\d\w]*[- ]base\b"
    hub = r"https://(?:huggingface\.co/|modelscope\.cn/models/)"
    own_base = re.search(hub + re.escape(owner) + "/" + family_base, card, re.I)
    branded_release = (
        family_name(owner) == family
        and re.search(hub + re.escape(repo) + r"(?![\w.-])", card, re.I)
        and re.search(r"\b(?:based on|trained from)\s+" + family_base, card, re.I)
    )
    if own_base or branded_release:
        for line in card.splitlines():
            if re.search(r"\bwe (?:introduce|present|release)\b", line, re.I) and re.search(
                r"(?<![\w.-])" + re.escape(name) + r"(?![\w-]|\.\d)", line, re.I
            ):
                return line.strip()[:260]
    return ""


def hub_release_evidence(row, card):
    tasks = {row.get("pipeline_tag")} | set(row.get("tasks") or ())
    if not tasks.intersection(LANGUAGE_TASKS) and not re.search(
        r"\b(?:language model(?:s|ing)?|LLMs?|causal decoder|next[- ]token)\b|语言模型", card, re.I
    ):
        return ""
    launch = documented_version(row["id"], card)
    quant = (row.get("config") or {}).get("quantization_config")
    if quant:
        precision = str(quant.get("quant_method") or "")
        if not re.fullmatch(r"fp[48]", precision, re.I):
            return ""
        if not launch and not re.search(
            r"\b" + precision + r"(?: mixed[- ]precision)? training\b|\btrained (?:natively )?in " + precision,
            card, re.I,
        ):
            return ""
    parents, _ = hub_parents(row)
    if own_hub_family(row["id"], parents):
        return row["id"] + "，发布方模型仓标明底座为 " + "、".join(sorted(parents))
    if card.startswith("---") and re.search(r"^base_model\s*:", card.split("---", 2)[1], re.M):
        return ""
    if launch:
        return launch
    card = re.sub(r"```.*?(?:```|$)", "", card, flags=re.S)
    card = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", card).replace("**", "")
    stage = re.search(r"\bTraining Stage\s*:\s*([^\n|]+)", card, re.I)
    if stage and re.search(r"post[- ]?training|fine[- ]?tun|distill|align|reinforcement", stage[1], re.I):
        return ""
    if not stage and DERIVED_CLAIM.search(card):
        return ""
    for line in card.splitlines():
        if match := BASE_EVIDENCE.search(line):
            start = max(0, match.start() - 65)
            return line[start : match.end() + 100].strip()[:260]
    return ""
