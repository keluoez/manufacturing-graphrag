"""节点 2：语料构建 - 种子库 + 模板渲染，约 200 份三体裁文档。

关键设计：
- 生成即带金标：每文档输出实体 mention（含字符偏移）与关系三元组；
- 噪声注入：别名/口语化、错别字、单位变体、全半角、原因与对策拆文档（多跳断点）；
- seed 固定，每次运行逐字节可复现。
"""
import json
import random
from collections import defaultdict

from ...config import CORPUS_DIR, GOLD_DIR, settings
from ..seeds import knowledge_base as kbmod
from ..seeds.knowledge_base import (
    DEVICE_TYPES,
    DEFECTS,
    LINES,
    MATERIALS,
    PROCESSES,
    Entity,
)

# 错别字混淆表（仅在低比例下替换，模拟现场录入错误）
TYPO_MAP = {
    "度": "渡", "温": "湿", "检": "捡", "焊": "旱", "胶": "交",
    "清": "青", "偏": "遍", "锡": "夕", "压": "庒", "螺": "骡",
}
UNIT_COLLOQUIAL = {"℃": "度", "mm": "毫米", "μm": "微米", "MPa": "兆帕",
                   "kPa": "千帕", "h": "小时", "g/mL": "克每毫升"}
GENERIC_TYPO_RATE = 0.03


class DocBuilder:
    """边拼文本边记录实体 mention 偏移。"""

    def __init__(self, rng: random.Random, genre: str):
        self.rng = rng
        self.genre = genre
        self.parts: list[tuple] = []

    def text(self, s: str) -> "DocBuilder":
        self.parts.append(("t", s))
        return self

    def ent(self, e: Entity, surface: str | None = None, typo: bool | None = None) -> "DocBuilder":
        if surface is None:
            pool = [e.name] + list(e.aliases)
            if self.genre == "EX":
                # 异常处理单更口语化：别名权重更高
                surface = self.rng.choice(e.aliases if e.aliases else [e.name]) if self.rng.random() < 0.65 else e.name
            else:
                surface = self.rng.choice(pool)
        if typo is None:
            typo = self.rng.random() < GENERIC_TYPO_RATE
        if typo:
            chars = [c for c in surface if c in TYPO_MAP]
            if chars:
                c0 = self.rng.choice(chars)
                surface = surface.replace(c0, TYPO_MAP[c0], 1)
                typo = True
            else:
                typo = False
        self.parts.append(("e", e, surface, typo))
        return self

    def build(self) -> tuple[str, list[dict]]:
        out, mentions, pos = [], [], 0
        for p in self.parts:
            if p[0] == "t":
                out.append(p[1])
                pos += len(p[1])
            else:
                _, e, surface, typo = p
                out.append(surface)
                mentions.append({
                    "id": e.id, "label": e.label, "name": e.name,
                    "surface": surface, "start": pos, "end": pos + len(surface),
                    "typo": typo,
                })
                pos += len(surface)
        return "".join(out), mentions


_line_idx = kbmod.line_index


def _param_surface(rng, value, unit, genre):
    if genre == "SOP" and unit in UNIT_COLLOQUIAL and rng.random() < 0.5:
        return f"{value}{UNIT_COLLOQUIAL[unit]}"
    return f"{value}{unit}"


def _stable_date(rng) -> str:
    return f"2026-{rng.randint(3, 9):02d}-{rng.randint(1, 28):02d}"


