"""节点 3：LLM 结构化抽取（实体/关系/属性）。

两种模式：
- LLM_PROVIDER != mock：Schema 注入 + few-shot + JSON mode 调用 DeepSeek，
  prompt 级 SQLite 缓存、并发限速、断点续跑、失败不阻断；
- mock：制造领域词典（部分别名覆盖）+ 结构化句式规则，模拟真实规则抽取器的
  漏召（别名未登录、错别字）与关系规则覆盖上限。
输出 surface 级实体/关系，规范化与留待节点 4（实体对齐）。
"""
import hashlib
import json
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ...config import CORPUS_DIR, GOLD_DIR, OUTPUT_DIR, settings
from ..seeds import knowledge_base as kbmod
from ...kg_types import ENTITY_LABELS, REL_TYPES

ENT_PATH = OUTPUT_DIR / "entities.jsonl"
REL_PATH = OUTPUT_DIR / "relations.jsonl"
SAMPLE_PATH = GOLD_DIR / "sample300.jsonl"

# 各类关系的规则召回率（稳定哈希决定丢条，模拟句式覆盖不全）
REL_RULE_COVERAGE = {
    "CAUSED_BY": 1.00,
    "ADDRESSED_BY": 0.98,
    "OCCURS_AT": 1.00,
    "USED_IN": 0.97,
    "TARGETS": 0.93,
    "INDUCED_BY_MATERIAL": 0.92,
    "USES_MATERIAL": 0.92,
    "PRECEDES": 0.90,
    "SUBCLASS_OF": 0.85,
}
# 抽取词典别名覆盖率按实体类型分层：核心三要素（设备/工序/不良）为现场规范词表，近乎全量；
# 原因/对策/物料长尾多，词典不全（模拟真实维护成本）
GAZETTEER_COVERAGE_BY_LABEL = {
    "Device": 0.98, "Process": 0.98, "Defect": 0.98,
    "Material": 0.85, "Cause": 0.82, "Countermeasure": 0.82,
}
PARAPHRASE_RATE = 0.12  # 长实体小概率以去产品前缀的复述形式出现，检验 embedding 对齐


# ---------------------------------------------------------------- 词典
def build_gazetteer(kb: dict) -> dict[str, tuple[str, str]]:
    """surface -> (entity_id, label)；长词优先，别名按稳定哈希部分收录。"""
    gaz: dict[str, tuple[str, str]] = {}

    def stable_keep(token: str, rate: float) -> bool:
        h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % 1000
        return h / 1000 < rate

    for eid, e in kb["entities"].items():
        gaz[e.name] = (eid, e.label)
        rate = GAZETTEER_COVERAGE_BY_LABEL.get(e.label, 0.85)
        for alias in e.aliases:
            if stable_keep(eid + "|" + alias, rate):
                gaz.setdefault(alias, (eid, e.label))
    return gaz


TYPO_CONFUSIONS = {"渡": "度", "湿": "温", "捡": "检", "旱": "焊", "交": "胶",
                   "青": "清", "遍": "偏", "夕": "锡", "庒": "压"}


def _typo_variants(sf: str) -> list[str]:
    out = []
    for wrong, right in TYPO_CONFUSIONS.items():
        if right in sf:
            out.append(sf.replace(right, wrong, 1))
    return out


def _ner_scan(text: str, gaz: dict[str, tuple[str, str]], rng: random.Random | None = None):
    """最长匹配扫描；同时容忍已知混淆字的一字符错别字（原样输出错别字 surface，交对齐纠错）。"""
    surfaces = sorted(gaz.keys(), key=len, reverse=True)
    found, occupied = [], [False] * len(text)

    def emit(pos: int, surface_out: str, eid: str, label: str, canonical: str):
        end = pos + len(surface_out)
        if not any(occupied[pos:end]):
            found.append({"surface": surface_out, "start": pos, "end": end,
                          "id": eid, "label": label, "canonical_surface": canonical})
            for i in range(pos, end):
                occupied[i] = True

    for sf in surfaces:
        eid, label = gaz[sf]
        # 精确命中
        start = 0
        while True:
            pos = text.find(sf, start)
            if pos < 0:
                break
            emit(pos, sf, eid, label, sf)
            start = pos + 1
        # 错别字模糊命中（单字混淆；长原因句也允许单点纠错）
        if len(sf) <= 40:
            for var in _typo_variants(sf):
                start = 0
                while True:
                    pos = text.find(var, start)
                    if pos < 0:
                        break
                    emit(pos, var, eid, label, sf)
                    start = pos + 1

    # 复述形式：小概率为长实体（原因/对策）额外产出一条「去产品前缀」mention，
    # 与规范 mention 并存（模拟同一对象的两种表述），供 embedding 对齐环节处理
    if rng is not None:
        extra = []
        for m in found:
            if m["label"] not in ("Cause", "Countermeasure"):
                continue
            if rng.random() >= PARAPHRASE_RATE:
                continue
            canon = m.get("canonical_surface", m["surface"])
            for _pid, pname, short, _k in kbmod.PRODUCTS:
                matched = None
                for pref in (pname + "：", short + "："):
                    if canon.startswith(pref):
                        matched = pref
                        break
                if matched:
                    new_start = m["start"] + len(matched)
                    new_surface = text[new_start: m["end"]]
                    if new_surface and new_surface != m["surface"]:
                        extra.append({"surface": new_surface, "start": new_start,
                                      "end": m["end"], "id": m["id"], "label": m["label"],
                                      "canonical_surface": canon})
                    break
        found.extend(extra)

    found.sort(key=lambda m: m["start"])
    return found


