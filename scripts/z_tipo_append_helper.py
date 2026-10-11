import os
import json
import pathlib
import random
import re
import sys
from typing import List

import gradio as gr
import torch
from modules import devices, scripts, shared
from modules.extra_networks import parse_prompt
try:
    # Forge Neo 2.30 moved attention parsing into the text-processing backend.
    from backend.text_processing.parsing import parse_prompt_attention
except ImportError:
    from modules.prompt_parser import parse_prompt_attention
from modules.scripts import OnComponent, basedir

# --- Locate original z-tipo extension so we can reuse its installer/runtime ---
HELPER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSIONS_DIR = os.path.abspath(os.path.join(HELPER_DIR, ".."))
ZT_DIR = os.path.join(EXTENSIONS_DIR, "z-tipo-extension")
if ZT_DIR not in sys.path and os.path.isdir(ZT_DIR):
    sys.path.insert(0, ZT_DIR)

# Runtime pieces installed / provided by z-tipo-extension
from tipo_installer import ensure_runtime  # type: ignore
from kgen import models  # type: ignore
from kgen.executor import tipo  # type: ignore
from kgen.executor.tipo import parse_tipo_request, tipo_runner  # type: ignore
from kgen.formatter import apply_format, seperate_tags  # type: ignore
from kgen.logging import logger  # type: ignore
from kgen.metainfo import TIPO_DEFAULT_FORMAT  # type: ignore

SEED_MAX = 2**31 - 1
TOTAL_TAG_LENGTH = {
    "VERY_SHORT": "very short",
    "SHORT": "short",
    "LONG": "long",
    "VERY_LONG": "very long",
}
MODEL_NAME_LIST = [
    f"{model_name} | {file}"
    for model_name, ggufs in models.tipo_model_list
    for file in ggufs
] + [i[0] for i in models.tipo_model_list]

DEFAULT_FORMAT_KEY = "Both, tag first (recommend)"
DEFAULT_FORMAT = TIPO_DEFAULT_FORMAT[DEFAULT_FORMAT_KEY]
EXTRA_INPUT_LAYOUT = ["Side by side", "Stacked"]

COMMENT_LINE = re.compile(r"^[^\S\n]*#[^\n]*(?:\n|$)", re.MULTILINE)
DANGLING = re.compile(r"(?:^[\s,]+|[\s,]+$)")
REPEATED_COMMA = re.compile(r",\s*(?=,)")
WEIGHTED_TAG_RE = re.compile(r"^\((.*?):\s*[-+]?\d*\.?\d+\)$")
MULTISPACE = re.compile(r"\s+")


def sanitize_ban_tags(ban_tags: str):
    """Return BAN_TAGS entries safe for kgen's substring/regex matcher.

    kgen applies both `b in tag` and `re.match(b, tag)` to every entry.
    Invalid regex such as `*foo` would otherwise raise re.PatternError and abort
    prompt generation.  Keep valid regex intact; escape invalid patterns so they
    are treated as literal text by re.match while preserving substring matching.
    """
    safe = []
    converted = []
    for raw in (ban_tags or "").split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            re.compile(item)
            safe.append(item)
        except re.error:
            escaped = re.escape(item)
            safe.append(escaped)
            converted.append(item)
    if converted:
        logger.warning(
            "TIPO Append Helper: invalid Ban tags regex treated as literal text: "
            + ", ".join(converted)
        )
    return safe
SETTINGS_FILE = os.path.join(HELPER_DIR, "settings.json")

DEFAULT_SETTINGS = {
    "append_enabled": True,
    "dedupe_enabled": True,
    "no_formatting": False,
    "tag_length": TOTAL_TAG_LENGTH["LONG"],
    "nl_length": TOTAL_TAG_LENGTH["LONG"],
    "ban_tags": "",
    "format_select": DEFAULT_FORMAT_KEY,
    "format_text": DEFAULT_FORMAT,
    "seed": -1,
    "gguf_use_cpu": False,
    "temperature": 0.5,
    "top_p": 0.95,
    "top_k": 80,
    "keep_open": False,
}