# ---------------------------------------------------------------- SOP
def render_sop(no, rng, scenarios_by_proc, process_id, line_device_idx):
    proc_tuple = next(p for p in PROCESSES if p[0] == process_id)
    _, pname, _, seq, params = proc_tuple
    proc = next(s.process for s in scenarios_by_proc.get(process_id, [])) if scenarios_by_proc.get(process_id) else None
    if proc is None:
        # 从 KB 兜底构建
        kb = _SHARED_KB
        proc = kb["processes"][process_id]

    dev_type = next((d[0] for d in DEVICE_TYPES if d[3] == process_id), None)

    b = DocBuilder(rng, "SOP")
    b.text(f"【作业指导书 SOP-{no:03d}】\n")
    b.text(f"文件编号：WI-{seq}-{no:03d}　版本：B.{rng.randint(0, 3)}　密级：内部\n")
    b.text("一、适用范围\n")
    b.text("本指导书适用于").ent(_product_entity(rng)).text("在").ent(proc).text("工序的标准作业，本工序为责任工序。\n")

    if dev_type:
        dev = _SHARED_KB["devices_by_type"][dev_type][line_device_idx % len(LINES)]
        model = next(d[4] for d in DEVICE_TYPES if d[0] == dev_type)
        b.text("二、设备与工装\n本工序使用").ent(dev).text(f"（型号 {model}），开班执行点检并记录设备状态。\n")

    b.text("三、耗用物料\n")
    mats = [m for m in MATERIALS if process_id in m[5]]
    rng.shuffle(mats)
    mats = mats[:6]
    if not mats:
        b.text("本工序为检测/试验工序，不直接耗用计入BOM的生产物料，仅使用少量检测辅材。\n")
    for mrow in mats:
        mat = _SHARED_KB["materials"][mrow[0]]
        b.text("· ").ent(mat).text(f"，规格 {mrow[3]}，计量单位 {mrow[4]}\n")

    if params:
        b.text("四、标准工艺参数（每 2 小时记录一次）\n")
        for pname_i, unit, lo, hi, std in params[:4]:
            b.text(f"· {pname_i}：标准值 ").text(
                _param_surface(rng, std, unit, "SOP")
            ).text(f"，工艺窗口 {lo}~{hi}{unit}\n")

    # 该工序不良
    proc_defects = [d for d in DEFECTS if d[3] == process_id]
    b.text("五、常见不良与判定\n本工序需重点拦截的不良现象：")
    if proc_defects:
        chosen = rng.sample(proc_defects, min(len(proc_defects), max(2, len(proc_defects))))
        for i, drow in enumerate(chosen):
            d = _SHARED_KB["defects"][drow[0]]
            b.ent(d)
            if i < len(chosen) - 1:
                b.text("、")
            else:
                b.text("。\n")
        # SUBCLASS
        sub = [d for d in chosen if d[5]]
        wrote_subclass = False
        if sub and rng.random() < 0.8:
            child = _SHARED_KB["defects"][sub[0][0]]
            parent = _SHARED_KB["defects"][sub[0][5]]
            b.text("其中").ent(child).text("属于").ent(parent).text("的细分表现形式，判定时需分别记录代码。\n")
            wrote_subclass = True
    else:
        b.text("本工序以装配/检测为主，按通用外观判定标准执行。\n")

    # PRECEDES
    idx = next((i for i, p in enumerate(PROCESSES) if p[0] == process_id), 0)
    wrote_precedes = False
    if idx > 0 and rng.random() < 0.7:
        prev = _SHARED_KB["processes"][PROCESSES[idx - 1][0]]
        b.text("六、工序流转：").ent(prev).text("完工并放行后，方可进入").ent(proc).text("。\n")
        wrote_precedes = True

    relations = []
    if dev_type:
        relations.append((dev.id, "USED_IN", process_id))
    for mrow in mats:
        relations.append((process_id, "USES_MATERIAL", mrow[0]))
    if proc_defects:
        for drow in chosen:
            relations.append((drow[0], "OCCURS_AT", process_id))
        if wrote_subclass:
            sub = [d for d in chosen if d[5]]
            relations.append((sub[0][0], "SUBCLASS_OF", sub[0][5]))
    if wrote_precedes:
        relations.append((PROCESSES[idx - 1][0], "PRECEDES", process_id))

    text, mentions = b.build()
    # 去重关系
    relations = list(dict.fromkeys(relations))
    return text, mentions, relations


