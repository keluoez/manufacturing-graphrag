"""150 题业务测试集（50 单跳 / 50 多跳归因 / 50 统计聚合）。

金标答案从语料金标图谱（corpus_gold）派生，与抽取结果无关；seed 固定可复现。

题面鲁棒性设计（v2）：锚点不再一律使用实体规范名——
- ~30% 锚点改用实体别名/口语说法（假焊、墓碑效应、短产品名…），考察别名词表覆盖；
- ~8% 非产品锚点注入一个现场录入错别字（锡→夕、检→捡…），只能靠 embedding
  语义补链/纠错找回，考察实体链接在非规范输入下的真实鲁棒性；
- gold_ids 始终是规范实体 ID，不因题面写法改变。
"""
import json
import random
from collections import defaultdict

from ..config import GOLD_DIR, settings
from ..pipeline.seeds import knowledge_base as kbmod

QUESTION_PATH = GOLD_DIR / "questions150.jsonl"

# 与 s2_corpus.TYPO_MAP 同方向（正确字 → 现场常见错字），用于题面噪声
TYPO_FOR_QUESTION = {
    "度": "渡", "温": "湿", "检": "捡", "焊": "旱", "胶": "交",
    "清": "青", "偏": "遍", "锡": "夕", "压": "庒",
}
ALIAS_RATE = 0.30   # 锚点改用别名的比例
TYPO_RATE = 0.08    # 在锚点说法上再注入一个错别字的比例