def load_helper_settings():
    data = dict(DEFAULT_SETTINGS)
    try:
        if os.path.isfile(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                for key in data:
                    if key in saved:
                        data[key] = saved[key]
    except Exception as e:
        print(f"TIPO Append Helper: failed to load settings: {e}")
    return data


def _write_settings(data):
    os.makedirs(HELPER_DIR, exist_ok=True)
    tmp_file = SETTINGS_FILE + ".tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, SETTINGS_FILE)


def save_helper_settings(*values):
    keys = [
        "append_enabled", "dedupe_enabled", "no_formatting",
        "tag_length", "nl_length", "ban_tags", "format_select",
        "format_text", "seed", "gguf_use_cpu", "temperature",
        "top_p", "top_k",
    ]
    data = load_helper_settings()
    for key, value in zip(keys, values):
        data[key] = value
    try:
        _write_settings(data)
        print(f"TIPO Append Helper: settings saved: {SETTINGS_FILE}")
        return "設定を保存しました（次回起動時にも復元されます）"
    except Exception as e:
        return f"設定の保存に失敗しました: {e}"


def toggle_keep_open(current):
    new_value = not bool(current)
    data = load_helper_settings()
    data["keep_open"] = new_value
    try:
        _write_settings(data)
        print(f"TIPO Append Helper: keep_open={new_value} saved: {SETTINGS_FILE}")
        label = "📌 開いたまま：ON" if new_value else "📌 スクリプト欄を開いたままにする"
        status = "開いたまま設定を保存しました" if new_value else "開いたまま設定を解除しました"
        return new_value, gr.update(open=new_value), gr.update(value=label), status
    except Exception as e:
        return current, gr.update(), gr.update(), f"設定の保存に失敗しました: {e}"


print(f"TIPO Append Helper: settings file: {SETTINGS_FILE}")


def strip_comments(text: str) -> str:
    if not text or "#" not in text:
        return text
    cleaned = REPEATED_COMMA.sub("", COMMENT_LINE.sub("", text))
    return DANGLING.sub("", cleaned)


def embedding_names() -> set:
    try:
        from modules.sd_hijack import model_hijack
        return {str(name).lower() for name in model_hijack.embedding_db.word_embeddings}
    except Exception:
        return set()


def embedding_spellings(tags) -> dict:
    known = embedding_names()
    if not known:
        return {}
    return {
        tag.replace("_", " "): tag
        for tag in tags
        if len(tag) >= 4 and "_" in tag and tag.lower() in known
    }


def restore_embeddings(tag_map, mapping):
    if not mapping:
        return tag_map
    for cate, value in tag_map.items():
        if isinstance(value, list):
            tag_map[cate] = [mapping.get(tag, tag) for tag in value]
        elif isinstance(value, str):
            for spaced, original in mapping.items():
                value = value.replace(spaced, original)
            tag_map[cate] = value
    return tag_map


def apply_strength(tag_map, strength_map, strength_map_nl, break_map):
    for cate in tag_map:
        new_list = []
        if isinstance(tag_map[cate], str):
            if all(part in tag_map[cate] for part, strength in strength_map_nl):
                org_prompt = tag_map[cate]
                new_prompt = ""
                for part, strength in strength_map_nl:
                    before, org_prompt = org_prompt.split(part, 1)
                    new_prompt += before.replace("(", "\\(").replace(")", "\\)")
                    part = part.replace("(", "\\(").replace(")", "\\)")
                    new_prompt += f"({part}:{strength})"
                new_prompt += org_prompt
            else:
                new_prompt = tag_map[cate]
            tag_map[cate] = new_prompt
            continue
        for org_tag in tag_map[cate]:
            tag = org_tag.replace("(", "\\(").replace(")", "\\)")
            if org_tag in strength_map:
                new_list.append(f"({tag}:{strength_map[org_tag]})")
            else:
                new_list.append(tag)
            if tag in break_map or org_tag in break_map:
                new_list.append("BREAK")
        tag_map[cate] = new_list
    return tag_map


def normalize_tag(token: str) -> str:
    token = token.strip().strip(",")
    if not token:
        return ""
    m = WEIGHTED_TAG_RE.match(token)
    while m:
        token = m.group(1).strip()
        m = WEIGHTED_TAG_RE.match(token)
    if token.startswith("(") and token.endswith(")") and token.count("(") == 1 and token.count(")") == 1:
        token = token[1:-1].strip()
    token = token.replace("\\(", "(").replace("\\)", ")")
    token = token.replace("_", " ")
    token = MULTISPACE.sub(" ", token)
    return token.lower()


def split_prompt_tokens(text: str) -> List[str]:
    if not text:
        return []
    text = text.replace("\r\n", "\n")
    parts = re.split(r",|\n", text)
    return [part.strip() for part in parts if part and part.strip()]


def merge_prompt_keep_original(original: str, addon: str, dedupe: bool = True) -> str:
    original = (original or "").strip()
    addon = (addon or "").strip()
    if not original:
        return addon
    if not addon:
        return original

    addon_tokens = split_prompt_tokens(addon)
    if not addon_tokens:
        return original

    if dedupe:
        existing = {normalize_tag(tok) for tok in split_prompt_tokens(original)}
        filtered = []
        for tok in addon_tokens:
            norm = normalize_tag(tok)
            if not norm or norm in existing:
                continue
            filtered.append(tok)
            existing.add(norm)
    else:
        filtered = addon_tokens

    if not filtered:
        return original

    base = original.rstrip().rstrip(",")
    return base + ", " + ", ".join(filtered)


class TIPOProcessor:
    def __init__(self):
        self.current_model = None
        self.model_dir = pathlib.Path(ZT_DIR) / "models"
        models.model_dir = self.model_dir

    def process(
        self,
        prompt: str,
        nl_prompt: str,
        aspect_ratio: float,
        seed: int,
        tag_length: str,
        nl_length: str,
        ban_tags: str,
        format_select: str,
        format_text: str,
        temperature: float,
        top_p: float,
        top_k: int,
        model: str,
        gguf_use_cpu: bool,
        no_formatting: bool,
        tag_prompt: str,
    ):
        prompt = (prompt or "").strip() or (tag_prompt or "")
        seed = int(seed) % SEED_MAX
        if model != self.current_model:
            ensure_runtime()
            if " | " in model:
                model_name, gguf_name = model.split(" | ")
                target_file = f"{model_name.split('/')[-1]}_{gguf_name}"
                if str(models.model_dir / target_file) not in models.list_gguf():
                    models.download_gguf(model_name, gguf_name)
                target = os.path.join(str(models.model_dir), target_file)
                gguf = True
            else:
                target = model
                gguf = False
            target_device = "cpu" if gguf_use_cpu else str(devices.device)
            models.load_model(target, gguf, device=target_device)
            self.current_model = model

        prompt = strip_comments(prompt)
        nl_prompt = strip_comments(nl_prompt)
        prompt_preview = prompt.replace("\n", " ")[:40]
        logger.info(f"TIPO Append Helper processing prompt: {prompt_preview}...")
        logger.info(f"TIPO Append Helper seed: {seed}")

        prompt_without_extranet, res = parse_prompt(prompt)
        prompt_parse_strength = parse_prompt_attention(prompt_without_extranet)
        nl_prompt_parse_strength = parse_prompt_attention(nl_prompt)

        nl_prompt_joined = ""
        strength_map_nl = []
        for part, strength in nl_prompt_parse_strength:
            nl_prompt_joined += part
            if strength != 1:
                strength_map_nl.append((part, strength))

        rebuild_extranet = ""
        for name, params in res.items():
            for param in params:
                items = ":".join(param.items)
                rebuild_extranet += f" <{name}:{items}>"

        black_list = sanitize_ban_tags(ban_tags)
        tipo.BAN_TAGS = black_list
        all_tags = []
        strength_map = {}
        break_map = set()
        for part, strength in prompt_parse_strength:
            part_tags = [tag.strip() for tag in part.strip().split(",") if tag.strip()]
            if part == "BREAK" and strength == -1:
                if all_tags:
                    break_map.add(all_tags[-1])
                continue
            all_tags.extend(part_tags)
            if strength != 1:
                for tag in part_tags:
                    strength_map[tag] = strength

        tag_length = tag_length.replace(" ", "_")
        nl_length = nl_length.replace(" ", "_")
        embeddings = embedding_spellings(all_tags)
        org_tag_map = restore_embeddings(seperate_tags(all_tags), embeddings)

        chosen_format = format_text
        if format_select != "custom":
            chosen_format = TIPO_DEFAULT_FORMAT.get(format_select, format_text)

        meta, operations, general, nl_prompt_final = parse_tipo_request(
            org_tag_map,
            nl_prompt_joined,
            tag_length_target=tag_length,
            nl_length_target=nl_length,
            generate_extra_nl_prompt=(not nl_prompt_joined and "<|extended|>" in chosen_format)
            or "<|generated|>" in chosen_format,
        )
        meta["aspect_ratio"] = f"{aspect_ratio:.1f}"

        if isinstance(models.text_model, torch.nn.Module):
            models.text_model.to(devices.device)
        tag_map, _ = tipo_runner(
            meta,
            operations,
            general,
            nl_prompt_final,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            seed=seed,
        )
        if isinstance(models.text_model, torch.nn.Module):
            models.text_model.cpu()
            devices.torch_gc()

        tag_map = restore_embeddings(tag_map, embeddings)
        addon = {"tags": [], "nl": ""}
        for cate in tag_map:
            if cate == "generated" and addon["nl"] == "":
                addon["nl"] = tag_map[cate]
                continue
            if cate == "extended":
                addon["nl"] = tag_map[cate]
                continue
            if cate not in org_tag_map:
                continue
            for tag in tag_map[cate]:
                if tag in org_tag_map[cate]:
                    continue
                addon["tags"].append(tag)
        addon = apply_strength(addon, strength_map, strength_map_nl, break_map)
        unformatted = prompt_without_extranet + ", " + ", ".join(addon["tags"]) + "\n" + addon["nl"]
        tag_map = apply_strength(tag_map, strength_map, strength_map_nl, break_map)
        formatted = apply_format(tag_map, chosen_format).replace("BREAK,", "BREAK")
        final_prompt = unformatted if no_formatting else formatted
        result = final_prompt + "\n" + rebuild_extranet
        logger.info("TIPO Append Helper done.")
        return result


class TIPOAppendHelper(scripts.Script):
    # Forge Neo can render AlwaysVisible scripts without an extra wrapper.
    # This lets the companion JS place this accordion directly below Z-Tipo.
    create_group = False
    sorting_priority = 0.01

    def __init__(self):
        super().__init__()
        self.prompt_area = [None, None]
        self.processor = TIPOProcessor()
        self.on_after_component_elem_id = [
            ("txt2img_prompt", lambda x: self._set_prompt_area(0, x)),
            ("img2img_prompt", lambda x: self._set_prompt_area(1, x)),
        ]

    def _set_prompt_area(self, idx: int, component: OnComponent):
        self.prompt_area[idx] = component.component

    def title(self):
        return "TIPO Append Helper"

    def show(self, _):
        return scripts.AlwaysVisible

    def _generate_and_merge(
        self,
        original_prompt: str,
        tag_prompt: str,
        nl_prompt: str,
        append_enabled: bool,
        dedupe_enabled: bool,
        seed: int,
        tag_length: str,
        nl_length: str,
        ban_tags: str,
        format_select: str,
        format_text: str,
        temperature: float,
        top_p: float,
        top_k: int,
        model: str,
        gguf_use_cpu: bool,
        no_formatting: bool,
    ):
        if seed == -1:
            seed = random.randrange(2**31 - 1)
        generated = self.processor.process(
            tag_prompt,
            nl_prompt,
            1.0,
            int(seed),
            tag_length,
            nl_length,
            ban_tags,
            format_select,
            format_text,
            temperature,
            top_p,
            top_k,
            model,
            gguf_use_cpu,
            no_formatting,
            tag_prompt,
        )
        if append_enabled:
            return merge_prompt_keep_original(original_prompt, generated, dedupe_enabled)
        return generated

    def ui(self, is_img2img):
        tab_name = "img2img" if is_img2img else "txt2img"
        saved = load_helper_settings()
        accordion_id = f"{tab_name}_tipo_append_helper"

        with gr.Accordion("TIPO Append Helper", open=bool(saved.get("keep_open", False)), elem_id=accordion_id) as helper_accordion:
            keep_open_state = gr.State(bool(saved.get("keep_open", False)))
            with gr.Row():
                pin_btn = gr.Button(
                    value=("📌 開いたまま：ON" if saved.get("keep_open", False) else "📌 スクリプト欄を開いたままにする"),
                    size="sm",
                    elem_id=f"{tab_name}_tipo_append_pin",
                )
                save_btn = gr.Button(value="設定を保存", size="sm")
                save_status = gr.Textbox(show_label=False, interactive=False, container=False)

            gr.Markdown(
                "既存の Positive Prompt を保持したまま、TIPO で生成したタグを追記します。"
                "\n\n- 既存Positiveへ追記: 生成結果を上書きせずに追加"
                "\n- 重複タグを除去: `blue_hair` / `blue hair` / `(blue hair:1.2)` を同一タグとして扱います"
                "\n- Model: Z-Tipo本体で選択しているモデルを生成時に自動取得します"
            )
            with gr.Row():
                tag_prompt_box = gr.Textbox(
                    label="Tag Prompt",
                    lines=3,
                    placeholder="TIPO に推論させたいタグだけを入れてください",
                )
                nl_prompt_box = gr.Textbox(
                    label="Natural Language Prompt",
                    lines=3,
                    placeholder="必要なら自然文でも補足できます",
                )
            with gr.Row():
                append_enabled = gr.Checkbox(label="既存Positiveへ追記", value=saved["append_enabled"])
                dedupe_enabled = gr.Checkbox(label="重複タグを除去", value=saved["dedupe_enabled"])
                no_formatting = gr.Checkbox(label="No formatting", value=saved["no_formatting"])
            with gr.Row():
                tag_length_radio = gr.Radio(
                    label="Tags Length target",
                    choices=list(TOTAL_TAG_LENGTH.values()),
                    value=saved["tag_length"],
                )
                nl_length_radio = gr.Radio(
                    label="NL Length target",
                    choices=list(TOTAL_TAG_LENGTH.values()),
                    value=saved["nl_length"],
                )
            ban_tags_textbox = gr.Textbox(
                label="Ban tags",
                info="カンマ区切り。正規表現対応。",
                value=saved["ban_tags"],
                placeholder="umbrella, official.*, .*text, ...",
            )
            format_dropdown = gr.Dropdown(
                label="Prompt Format",
                choices=list(TIPO_DEFAULT_FORMAT.keys()) + ["custom"],
                value=saved["format_select"],
            )
            format_textarea = gr.TextArea(
                value=saved["format_text"],
                label="Custom Prompt Format",
                visible=saved["format_select"] == "custom",
                placeholder="<|extended|>. <|general|>",
            )
            format_dropdown.change(
                lambda x: gr.update(
                    visible=x == "custom",
                    value=TIPO_DEFAULT_FORMAT.get(x, DEFAULT_FORMAT),
                ),
                inputs=format_dropdown,
                outputs=format_textarea,
            )
            model_from_tipo = gr.Textbox(
                value=MODEL_NAME_LIST[0] if MODEL_NAME_LIST else "",
                visible=False,
                elem_id=f"{tab_name}_tipo_append_model_bridge",
            )
            model_status = gr.Markdown("**Model:** Z-Tipo本体の選択値を使用")
            with gr.Row():
                seed_input = gr.Number(label="Seed", minimum=-1, step=1, value=saved["seed"])
                gguf_use_cpu = gr.Checkbox(label="Use CPU (GGUF)", value=saved["gguf_use_cpu"])
            with gr.Row():
                temperature_slider = gr.Slider(label="Temperature", maximum=1.5, minimum=0.1, step=0.05, value=saved["temperature"])
                top_p_slider = gr.Slider(label="Top-p", maximum=1, minimum=0, step=0.05, value=saved["top_p"])
                top_k_slider = gr.Slider(label="Top-k", maximum=150, minimum=0, step=1, value=saved["top_k"])
            generate_btn = gr.Button(value="TIPO生成して Positive に追記")

            save_inputs = [
                append_enabled,
                dedupe_enabled,
                no_formatting,
                tag_length_radio,
                nl_length_radio,
                ban_tags_textbox,
                format_dropdown,
                format_textarea,
                seed_input,
                gguf_use_cpu,
                temperature_slider,
                top_p_slider,
                top_k_slider,
            ]
            save_btn.click(
                fn=save_helper_settings,
                inputs=save_inputs,
                outputs=save_status,
                show_progress=False,
            )

            pin_btn.click(
                fn=toggle_keep_open,
                inputs=[keep_open_state],
                outputs=[keep_open_state, helper_accordion, pin_btn, save_status],
                show_progress=False,
            )

            generate_btn.click(
                fn=self._generate_and_merge,
                inputs=[
                    self.prompt_area[is_img2img],
                    tag_prompt_box,
                    nl_prompt_box,
                    append_enabled,
                    dedupe_enabled,
                    seed_input,
                    tag_length_radio,
                    nl_length_radio,
                    ban_tags_textbox,
                    format_dropdown,
                    format_textarea,
                    temperature_slider,
                    top_p_slider,
                    top_k_slider,
                    model_from_tipo,
                    gguf_use_cpu,
                    no_formatting,
                ],
                outputs=[self.prompt_area[is_img2img]],
                js=f"(...args) => tipoAppendInjectZTipoModel('{tab_name}', args, 14)",
            )

        return []