# ---------------------------------------------------------------- EX（批次多不良）
def render_exception_batch(no, rng, batch_scenarios):
    """一份异常单记录同一批次发现的多个不良现象；每个不良列根因，临时措施偶发出现。"""
    product = batch_scenarios[0].product
    line = LINES[_line_idx(product.id)]
    b = DocBuilder(rng, "EX")
    b.text(f"【异常处理单 EX-{_stable_date(rng).replace('-', '')[-6:]}-{no:03d}】\n")
    b.text(f"线别：{line}　产品型号：").ent(product)
    b.text("　批次：B").text(str(rng.randint(2601, 2640))).text("　等级：批量异常\n")

    relations = []
    covered_items = []
    b.text("一、不良现象与原因分析\n")
    for j, s in enumerate(batch_scenarios):
        b.text(f"{j+1}. 责任工序：").ent(s.process)
        if s.device:
            b.text("（当班设备：").ent(s.device).text("）")
        b.text("\n   不良现象：").ent(s.defect)
        b.text(f"，严重度：{s.defect.attrs.get('severity', '中')}。\n")
        relations.append((s.defect.id, "OCCURS_AT", s.process.id))
        if s.device:
            relations.append((s.device.id, "USED_IN", s.process.id))

        causes = s.case_causes[:]
        rng.shuffle(causes)
        b.text("   根因分析（经工艺参数比对与现场确认）：\n")
        for i, cc in enumerate(causes):
            b.text(f"   （{i+1}）").ent(cc.cause).text("。\n")
            relations.append((s.defect.id, "CAUSED_BY", cc.cause.id))
            if cc.spec_key.startswith("MAT_") or cc.spec_key in ("COPLANAR", "FLUX_LOW", "DRY_INSUF"):
                b.text("         关联物料：").ent(s.material).text("状态异常可诱发该不良，已要求供方提供批次质保书。\n")
                relations.append((s.defect.id, "INDUCED_BY_MATERIAL", s.material.id))
            covered_items.append((s, cc))

        # 临时遏制措施：约 80% 的不良给出 1-2 条临时措施（非根因对策）
        if rng.random() < 0.80:
            n_temp = 2 if len(causes) >= 2 else 1
            b.text("   临时遏制措施：\n")
            for k, cc in enumerate(causes[:n_temp]):
                b.text(f"   （{k+1}）").ent(cc.temp_countermeasure)
                b.text("（作用工序：").ent(s.process).text("）。\n")
                relations.append((cc.cause.id, "ADDRESSED_BY", cc.temp_countermeasure.id))
                relations.append((cc.temp_countermeasure.id, "TARGETS", s.process.id))
                # 设备切换类临时措施同时作用于当班设备
                if s.device and "备用设备" in cc.temp_countermeasure.name:
                    relations.append((cc.temp_countermeasure.id, "TARGETS", s.device.id))

    b.text("二、处置结论\n")
    ntc = f"NTC-{rng.randint(1, 40):03d}"
    b.text(f"当批在制品已隔离评审，根因对策由制造工程部评估后以工艺通知单（{ntc}）下发。\n")

    text, mentions = b.build()
    return text, mentions, list(dict.fromkeys(relations)), covered_items


# ---------------------------------------------------------------- NTC
def render_notice(no, rng, items):
    """items: [(scenario, case_cause), ...] 一组原因-对策条目。"""
    b = DocBuilder(rng, "NTC")
    b.text(f"【工艺通知单 NTC-{no:03d}】\n")
    b.text(f"发文日期：{_stable_date(rng)}　编制：制造工程部　会签：质量部\n")
    b.text("针对近期生产线多发不良的根因，经验证明确以下改善对策，自发布之日起执行：\n")
    relations = []
    action_type = ""
    for i, (sc, cc) in enumerate(items):
        # 约 70% 的通知单条目会回指不良现象（闭环留痕比例高但非全量；
        # 关键词/语义路因此能桥接一部分多跳，图谱显式边仍占优）
        if rng.random() < 0.70:
            b.text(f"{i+1}. 适用产品 ").ent(sc.product).text("，针对").ent(sc.defect)
            b.text("的根因（").ent(cc.cause).text("）：\n")
        else:
            b.text(f"{i+1}. 适用产品 ").ent(sc.product).text("，针对").ent(cc.cause).text("：\n")
        b.text("   对策：").ent(cc.countermeasure).text("。\n")
        cm_attrs = cc.countermeasure.attrs
        tgt_kind = cm_attrs.get("target_kind")
        action_type = cm_attrs.get("action_type", "")
        dev_mentioned = None
        if tgt_kind == "proc":
            b.text("   作用对象：责任工序 ").ent(sc.process).text("，由工艺工程师跟线确认 3 个批次。\n")
            relations.append((cc.countermeasure.id, "TARGETS", sc.process.id))
            # 调参类动作同时标定工序设备，形成第二作用对象（约 80%）
            if action_type == "调参" and rng.random() < 0.80:
                dev_type = next((d[0] for d in DEVICE_TYPES if d[3] == sc.process.id), None)
                if dev_type:
                    devs = _SHARED_KB["devices_by_type"][dev_type]
                    dev_mentioned = devs[_line_idx(sc.product.id) % len(devs)]
                    b.text("   同步执行：对 ").ent(dev_mentioned).text(" 进行参数标定与首件验证。\n")
        elif tgt_kind == "dev":
            dev_type = cm_attrs.get("target_dev_type")
            if dev_type:
                devs = _SHARED_KB["devices_by_type"].get(dev_type, [])
                dev_mentioned = devs[_line_idx(sc.product.id) % len(devs)] if devs else None
                if dev_mentioned:
                    b.text("   作用对象：设备 ").ent(dev_mentioned).text("，设备组负责实施与验收。\n")
                    relations.append((cc.countermeasure.id, "TARGETS", dev_mentioned.id))
            # 设备类动作同时要求工序更新点检标准
            if action_type in ("设备维护", "清洁"):
                b.text("   同步更新 ").ent(sc.process).text(" 工序点检表与保养周期标准。\n")
                relations.append((cc.countermeasure.id, "TARGETS", sc.process.id))
        elif tgt_kind == "mat":
            mat_id = cm_attrs.get("target_mat")
            mat = _SHARED_KB["materials"].get(mat_id)
            if mat:
                b.text("   作用对象：物料 ").ent(mat).text("，SQE 负责供应商侧闭环。\n")
                relations.append((cc.countermeasure.id, "TARGETS", mat.id))
            # 换料类对策同步要求工序更新作业标准（约 60%）
            if rng.random() < 0.60:
                b.text("   同步更新 ").ent(sc.process).text(" 工序上料作业与首件确认要求。\n")
                relations.append((cc.countermeasure.id, "TARGETS", sc.process.id))
        if dev_mentioned is not None:
            relations.append((cc.countermeasure.id, "TARGETS", dev_mentioned.id))
        relations.append((cc.cause.id, "ADDRESSED_BY", cc.countermeasure.id))

        # D7 标准化/横展对策：固化规程、更新FMEA、培训
        b.text("   标准化：").ent(cc.std_countermeasure).text("（责任工序：").ent(sc.process).text("）。\n")
        relations.append((cc.cause.id, "ADDRESSED_BY", cc.std_countermeasure.id))
        relations.append((cc.std_countermeasure.id, "TARGETS", sc.process.id))
        std_dev_type = cc.std_countermeasure.attrs.get("target_dev_type")
        if std_dev_type and rng.random() < 0.80:
            std_devs = _SHARED_KB["devices_by_type"].get(std_dev_type, [])
            if std_devs:
                std_dev = std_devs[_line_idx(sc.product.id) % len(std_devs)]
                b.text("   设备侧要求：").ent(std_dev).text("的保养规程同步修订。\n")
                relations.append((cc.std_countermeasure.id, "TARGETS", std_dev.id))

    text, mentions = b.build()
    return text, mentions, list(dict.fromkeys(relations))