def build_questions() -> list[dict]:
    seed = settings.seed
    rng = random.Random(seed + 150)
    kb = kbmod.build_kb(seed)
    kbmod.build_scenarios(kb, seed)
    entities = kb["entities"]

    # ---- 金标图谱（语料实际出现的边）
    occurs = defaultdict(set)          # defect -> {proc}
    caused = defaultdict(set)          # defect -> {cause}
    addressed = defaultdict(set)       # cause -> {cm}
    used_in = defaultdict(set)         # device -> {proc}
    uses_mat = defaultdict(set)        # proc -> {mat}
    induced = defaultdict(set)         # defect -> {mat}
    docs = [json.loads(l) for l in
            (GOLD_DIR / "corpus_gold.jsonl").read_text(encoding="utf-8").splitlines()]
    for d in docs:
        for h, rel, t in d["relations"]:
            if rel == "OCCURS_AT":
                occurs[h].add(t)
            elif rel == "CAUSED_BY":
                caused[h].add(t)
            elif rel == "ADDRESSED_BY":
                addressed[h].add(t)
            elif rel == "USED_IN":
                used_in[h].add(t)
            elif rel == "USES_MATERIAL":
                uses_mat[h].add(t)
            elif rel == "INDUCED_BY_MATERIAL":
                induced[h].add(t)

    def name(eid: str) -> str:
        return entities[eid].name if eid in entities else eid

    # ---- 题面噪声：唯一别名反查（歧义别名不用于出题，避免题面指向多实体）----
    alias_owner: dict[str, str] = {}
    ambiguous: set[str] = set()
    for eid, e in entities.items():
        for a in [e.name, *e.aliases]:
            if a in alias_owner and alias_owner[a] != eid:
                ambiguous.add(a)
            else:
                alias_owner.setdefault(a, eid)

    def display(eid: str, allow_typo: bool = True) -> str:
        """题面中实体的显示文本：规范名 / 唯一别名 / 加一个错别字。"""
        e = entities.get(eid)
        if e is None:
            return eid
        txt = e.name
        if rng.random() < ALIAS_RATE:
            safe = [a for a in e.aliases
                    if a not in ambiguous and a != e.name and len(a) >= 2]
            if safe:
                txt = rng.choice(safe)
        # 产品锚点不注错别字：多跳题的产品代码过滤依赖产品链接成功
        if allow_typo and rng.random() < TYPO_RATE:
            chars = [c for c in txt if c in TYPO_FOR_QUESTION]
            if chars:
                c0 = rng.choice(chars)
                txt = txt.replace(c0, TYPO_FOR_QUESTION[c0], 1)
        return txt

    def defect_cms(defect_id: str) -> set[str]:
        out = set()
        for c in caused.get(defect_id, ()):  # 仅根因工程对策（CM_）作为严格金标
            for cm in addressed.get(c, ()):
                if cm.startswith("CM_"):
                    out.add(cm)
        return out

    questions = []
    qid = 0

    def add(qtype, query, gold_ids, intent, anchors=None, gold_number=None):
        nonlocal qid
        qid += 1
        questions.append({
            "qid": f"Q{qid:03d}", "qtype": qtype, "query": query,
            "gold_ids": sorted(gold_ids), "intent": intent,
            "anchors": anchors or [], "gold_number": gold_number,
        })

    # 产品代码 -> 该产品在金标中出现的缺陷
    product_defects = defaultdict(set)
    for d in docs:
        if d.get("product"):
            for dd in d.get("defects", [d.get("defect")]):
                if dd:
                    product_defects[d["product"]].add(dd)

    def product_cms(pid: str, defect_id: str) -> set[str]:
        """某产品某缺陷的根因工程对策（严格金标）。"""
        code = pid.removeprefix("MAT_")
        out = set()
        for c in caused.get(defect_id, ()):
            if f"_{code}_" not in c:
                continue
            for cm in addressed.get(c, ()):
                if cm.startswith("CM_") and f"_{code}_" in cm:
                    out.add(cm)
        return out

    # ---------------- 单跳 50
    pool_d = [d for d in occurs if occurs[d]]
    pool_p = sorted({p for p in uses_mat if uses_mat[p]} |
                    {pp for ds in occurs.values() for pp in ds})
    multi_cause = [d for d in caused if len(caused[d]) >= 2]
    pool_dev = [x for x in used_in if used_in[x]]
    pool_mat_p = [p for p in uses_mat if len(uses_mat[p]) >= 2]
    for pool in (pool_d, pool_p, multi_cause, pool_dev, pool_mat_p):
        rng.shuffle(pool)

    def add_d():
        if pool_d:
            d = pool_d.pop()
            add("single", f"{display(d)}一般发生在哪个工序？", occurs[d], "defect_to_process", [d])
            return True
        return False

    for _ in range(15):
        add_d()
    for p in pool_p[:15]:
        gold = {d for d in occurs if p in occurs[d]}
        if gold:
            add("single", f"{display(p)}工序有哪些常见不良现象？", gold, "process_to_defects", [p])
    for d in multi_cause[:10]:
        add("single", f"{display(d)}的可能原因有哪些？", caused[d], "defect_to_causes", [d])
    for dv in pool_dev[:5]:
        add("single", f"{display(dv)}用于哪个工序？", used_in[dv], "device_to_process", [dv])
    for p in pool_mat_p[:5]:
        add("single", f"{display(p)}工序主要耗用哪些物料？", uses_mat[p], "process_to_materials", [p])
    # 不足 50 用缺陷→工序补齐
    while sum(1 for q in questions if q["qtype"] == "single") < 50 and add_d():
        pass

    # ---------------- 多跳归因 50（产品 × 缺陷 → 根因对策）
    pd_pairs = []
    for pid, defects in product_defects.items():
        for d in defects:
            if product_cms(pid, d):
                pd_pairs.append((pid, d))
    rng.shuffle(pd_pairs)
    templates = [
        "{p}产线上出现{n}，工程上应该采取什么根因对策？",
        "{p}批次{n}异常怎么处理？给出可闭环的永久对策。",
        "针对{p}产线的{n}问题，有哪些永久性改善措施？",
        "{p}在制{n}的处理对策是什么？要针对根因。",
    ]
    for j, (pid, d) in enumerate(pd_pairs[:50]):
        add("multi", templates[j % len(templates)].format(p=display(pid, allow_typo=False), n=display(d)),
            product_cms(pid, d), "defect_to_countermeasures", [pid, d])

    # ---------------- 聚合 50
    # 20：工序 → 全部不良 → 对策汇总
    agg_proc = [p for p in occurs if any(defect_cms(d) for d in occurs if p in occurs[d])]
    rng.shuffle(agg_proc)
    for p in agg_proc[:20]:
        gold = set()
        for d in occurs[p]:
            gold |= defect_cms(d)
        add("agg", f"{display(p)}工序常见不良的根因对策汇总有哪些？", gold,
            "process_aggregate_cm", [p])
    # 15：工序不良数量（数值题）
    count_procs = [p for p in occurs if occurs[p]]
    rng.shuffle(count_procs)
    for p in count_procs[:15]:
        gold = {d for d in occurs if p in occurs[d]}
        add("agg", f"{display(p)}工序需要重点拦截多少种不良现象？", gold,
            "process_defect_count", [p], gold_number=len(gold))
    # 15：物料 → 相关不良
    mat_pool = sorted({m for ds in induced.values() for m in ds})
    rng.shuffle(mat_pool)
    mat_used = 0
    for m in mat_pool:
        if mat_used >= 15:
            break
        gold = {d for d in induced if m in induced[d]}
        if gold:
            add("agg", f"与{display(m)}相关的不良现象有哪些？", gold, "material_to_defects", [m])
            mat_used += 1
    # 不足部分：产品×工序对策汇总
    pp_pairs = [(pid, p) for pid, ds in product_defects.items()
                for p in {pp for dd in ds for pp in occurs.get(dd, set())}]
    rng.shuffle(pp_pairs)
    while sum(1 for q in questions if q["qtype"] == "agg") < 50 and pp_pairs:
        pid, p = pp_pairs.pop()
        gold = set()
        for d in product_defects[pid]:
            if p in occurs.get(d, set()):
                gold |= product_cms(pid, d)
        if gold:
            add("agg", f"{display(pid, allow_typo=False)}在{display(p)}工序不良的根因对策汇总？", gold,
                "process_aggregate_cm", [pid, p])

    by_type = defaultdict(int)
    for q in questions:
        by_type[q["qtype"]] += 1
    return questions


def write_questions() -> list[dict]:
    qs = build_questions()
    QUESTION_PATH.write_text(
        "\n".join(json.dumps(q, ensure_ascii=False) for q in qs), encoding="utf-8")
    return qs


def load_questions() -> list[dict]:
    if not QUESTION_PATH.exists():
        return write_questions()
    return [json.loads(l) for l in QUESTION_PATH.read_text(encoding="utf-8").splitlines()]