def _kept(doc_id: str, h: str, rel: str, t: str) -> bool:
    rate = REL_RULE_COVERAGE.get(rel, 0.85)
    hsh = int(hashlib.md5(f"{doc_id}|{h}|{rel}|{t}".encode()).hexdigest(), 16) % 1000
    return hsh / 1000 < rate


def _find_between(mentions, lo, hi, label=None):
    for m in mentions:
        if m["start"] >= lo and m["end"] <= hi and (label is None or m["label"] == label):
            return m
    return None


def _find_all_between(mentions, lo, hi, label=None):
    return [m for m in mentions if m["start"] >= lo and m["end"] <= hi
            and (label is None or m["label"] == label)]


# ---------------------------------------------------------------- 规则关系
def extract_relations_rule(doc_id: str, text: str, mentions: list[dict]):
    """按三体裁文档的结构化句式抽取关系（仅依赖 mention 与句式关键词）。"""
    rels = []

    def add(h, rel, t):
        if h and t and h["id"] != t["id"] and _kept(doc_id, h["surface"], rel, t["surface"]):
            rels.append({
                "head": h["surface"], "head_label": h["label"],
                "rel": rel,
                "tail": t["surface"], "tail_label": t["label"],
            })

    if doc_id.startswith("EX-"):
        # 以编号项切分每个不良块（mention 偏移基于全文，需换算块内偏移）
        blocks = re.split(r"\n(?=\d+\.\s责任工序：)", text)
        for blk in blocks:
            base = text.find(blk)
            proc_m, dev_m = None, None

            hp = blk.find("责任工序：")
            if hp >= 0:
                he = blk.find("\n", hp)
                proc_m = _find_between(mentions, base + hp, base + he, "Process")
                dev_m = _find_between(mentions, base + hp, base + he, "Device")
                add(dev_m, "USED_IN", proc_m)

            defect = None
            dp = blk.find("不良现象：")
            if dp >= 0:
                de = blk.find("根因分析", dp)
                defect = _find_between(mentions, base + dp, base + (de if de > 0 else dp + 120),
                                       "Defect")
                add(defect, "OCCURS_AT", proc_m)

            # 根因列表
            causes = []
            cause_section = re.search(r"根因分析[^\n]*\n(.*?)(?:临时遏制措施：|二、|$)", blk, re.S)
            if cause_section:
                cs, ce = cause_section.start(1), cause_section.end(1)
                acs, ace = base + cs, base + ce
                causes = [m for m in mentions
                          if m["start"] >= acs and m["end"] <= ace and m["label"] == "Cause"]
                for c in causes:
                    add(defect, "CAUSED_BY", c)
                # 关联物料行
                for mm in re.finditer(r"关联物料：([^\n]*)", blk[cs:ce]):
                    ls = acs + mm.start(1)
                    le = acs + mm.end(1)
                    mat = _find_between(mentions, ls, le, "Material")
                    add(defect, "INDUCED_BY_MATERIAL", mat)

            # 临时遏制列表：（1）对策（作用工序：proc）
            temp_section = re.search(r"临时遏制措施：\n(.*?)(?:\d+\.\s责任工序：|二、|$)", blk, re.S)
            if temp_section:
                ts, te = temp_section.start(1), temp_section.end(1)
                ats, ate = base + ts, base + te
                items = list(re.finditer(r"（\d+）", blk[ts:te]))
                for i, im in enumerate(items):
                    ls = ats + im.start()
                    le = ate if i == len(items) - 1 else ats + items[i + 1].start()
                    cm = _find_between(mentions, ls, le, "Countermeasure")
                    tp = _find_between(mentions, ls, le, "Process")
                    if cm and i < len(causes):
                        add(causes[i], "ADDRESSED_BY", cm)
                        add(cm, "TARGETS", tp or proc_m)
                        # 设备切换类临时措施同时作用于当班设备
                        if dev_m and "备用设备" in cm["surface"]:
                            add(cm, "TARGETS", dev_m)

    elif doc_id.startswith("NTC-"):
        items = re.split(r"\n(?=\d+\.\s适用产品\s)", text)
        for it in items:
            hm = re.search(r"针对(.+?)：\n", it)
            if not hm:
                continue
            it_off = text.find(it)
            hs = it_off + hm.start(1)
            cause = _find_between(mentions, hs, hs + len(hm.group(1)) + 2, "Cause")

            # 工程对策
            cm6 = None
            m6 = re.search(r"对策：(.+?)。\n", it)
            if m6:
                s6, e6 = it_off + m6.start(1), it_off + m6.end(1)
                cm6 = _find_between(mentions, s6, e6, "Countermeasure")
                add(cause, "ADDRESSED_BY", cm6)
            # 标准化对策
            cm7 = None
            m7 = re.search(r"标准化：(.+?)（责任工序：", it)
            if m7:
                s7, e7 = it_off + m7.start(1), it_off + m7.end(1)
                cm7 = _find_between(mentions, s7, e7 + 20, "Countermeasure")
                tail_seg = it[m7.end(1): m7.end(1) + 40]
                proc7 = None
                pm = re.search(r"责任工序：(.+?)）", tail_seg)
                if pm:
                    seg_lo = it_off + m7.end(1) + 5
                    proc7 = _find_between(mentions, seg_lo,
                                          seg_lo + len(pm.group(1)) + 6, "Process")
                add(cause, "ADDRESSED_BY", cm7)
                add(cm7, "TARGETS", proc7)
                sm = re.search(r"设备侧要求：(.+?)的保养规程", it)
                if sm:
                    dv = _find_between(mentions, it_off + sm.start(1), it_off + sm.end(1) + 20, "Device")
                    add(cm7, "TARGETS", dv)

            # 作用对象/同步行（归属工程对策）
            for mm in re.finditer(r"作用对象：责任工序\s*(.+?)，", it):
                pm = _find_between(mentions, it_off + mm.start(1), it_off + mm.end(1) + 30, "Process")
                add(cm6, "TARGETS", pm)
            for mm in re.finditer(r"作用对象：设备\s*(.+?)，", it):
                dv = _find_between(mentions, it_off + mm.start(1), it_off + mm.end(1) + 30, "Device")
                add(cm6, "TARGETS", dv)
            for mm in re.finditer(r"作用对象：物料\s*(.+?)，", it):
                mt = _find_between(mentions, it_off + mm.start(1), it_off + mm.end(1) + 30, "Material")
                add(cm6, "TARGETS", mt)
            for mm in re.finditer(r"同步执行：对\s*(.+?)\s进行参数标定", it):
                dv = _find_between(mentions, it_off + mm.start(1), it_off + mm.end(1), "Device")
                add(cm6, "TARGETS", dv)
            for mm in re.finditer(r"同步更新\s*(.+?)\s工序", it):
                pm = _find_between(mentions, it_off + mm.start(1), it_off + mm.end(1), "Process")
                add(cm6, "TARGETS", pm)

    elif doc_id.startswith("SOP-"):
        proc = None
        m = re.search(r"在(.+?)工序的标准作业", text)
        if m:
            proc = _find_between(mentions, m.start(1), m.end(1) + 4, "Process")
        m = re.search(r"本工序使用(.+?)（型号", text)
        if m:
            dev = _find_between(mentions, m.start(1), m.end(1), "Device")
            add(dev, "USED_IN", proc)
        mm = re.search(r"三、耗用物料\n(.*?)(?:四、|五、|$)", text, re.S)
        if mm:
            for mat in _find_all_between(mentions, mm.start(1), mm.end(1), "Material"):
                add(proc, "USES_MATERIAL", mat)
        dm = re.search(r"重点拦截的不良现象：(.+?)。\n", text, re.S)
        if dm:
            for defect in _find_all_between(mentions, dm.start(1), dm.end(1), "Defect"):
                add(defect, "OCCURS_AT", proc)
        sm = re.search(r"其中(.+?)属于(.+?)的细分", text, re.S)
        if sm:
            child = _find_between(mentions, sm.start(1), sm.end(1), "Defect")
            parent = _find_between(mentions, sm.start(2), sm.end(2), "Defect")
            add(child, "SUBCLASS_OF", parent)
        pm = re.search(r"工序流转：(.+?)完工.*?进入(.+?)。", text, re.S)
        if pm:
            prev = _find_between(mentions, pm.start(1), pm.end(1), "Process")
            curr = _find_between(mentions, pm.start(2), pm.end(2), "Process")
            add(prev, "PRECEDES", curr)

    # 去重
    seen, uniq = set(), []
    for r in rels:
        k = (r["head"], r["rel"], r["tail"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


# ---------------------------------------------------------------- LLM 抽取
PROMPT_VERSION = "v5"  # 体裁感知 + 长文档分块；改 prompt 必须升版本号使旧产物失效
PACK_MAX_CHARS = 2600  # NTC 条目/EX 不良块打包上限（约 8 个 NTC 条目 / 3-4 个 EX 块）
CHUNK_CKPT = OUTPUT_DIR / ".extract_chunks.jsonl"

SYS_PROMPT = """你是制造工艺质量领域的信息抽取引擎。只输出一个 JSON 对象，禁止输出解释、分析或思考过程。
【实体类型（6 类，闭域）】
- Device 设备：回焊炉、贴片机、点胶机、波峰炉、注塑机、丝印机等具体设备（保留车间产线前缀）
- Process 工序：回流焊接、贴片、焊膏印刷、注塑成型、点胶等工艺/作业步骤名称（不是设备名）
- Material 物料：锡膏、助焊剂、胶水、PCB/元器件等实物；产品板卡（如 H6心率小板）也算 Material；工艺参数、温度值、设备、工序不算物料
- Defect 不良现象：虚焊、连锡、元件移位、立碑、缩水等缺陷名
- Cause 原因：根因括号内或"针对"后的完整根因句，必须连同"产品："前缀整句抽取，例如"H6手环心率小板：飞达送料步距异常，供料位置偏移"
- Countermeasure 对策（三类整句，均保留"产品："前缀，逐字复制）：
  · "对策："后的工程根因对策
  · "标准化："后的标准化/横展对策
  · "临时遏制措施："编号项内的遏制措施
【关系类型（9 类，闭域）】
USED_IN(Device->Process 设备用于工序)、USES_MATERIAL(Process->Material 工序耗用物料)、
OCCURS_AT(Defect->Process 不良发生于工序)、CAUSED_BY(Defect->Cause 不良归因于原因)、
ADDRESSED_BY(Cause->Countermeasure 原因由对策处置)、
TARGETS(Countermeasure->Device|Process|Material 对策作用对象)、
INDUCED_BY_MATERIAL(Defect->Material 物料诱发不良)、
SUBCLASS_OF(Defect->Defect 不良细分)、PRECEDES(Process->Process 前序工序)
【硬性要求】
1. 穷尽抽取：输入中的每个编号条目/不良块都必须覆盖，不得跳过任何一条根因或对策；
2. surface 必须是输入文本中的连续原文片段，逐字复制，不改写、不概括、不截断长句；
   原因/对策务必保留"产品：内容"完整前缀和句末标点前的全部内容；
3. 每条 Cause 都要与它对应的 Countermeasure 连 ADDRESSED_BY：工程对策、标准化对策分别各连一条；
4. "作用对象：责任工序 X"与"（责任工序：X）"-> Countermeasure TARGETS 该 Process；
   "作用对象：设备 X"/"同步执行：对 X 进行参数标定"-> TARGETS 该 Device；
   "作用对象：物料 X"-> TARGETS 该 Material；
5. "关联物料：X 状态异常"-> 对应 Defect INDUCED_BY_MATERIAL 该 Material；
6. 边界约束（避免过度切抽）：
   - Material 只在「作用对象：物料 X」「关联物料：X」「耗用物料 · X」等显式物料位置抽取，
     不要把 Cause/Countermeasure 长句内部出现的物料名再切成 Material（整句已归为原因/对策）；
   - Process/Device 同样只取显式位置（责任工序、作用工序、作用对象、同步执行/同步更新、当班设备、设备侧要求）中的名字；
   - "同步更新 X 工序点检表"/"同步执行：对 X"中的 X 也要与对应 Countermeasure 连 TARGETS；
7. 归不进闭域类型/关系的内容一律不抽；确实没有就给空数组。
【输出格式】{"entities":[{"surface":"原文片段","label":"类型"}],
"relations":[{"head":"头实体原文","head_label":"类型","rel":"关系","tail":"尾实体原文","tail_label":"类型"}]}"""

GENRE_HINT = {
    "EX": "【体裁：异常处理单】下面是一个或多个编号不良块。「根因分析」编号项整句（含产品前缀）是 Cause；"
          "「关联物料」行的实物是 Material；「临时遏制措施」编号项整句（含产品前缀）是 Countermeasure，"
          "与对应 Cause 连 ADDRESSED_BY，并 TARGETS 到「作用工序：X」的工序。",
    "NTC": "【体裁：工艺通知单】下面是一个或多个编号改善条目，必须逐条穷尽抽取。"
           "「对策：」整句与「标准化：」整句（均含产品前缀）各是一条 Countermeasure，"
           "二者都要与该条根因 Cause 连 ADDRESSED_BY；再按「作用对象/同步执行/同步更新/责任工序」"
           "把每条对策 TARGETS 到对应设备或工序。",
    "SOP": "【体裁：作业指导书】按章节抽取设备、工序、耗用物料与重点拦截不良。",
}

_SHOT_EX = (
    "1. 责任工序：回流焊接（当班设备：一车间SMT-2线回焊炉）\n"
    "   不良现象：枕头效应，严重度：高。\n"
    "   根因分析（经工艺参数比对与现场确认）：\n"
    "   （1）H6手环心率小板：回流焊炉波动大、参数漂移，过程能力Cpk不足。\n"
    "   （2）H6手环心率小板：无铅锡膏SAC305氧化变质，焊接表面可焊性下降。\n"
    "         关联物料：锡膏状态异常可诱发该不良，已要求供方提供批次质保书。\n"
    "   临时遏制措施：\n"
    "   （1）H6手环心率小板：暂停该工序生产，保留现场实物与参数记录，通知工程质量联合分析（回流焊接·参数漂移）（作用工序：回流焊接）。"
)
_SHOT_EX_JSON = {
    "entities": [
        {"surface": "回流焊接", "label": "Process"},
        {"surface": "一车间SMT-2线回焊炉", "label": "Device"},
        {"surface": "枕头效应", "label": "Defect"},
        {"surface": "H6手环心率小板：回流焊炉波动大、参数漂移，过程能力Cpk不足", "label": "Cause"},
        {"surface": "H6手环心率小板：无铅锡膏SAC305氧化变质，焊接表面可焊性下降", "label": "Cause"},
        {"surface": "锡膏", "label": "Material"},
        {"surface": "H6手环心率小板：暂停该工序生产，保留现场实物与参数记录，通知工程质量联合分析（回流焊接·参数漂移）", "label": "Countermeasure"},
    ],
    "relations": [
        {"head": "一车间SMT-2线回焊炉", "head_label": "Device", "rel": "USED_IN", "tail": "回流焊接", "tail_label": "Process"},
        {"head": "枕头效应", "head_label": "Defect", "rel": "OCCURS_AT", "tail": "回流焊接", "tail_label": "Process"},
        {"head": "枕头效应", "head_label": "Defect", "rel": "CAUSED_BY",
         "tail": "H6手环心率小板：回流焊炉波动大、参数漂移，过程能力Cpk不足", "tail_label": "Cause"},
        {"head": "枕头效应", "head_label": "Defect", "rel": "CAUSED_BY",
         "tail": "H6手环心率小板：无铅锡膏SAC305氧化变质，焊接表面可焊性下降", "tail_label": "Cause"},
        {"head": "枕头效应", "head_label": "Defect", "rel": "INDUCED_BY_MATERIAL", "tail": "锡膏", "tail_label": "Material"},
        {"head": "H6手环心率小板：回流焊炉波动大、参数漂移，过程能力Cpk不足", "head_label": "Cause",
         "rel": "ADDRESSED_BY",
         "tail": "H6手环心率小板：暂停该工序生产，保留现场实物与参数记录，通知工程质量联合分析（回流焊接·参数漂移）", "tail_label": "Countermeasure"},
        {"head": "H6手环心率小板：暂停该工序生产，保留现场实物与参数记录，通知工程质量联合分析（回流焊接·参数漂移）",
         "head_label": "Countermeasure", "rel": "TARGETS", "tail": "回流焊接", "tail_label": "Process"},
    ],
}

_SHOT_NTC = (
    "1. 适用产品 H6心率小板，针对元件移位的根因（H6手环心率小板：飞达送料步距异常，供料位置偏移）：\n"
    "   对策：H6手环心率小板：更换异常飞达并送修校验，台账记录该飞达不良履历。\n"
    "   作用对象：设备 一车间SMT-2线贴片机，设备组负责实施与验收。\n"
    "   同步更新 贴装 工序点检表与保养周期标准。\n"
    "   标准化：H6手环心率小板：修订设备预防性维护(PM)规程与备件寿命标准，更新点检表并培训设备组（贴片·飞达异常）（责任工序：贴片）。"
)
_SHOT_NTC_JSON = {
    "entities": [
        {"surface": "H6心率小板", "label": "Material"},
        {"surface": "元件移位", "label": "Defect"},
        {"surface": "H6手环心率小板：飞达送料步距异常，供料位置偏移", "label": "Cause"},
        {"surface": "H6手环心率小板：更换异常飞达并送修校验，台账记录该飞达不良履历", "label": "Countermeasure"},
        {"surface": "H6手环心率小板：修订设备预防性维护(PM)规程与备件寿命标准，更新点检表并培训设备组（贴片·飞达异常）", "label": "Countermeasure"},
        {"surface": "一车间SMT-2线贴片机", "label": "Device"},
        {"surface": "贴装", "label": "Process"},
        {"surface": "贴片", "label": "Process"},
    ],
    "relations": [
        {"head": "H6手环心率小板：飞达送料步距异常，供料位置偏移", "head_label": "Cause", "rel": "ADDRESSED_BY",
         "tail": "H6手环心率小板：更换异常飞达并送修校验，台账记录该飞达不良履历", "tail_label": "Countermeasure"},
        {"head": "H6手环心率小板：飞达送料步距异常，供料位置偏移", "head_label": "Cause", "rel": "ADDRESSED_BY",
         "tail": "H6手环心率小板：修订设备预防性维护(PM)规程与备件寿命标准，更新点检表并培训设备组（贴片·飞达异常）", "tail_label": "Countermeasure"},
        {"head": "H6手环心率小板：更换异常飞达并送修校验，台账记录该飞达不良履历", "head_label": "Countermeasure",
         "rel": "TARGETS", "tail": "一车间SMT-2线贴片机", "tail_label": "Device"},
        {"head": "H6手环心率小板：更换异常飞达并送修校验，台账记录该飞达不良履历", "head_label": "Countermeasure",
         "rel": "TARGETS", "tail": "贴装", "tail_label": "Process"},
        {"head": "H6手环心率小板：修订设备预防性维护(PM)规程与备件寿命标准，更新点检表并培训设备组（贴片·飞达异常）",
         "head_label": "Countermeasure", "rel": "TARGETS", "tail": "贴片", "tail_label": "Process"},
    ],
}

FEW_SHOT = [
    {"role": "user", "content": GENRE_HINT["EX"] + "\n" + _SHOT_EX},
    {"role": "assistant", "content": json.dumps(_SHOT_EX_JSON, ensure_ascii=False)},
    {"role": "user", "content": GENRE_HINT["NTC"] + "\n" + _SHOT_NTC},
    {"role": "assistant", "content": json.dumps(_SHOT_NTC_JSON, ensure_ascii=False)},
]

# 长文档编号条目切分（条目内关系闭环，无跨条目边）
_SEG_MARKERS = {
    "NTC": re.compile(r"(?m)^(?=\d+\.\s适用产品)"),
    "EX": re.compile(r"(?m)^(?=\d+\.\s责任工序：)"),
}


def _genre(doc_id: str) -> str:
    return doc_id.split("-", 1)[0]  # SOP / EX / NTC


def split_segments(doc_id: str, text: str) -> list[tuple[str, int]]:
    """长文档按编号条目切块并打包到 PACK_MAX_CHARS；返回 [(段文本, 全文起始偏移)]。

    条目边界即正则行首匹配位置，相邻条目在原文中连续 -> 拼包后仍是原文连续子串，
    段内偏移加 base 即全文偏移。
    """
    marker = _SEG_MARKERS.get(_genre(doc_id))
    if not marker:
        return [(text, 0)]
    starts = [m.start() for m in marker.finditer(text)]
    if not starts:
        return [(text, 0)]
    bounds = starts + [len(text)]
    pieces = [(starts[i], text[starts[i]:bounds[i + 1]]) for i in range(len(starts))]

    segs: list[tuple[str, int]] = []
    base, buf, total = None, [], 0
    for st, piece in pieces:
        if base is None:
            base, buf, total = st, [piece], len(piece)
        elif total + len(piece) <= PACK_MAX_CHARS:
            buf.append(piece)
            total += len(piece)
        else:
            segs.append(("".join(buf), base))
            base, buf, total = st, [piece], len(piece)
    if buf:
        segs.append(("".join(buf), base))
    return segs


def _salvage_truncated_json(raw: str) -> dict:
    """JSON 被 max_tokens 截断时，正则抢救完整对象（部分召回优于整篇丢弃）。"""
    ent, rel = [], []
    for m in re.finditer(r"\{[^{}]*?\"surface\"[^{}]*?\}", raw, re.S):
        try:
            ent.append(json.loads(m.group(0)))
        except Exception:
            pass
    for m in re.finditer(r"\{[^{}]*?\"head\"[^{}]*?\}", raw, re.S):
        try:
            rel.append(json.loads(m.group(0)))
        except Exception:
            pass
    return {"entities": ent, "relations": rel}


def extract_one_llm(doc_id: str, seg_text: str, base: int = 0) -> tuple[list[dict], list[dict]]:
    """对一个文档段（SOP 全文 / NTC 条目包 / EX 不良块包）做结构化抽取。

    base 为该段在全文的字符偏移，mention 偏移换算为全文口径。
    """
    import json as _json

    from ...config import settings
    from ...llm import llm

    genre = _genre(doc_id)
    hint = GENRE_HINT.get(genre, "")
    messages = [{"role": "system", "content": SYS_PROMPT}] + FEW_SHOT + [
        {"role": "user", "content": hint + "\n" + seg_text if hint else seg_text}
    ]
    raw = llm.chat(messages, temperature=0.05, json_mode=True,
                   max_tokens_override=settings.llm_extract_max_tokens)
    try:
        data = _json.loads(llm._repair_json(raw))
    except _json.JSONDecodeError:
        data = _salvage_truncated_json(raw)
    surfaces = {}
    ent_rows = []
    for e in data.get("entities", []):
        sf, lab = str(e.get("surface", "")).strip(), str(e.get("label", "")).strip()
        if not sf or lab not in ENTITY_LABELS or sf not in seg_text:
            continue
        pos = 0
        while True:
            pos = seg_text.find(sf, pos)
            if pos < 0:
                break
            key = (sf, lab, pos)
            if key not in surfaces:
                surfaces[key] = True
                ent_rows.append({"surface": sf, "label": lab,
                                 "start": base + pos, "end": base + pos + len(sf)})
            pos += len(sf)
    sf_by_label = {}
    for r in ent_rows:
        sf_by_label.setdefault((r["surface"], r["label"]), r)
    rel_rows = []
    for r in data.get("relations", []):
        rel = r.get("rel")
        if rel not in REL_TYPES:
            continue
        h, t = str(r.get("head", "")).strip(), str(r.get("tail", "")).strip()
        hl, tl = r.get("head_label"), r.get("tail_label")
        if (h, hl) in sf_by_label and (t, tl) in sf_by_label:
            rel_rows.append({"head": h, "head_label": hl, "rel": rel, "tail": t, "tail_label": tl})
    return ent_rows, rel_rows


# ---------------------------------------------------------------- 抽检集
def build_sample300(docs: list[dict]) -> list[dict]:
    rng = random.Random(settings.seed + 300)
    mentions, triples = [], []
    for d in docs:
        for m in d["mentions"]:
            if not m.get("typo"):
                mentions.append({"doc_id": d["doc_id"], "kind": "mention",
                                 "surface": m["surface"], "label": m["label"], "gold_id": m["id"]})
        for h, rel, t in d["relations"]:
            triples.append({"doc_id": d["doc_id"], "kind": "relation",
                            "head_id": h, "rel": rel, "tail_id": t})
    rng.shuffle(mentions)
    rng.shuffle(triples)
    sample = mentions[:150] + triples[:150]
    rng.shuffle(sample)
    return sample


# ---------------------------------------------------------------- 主流程
def run(ctx):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_DIR.mkdir(parents=True, exist_ok=True)

    kb = kbmod.build_kb(settings.seed)
    # 原因/对策实体在场景构建阶段生成并注册进 KB，词典必须包含它们（长词优先匹配）
    kbmod.build_scenarios(kb, settings.seed)
    gaz = build_gazetteer(kb)
    ctx.log(f"抽取词典构建：{len(gaz)} 个 surface（分层别名覆盖率：核心 98% / 长尾 82-85%）")

    doc_files = sorted(CORPUS_DIR.glob("*.txt"))
    docs = [(p.stem, p.read_text(encoding="utf-8")) for p in doc_files]
    gold_docs = [json.loads(l) for l in
                 (GOLD_DIR / "corpus_gold.jsonl").read_text(encoding="utf-8").splitlines()]
    (SAMPLE_PATH).write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in build_sample300(gold_docs)),
        encoding="utf-8")

    # 断点续跑：模式键含 provider/model/prompt 版本，任一变化必须全量重跑
    mode_marker = OUTPUT_DIR / ".extract_mode"
    if settings.llm_provider == "mock":
        mode_key = f"{settings.llm_provider}:{settings.llm_model}"
    else:
        mode_key = f"{settings.llm_provider}:{settings.llm_model}:{PROMPT_VERSION}"
    same_mode = mode_marker.exists() and mode_marker.read_text(
        encoding="utf-8").strip() == mode_key

    # ------------------------------------------------------------ mock：规则抽取（文档级）
    if settings.llm_provider == "mock":
        mode_marker.write_text(mode_key, encoding="utf-8")
        ent_out, rel_out, errors = [], [], []
        for doc_id, text in docs:
            try:
                doc_seed = int(hashlib.md5(doc_id.encode()).hexdigest()[:8], 16)
                mentions = _ner_scan(text, gaz, random.Random(settings.seed + doc_seed))
                e = [{"surface": m["surface"], "label": m["label"],
                      "start": m["start"], "end": m["end"]} for m in mentions]
                r = extract_relations_rule(doc_id, text, mentions)
            except Exception as exc:
                errors.append((doc_id, str(exc)))
                continue
            ent_out.extend({**x, "doc_id": doc_id} for x in e)
            rel_out.extend({**x, "doc_id": doc_id} for x in r)
        ENT_PATH.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in ent_out), encoding="utf-8")
        REL_PATH.write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in rel_out), encoding="utf-8")
        if errors[:3]:
            ctx.log(f"失败文档 {len(errors)} 份，示例：{errors[:3]}", "warning")
        ctx.log(f"抽取完成（mock 规则）：实体 mention {len(ent_out)} 条、关系 {len(rel_out)} 条；300 条抽检集已冻结")
        return {"mode": "mock", "docs": len(docs),
                "entity_mentions_extracted": len(ent_out),
                "relations_extracted": len(rel_out),
                "sample_size": 300, "errors": len(errors)}

    # ------------------------------------------------------------ LLM：长文档分块 + chunk 级断点
    if not same_mode:
        for p in (ENT_PATH, REL_PATH, CHUNK_CKPT):
            if p.exists():
                p.unlink()
    mode_marker.write_text(mode_key, encoding="utf-8")

    done: set[str] = set()
    if CHUNK_CKPT.exists():
        done = {l.strip() for l in CHUNK_CKPT.read_text(encoding="utf-8").splitlines() if l.strip()}

    tasks: list[tuple[str, str, str, int]] = []
    total_segs = 0
    for doc_id, text in docs:
        segs = split_segments(doc_id, text)
        total_segs += len(segs)
        for cidx, (seg, base) in enumerate(segs):
            key = f"{doc_id}#{cidx}"
            if key not in done:
                tasks.append((key, doc_id, seg, base))
    ctx.log(f"LLM 分块抽取：共 {total_segs} 段，待跑 {len(tasks)} 段，模式 {mode_key}（pack≤{PACK_MAX_CHARS}字）")

    errors: list[tuple[str, str]] = []
    write_mode = "a" if done and ENT_PATH.exists() else "w"
    lock = threading.Lock()
    n_finished = 0
    with open(ENT_PATH, write_mode, encoding="utf-8") as ent_f, \
            open(REL_PATH, write_mode, encoding="utf-8") as rel_f, \
            open(CHUNK_CKPT, "a", encoding="utf-8") as ckpt_f, \
            ThreadPoolExecutor(max_workers=settings.extract_concurrency) as pool:
        futs = {pool.submit(extract_one_llm, doc_id, seg, base): key
                for key, doc_id, seg, base in tasks}

        def _emit(key: str, doc_id: str, ent_rows, rel_rows) -> None:
            nonlocal n_finished
            for x in ent_rows:
                ent_f.write(json.dumps({**x, "doc_id": doc_id}, ensure_ascii=False) + "\n")
            for x in rel_rows:
                rel_f.write(json.dumps({**x, "doc_id": doc_id}, ensure_ascii=False) + "\n")
            ckpt_f.write(key + "\n")
            ent_f.flush()
            rel_f.flush()
            ckpt_f.flush()
            n_finished += 1
            if n_finished % 20 == 0:
                ctx.log(f"LLM 抽取进度 {n_finished}/{len(tasks)}")

        for fut in as_completed(futs):
            key = futs[fut]
            doc_id = key.split("#", 1)[0]
            try:
                e, r = fut.result()
            except Exception as exc:
                errors.append((key, str(exc)[:120]))
                continue
            with lock:
                _emit(key, doc_id, e, r)

    n_ent = sum(1 for _ in ENT_PATH.open(encoding="utf-8"))
    n_rel = sum(1 for _ in REL_PATH.open(encoding="utf-8"))
    if errors[:3]:
        ctx.log(f"失败段 {len(errors)} 个（可重跑续抽），示例：{errors[:3]}", "warning")
    ctx.log(f"抽取完成：{total_segs} 段全部落盘；实体 mention {n_ent} 条、关系 {n_rel} 条；300 条抽检集已冻结")
    return {"mode": settings.llm_provider, "docs": len(docs), "segments": total_segs,
            "entity_mentions_extracted": n_ent, "relations_extracted": n_rel,
            "sample_size": 300, "errors": len(errors)}