_SHARED_KB: dict = {}
_product_cache: dict[str, Entity] = {}


def _product_entity(rng) -> Entity:
    # SOP 中随机出现产品型号；复用 KB 中的产品实体
    pid = rng.choice(kbmod.PRODUCTS)[0]
    return _SHARED_KB["materials"][pid]


def run(ctx):
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    for old in CORPUS_DIR.glob("*.txt"):
        old.unlink()

    seed = settings.seed
    rng = random.Random(seed)
    kb = kbmod.build_kb(seed)
    _SHARED_KB.clear()
    _SHARED_KB.update(kb)
    scenarios = kbmod.build_scenarios(kb, seed)

    scenarios_by_proc = defaultdict(list)
    for s in scenarios:
        scenarios_by_proc[s.process.id].append(s)

    total = settings.corpus_size  # 200
    n_sop, n_ex, n_ntc = 70, 90, 40
    assert n_sop + n_ex + n_ntc == total

    gold_records = []
    all_relations = set()
    mentioned_ids = set()
    cause_covered = set()
    cm_covered = set()
    ex_cause_items: list[tuple] = []

    def write(doc_id, genre, text, mentions, relations, extra=None):
        (CORPUS_DIR / f"{doc_id}.txt").write_text(text, encoding="utf-8")
        rec = {
            "doc_id": doc_id,
            "genre": genre,
            "text_len": len(text),
            "mentions": mentions,
            "relations": relations,
        }
        if extra:
            rec.update(extra)
        gold_records.append(rec)
        for m in mentions:
            mentioned_ids.add(m["id"])
        all_relations.update(tuple(r) for r in relations)

    # 1) SOP：前 20 份保证每个工序一篇，其余按 SMT 高发工序加权
    proc_rotation = [p[0] for p in PROCESSES]
    weighted = (
        [p[0] for p in PROCESSES if p[0] in ("PROC_PRINT", "PROC_REFLOW", "PROC_PLACE", "PROC_AOI", "PROC_INJECT")] * 3
        + proc_rotation
    )
    sop_procs = proc_rotation + [rng.choice(weighted) for _ in range(n_sop - len(proc_rotation))]
    for i, proc_id in enumerate(sop_procs[:n_sop]):
        text, mentions, rels = render_sop(i + 1, rng, scenarios_by_proc, proc_id, i % len(LINES))
        write(f"SOP-{i+1:03d}", "SOP", text, mentions, rels)

    # 2) 异常处理单：按产品批次组织，每单记录同批次 3-4 个不良现象；
    #    优先不重复抽取同一（产品×不良）情景，保证原因覆盖广度
    scenarios_by_product = defaultdict(list)
    for s in scenarios:
        scenarios_by_product[s.product.id].append(s)
    product_ids = list(scenarios_by_product.keys())
    rng.shuffle(product_ids)
    used_scenario_keys: set[str] = set()
    ex_cause_items = []
    for i in range(n_ex):
        pid = product_ids[i % len(product_ids)]
        cand = [s for s in scenarios_by_product[pid]
                if f"{pid}|{s.defect.id}" not in used_scenario_keys]
        if len(cand) < 3:
            used_scenario_keys = {k for k in used_scenario_keys if not k.startswith(pid + "|")}
            cand = scenarios_by_product[pid][:]
        rng.shuffle(cand)
        batch = cand[: min(4, len(cand)) or 4]
        for s in batch:
            used_scenario_keys.add(pid + "|" + s.defect.id)
        text, mentions, rels, covered_items = render_exception_batch(i + 1, rng, batch)
        write(f"EX-{i+1:03d}", "EX", text, mentions, rels,
              {"product": pid, "defects": [x.defect.id for x in batch]})
        ex_cause_items.extend(covered_items)

    # 同一（产品×原因）在多份异常单重复出现时去重
    dedup_items, seen_item = [], set()
    for item in ex_cause_items:
        key = (item[0].product.id, item[1].cause.id)
        if key not in seen_item:
            seen_item.add(key)
            dedup_items.append(item)
    ex_cause_items = dedup_items

    # 3) 工艺通知单：覆盖异常单原因的根因对策，不足用场景池其他原因补齐；
    #    根因对策只出现在通知单 → 原因与对策跨文档分离（GraphRAG 多跳优势的来源）
    used_item_keys = {(s.product.id, cc.cause.id) for s, cc in ex_cause_items}
    item_pool = ex_cause_items[:]
    extra_pool: list[tuple] = []
    for s in scenarios:
        for cc in s.case_causes:
            if (s.product.id, cc.cause.id) not in used_item_keys:
                extra_pool.append((s, cc))
    rng.shuffle(item_pool)
    rng.shuffle(extra_pool)
    per_notice = 36
    idx, eidx = 0, 0
    notices_written = 0
    for n in range(n_ntc):
        items = item_pool[idx: idx + per_notice]
        idx += per_notice
        if len(items) < 8:
            need = per_notice - len(items)
            items += extra_pool[eidx: eidx + need]
            eidx += need
        if len(items) < 3:
            break
        text, mentions, rels = render_notice(n + 1, rng, items)
        write(f"NTC-{n+1:03d}", "NTC", text, mentions, rels)
        notices_written += 1
        for _, cc in items:
            cm_covered.add(cc.countermeasure.id)
            cause_covered.add(cc.cause.id)

    (GOLD_DIR / "corpus_gold.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in gold_records),
        encoding="utf-8",
    )
    # 全量潜在线索（供评测/问答金标构建，不等同抽取结果）
    manifest = {
        "seed": seed,
        "doc_count": len(gold_records),
        "genre_count": {"SOP": n_sop, "EX": n_ex, "NTC": n_ntc},
        "gold_entities_mentioned": len(mentioned_ids),
        "gold_relations": len(all_relations),
        "scenario_count": len(scenarios),
        "cause_covered": len(cause_covered),
        "countermeasure_covered": len(cm_covered),
        "kb_entity_pool": len(kb["entities"]),
    }
    (GOLD_DIR / "corpus_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    ctx.log(f"语料生成完成：{len(gold_records)} 份（SOP {n_sop} / 异常单 {n_ex} / 通知单 {n_ntc}），seed={seed}")
    ctx.log(f"金标：提及实体 {len(mentioned_ids)} 个（KB 实体池 {len(kb['entities'])}）、唯一关系 {len(all_relations)} 条")
    ctx.log(f"多跳断点设计：{len(cm_covered)} 个对策仅出现在通知单文档，原因与对策跨文档分离")
    return manifest
