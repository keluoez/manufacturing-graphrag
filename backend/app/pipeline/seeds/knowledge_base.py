"""制造领域种子知识库（确定性，seed 固定可复现）。

实体/别名/参数化原因与对策全部在此沉淀；
语料生成器(s2)、问题生成(s9)共用同一份 KB 与 scenario 池。
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 工序
# (id, 规范名, 别名, 序号, [(参数名, 单位, 下限, 上限, 标准值)])
PROCESSES = [
    ("PROC_IQC", "来料检验", ["进料检", "IQC"], 10, [("抽检比例", "%", 0, 100, 100)]),
    ("PROC_PRINT", "锡膏印刷", ["印刷", "丝印", "焊膏印刷"], 20,
     [("印刷压力", "kg", 5, 15, 10), ("脱模速度", "mm/s", 1, 10, 3),
      ("钢网厚度", "mm", 0.08, 0.15, 0.12), ("锡膏厚度", "μm", 100, 200, 150)]),
    ("PROC_SPI", "SPI检测", ["SPI", "锡膏厚度检测"], 30, [("判定下限", "μm", 80, 140, 120)]),
    ("PROC_PLACE", "贴片", ["贴装", "打件", "SMT贴装"], 40,
     [("贴装压力", "N", 1, 8, 4), ("贴装精度", "mm", 0.02, 0.1, 0.05),
      ("吸嘴真空度", "kPa", -60, -30, -45)]),
    ("PROC_PRE_AOI", "炉前目检", ["炉前检"], 50, []),
    ("PROC_REFLOW", "回流焊接", ["回流焊", "过炉", "回焊"], 60,
     [("峰值温度", "℃", 235, 250, 243), ("恒温区时间", "s", 60, 120, 90),
      ("升温斜率", "℃/s", 1, 3, 2), ("冷却速率", "℃/s", -4, -1, -2.5),
      ("链速", "cm/min", 60, 100, 80)]),
    ("PROC_WAVE", "波峰焊接", ["波峰焊"], 70,
     [("锡炉温度", "℃", 250, 265, 258), ("浸锡时间", "s", 2, 5, 3.5),
      ("传送倾角", "°", 4, 7, 5.5), ("助焊剂比重", "g/mL", 0.8, 0.85, 0.82)]),
    ("PROC_AOI", "AOI检测", ["AOI", "自动光学检测", "光学检"], 80, [("误判率上限", "%", 0, 5, 2)]),
    ("PROC_XRAY", "XRAY检测", ["X-RAY", "X光检", "射线检测"], 90, []),
    ("PROC_DISPENSE", "点胶", ["滴胶"], 100,
     [("点胶压力", "MPa", 0.2, 0.6, 0.4), ("点胶时间", "ms", 50, 300, 150),
      ("胶点直径", "mm", 0.8, 2.0, 1.3)]),
    ("PROC_PRESS", "压合", ["贴合压合"], 110,
     [("压合压力", "MPa", 0.1, 0.8, 0.4), ("压合时间", "s", 3, 15, 8),
      ("保压温度", "℃", 60, 110, 85)]),
    ("PROC_ROUT", "分板", ["铣板", "割板"], 120, [("主轴转速", "rpm", 10000, 40000, 24000)]),
    ("PROC_INJECT", "注塑成型", ["注塑", "啤机"], 130,
     [("料筒温度", "℃", 200, 280, 240), ("注射压力", "MPa", 60, 140, 100),
      ("保压压力", "MPa", 40, 90, 65), ("模具温度", "℃", 40, 90, 65),
      ("注射速度", "mm/s", 30, 90, 60), ("冷却时间", "s", 8, 30, 18)]),
    ("PROC_ULTRA", "超声波焊接", ["超焊", "超声焊"], 140,
     [("焊接振幅", "μm", 20, 60, 40), ("焊接时间", "ms", 100, 500, 280),
      ("焊接压力", "MPa", 0.1, 0.5, 0.3)]),
    ("PROC_COATING", "三防涂覆", ["涂覆", "喷三防漆"], 150,
     [("膜厚", "μm", 25, 125, 75), ("喷涂压力", "MPa", 0.15, 0.4, 0.25)]),
    ("PROC_PLASMA", "等离子清洗", ["等离子处理", "Plasma"], 155, [("处理功率", "W", 200, 800, 500)]),
    ("PROC_AGING", "老化测试", ["老化", "煲机"], 160,
     [("老化温度", "℃", 45, 70, 55), ("老化时长", "h", 4, 24, 8)]),
    ("PROC_FCT", "FCT功能测试", ["FCT", "功能测", "功能测试"], 170, []),
    ("PROC_OQC", "外观终检", ["终检", "OQC", "成品检"], 180, []),
    ("PROC_PACK", "包装", ["打包"], 190, []),
]

# ---------------------------------------------------------------- 设备型号
# (id, 名称, 别名, 用于工序id, 型号样例)
DEVICE_TYPES = [
    ("DEV_PRINTER", "锡膏印刷机", ["印刷机", "丝印机", "丝网印刷机"], "PROC_PRINT", "DEK Horizon 03iX"),
    ("DEV_SPI", "SPI锡膏检测仪", ["SPI机", "锡膏测厚仪"], "PROC_SPI", "Koh Young KY-8030"),
    ("DEV_MOUNTER", "贴片机", ["SMT贴片机", "打件机"], "PROC_PLACE", "ASM TX-2i"),
    ("DEV_REFLOW", "回流焊炉", ["回流炉", "回焊炉", "再流焊炉"], "PROC_REFLOW", "Heller 1936 EXL"),
    ("DEV_WAVE", "波峰焊炉", ["波峰炉"], "PROC_WAVE", "劲拓 NS-350"),
    ("DEV_AOI", "AOI检测设备", ["AOI机", "光学检测仪", "自动光学检查机"], "PROC_AOI", "Mirtec MV-6OMNI"),
    ("DEV_XRAY", "XRAY检测设备", ["X-RAY机", "X光机", "射线检查机"], "PROC_XRAY", "Phoenix v|tome|x"),
    ("DEV_DISPENSER", "自动点胶机", ["点胶机", "滴胶机"], "PROC_DISPENSE", "Nordson ASYMTEK S-920N"),
    ("DEV_PRESS", "真空压合机", ["压合机", "贴合机"], "PROC_PRESS", "航林 HL-600"),
    ("DEV_ROUTER", "铣刀分板机", ["分板机", "割板机"], "PROC_ROUT", " Genitec GAM-320A"),
    ("DEV_INJECT", "注塑机", ["啤机", "射出成型机"], "PROC_INJECT", "海天 MA2500II"),
    ("DEV_ULTRA", "超声波焊接机", ["超焊机", "超声焊机"], "PROC_ULTRA", "必能信 2000X"),
    ("DEV_COATER", "三防漆涂覆机", ["涂覆机", "喷涂机"], "PROC_COATING", "Asymtek SL-940E"),
    ("DEV_PLASMA", "等离子清洗机", ["Plasma机", "等离子处理机"], "PROC_PLASMA", "Diener Zepto"),
    ("DEV_AGING", "老化柜", ["老化房", "煲机柜"], "PROC_AGING", "自制 AG-8"),
    ("DEV_FCT", "FCT功能测试台", ["功能测试台", "FCT台架"], "PROC_FCT", "自制 FCT-12"),
]
LINES = ["一车间SMT-1线", "一车间SMT-2线", "二车间组装-3线", "二车间注塑-4线"]

# ---------------------------------------------------------------- 物料
# (id, 名称, 别名, 规格, 单位, 相关工序id列表)
MATERIALS = [
    ("MAT_PASTE_SAC", "无铅锡膏SAC305", ["锡膏", "焊膏", "SAC305锡膏"], "Sn96.5/Ag3.0/Cu0.5 T4", "瓶", ["PROC_PRINT", "PROC_REFLOW"]),
    ("MAT_PASTE_LEAD", "有铅锡膏Sn63Pb37", ["6337锡膏", "有铅焊膏"], "Sn63/Pb37 T3", "瓶", ["PROC_PRINT"]),
    ("MAT_BAR", "无铅焊锡条", ["锡条", "焊锡棒"], "SAC305", "kg", ["PROC_WAVE"]),
    ("MAT_FLUX", "免清洗助焊剂", ["助焊剂", "焊剂"], "RF-800", "L", ["PROC_WAVE"]),
    ("MAT_REDGLUE", "SMT红胶", ["红胶", "贴片胶"], "Loctite 3626", "支", ["PROC_DISPENSE", "PROC_PLACE"]),
    ("MAT_PCB", "FR4裸板", ["PCB", "裸板", "线路板", "电路板"], "FR4 1.6mm 双层", "片", ["PROC_PRINT", "PROC_WAVE", "PROC_IQC"]),
    ("MAT_STENCIL", "激光钢网", ["钢网", "网板"], "0.12mm 电抛光", "张", ["PROC_PRINT"]),
    ("MAT_RES", "片式电阻", ["电阻", "贴片电阻"], "0402/0603 全系列", "盘", ["PROC_PLACE"]),
    ("MAT_CAP", "片式电容", ["电容", "贴片电容", "MLCC"], "0402/0603 全系列", "盘", ["PROC_PLACE"]),
    ("MAT_IC", "集成电路IC", ["IC", "芯片", "集成块"], "QFN/BGA/SOP 系列", "盘", ["PROC_PLACE", "PROC_REFLOW"]),
    ("MAT_CONN", "板对板连接器", ["连接器", "接插件", "BTB"], "0.4mm pitch", "盘", ["PROC_PLACE", "PROC_PRESS"]),
    ("MAT_XTAL", "石英晶振", ["晶振", "晶体振荡器"], "26MHz 2016", "盘", ["PROC_PLACE"]),
    ("MAT_LED", "贴片LED灯珠", ["LED", "灯珠"], "2835 正白", "盘", ["PROC_PLACE"]),
    ("MAT_ABS", "ABS塑胶粒", ["ABS料", "ABS颗粒"], "PA-757K", "包", ["PROC_INJECT"]),
    ("MAT_PC", "PC塑胶粒", ["PC料", "聚碳酸酯粒"], "1100 透明", "包", ["PROC_INJECT"]),
    ("MAT_EPOXY", "环氧树脂胶", ["环氧胶", "结构胶"], "DP-460", "支", ["PROC_DISPENSE", "PROC_PRESS"]),
    ("MAT_COAT", "丙烯酸三防漆", ["三防漆", "保护漆", " conformal涂层"], "AC-46 透明", "桶", ["PROC_COATING"]),
    ("MAT_CLEAN", "电路板清洗剂", ["清洗剂", "洗板水"], "EC-70", "桶", ["PROC_PLASMA", "PROC_PRINT"]),
    ("MAT_SOLDER_PASTE_BATCH", "低温锡膏BiSnAg", ["低温锡膏", "Bi锡膏"], "Sn42/Bi57.6/Ag0.4", "瓶", ["PROC_PRINT", "PROC_REFLOW"]),
    ("MAT_LABEL", "耐高温标签", ["标签", "条码标"], "10×6mm PI", "卷", ["PROC_PACK", "PROC_PLACE"]),
]

# ---------------------------------------------------------------- 不良现象
# (id, 名称, 别名, 主工序id, 严重度, 细分父id或None, 关联物料id列表)
DEFECTS = [
    ("DEF_COLD", "虚焊", ["假焊", "冷焊", "焊点未熔合"], "PROC_REFLOW", "高", None, ["MAT_PASTE_SAC", "MAT_IC"]),
    ("DEF_BRIDGE", "连锡", ["桥接", "锡桥", "连桥", "搭锡"], "PROC_REFLOW", "高", None, ["MAT_PASTE_SAC", "MAT_STENCIL"]),
    ("DEF_TOMB", "立碑", ["曼哈顿现象", "墓碑效应", "竖立"], "PROC_REFLOW", "高", None, ["MAT_RES", "MAT_CAP"]),
    ("DEF_SHIFT", "元件偏移", ["偏移", "错位", "偏位", "元件移位"], "PROC_PLACE", "中", None, ["MAT_IC", "MAT_CONN"]),
    ("DEF_LESS", "少锡", ["锡量不足", "焊点瘦小", "锡少"], "PROC_PRINT", "中", None, ["MAT_PASTE_SAC", "MAT_STENCIL"]),
    ("DEF_MORE", "多锡", ["锡量过多", "锡厚", "胖焊点"], "PROC_PRINT", "中", None, ["MAT_PASTE_SAC"]),
    ("DEF_BALL", "锡珠", ["锡球", "焊锡珠", "锡豆"], "PROC_REFLOW", "中", None, ["MAT_PASTE_SAC"]),
    ("DEF_HEAD", "枕头效应", ["HoP", "头枕效应", "灯头效应"], "PROC_REFLOW", "高", None, ["MAT_IC", "MAT_PASTE_SAC"]),
    ("DEF_VOID", "焊接空洞", ["空洞", "气泡孔", "针孔"], "PROC_REFLOW", "中", None, ["MAT_PASTE_SAC", "MAT_IC"]),
    ("DEF_MISSING", "漏件", ["缺件", "少件", "漏贴"], "PROC_PLACE", "高", None, ["MAT_RES", "MAT_CAP", "MAT_IC"]),
    ("DEF_SIDE", "侧立", ["横立", "元件侧卧"], "PROC_PLACE", "中", None, ["MAT_RES", "MAT_CAP"]),
    ("DEF_LIFT", "翘脚", ["引脚翘起", "共面不良", "翘脚变形"], "PROC_PLACE", "中", None, ["MAT_IC", "MAT_CONN"]),
    ("DEF_NEEDLE", "锡尖", ["拉尖", "针状锡", "冰锥"], "PROC_WAVE", "低", None, ["MAT_BAR", "MAT_FLUX"]),
    ("DEF_DULL", "焊点不光亮", ["焊点暗淡", "沙面焊点", "麻面"], "PROC_WAVE", "低", None, ["MAT_BAR"]),
    ("DEF_PRINT_SHIFT", "印刷偏位", ["丝印偏位", "焊盘偏移", "印刷错位"], "PROC_PRINT", "中", "DEF_SHIFT", ["MAT_PCB", "MAT_STENCIL"]),
    ("DEF_BGA_VOID", "BGA空洞", ["BGA气泡", "球栅阵列空洞"], "PROC_REFLOW", "高", "DEF_VOID", ["MAT_IC", "MAT_PASTE_SAC"]),
    ("DEF_QFN_BRIDGE", "QFN连锡", ["QFN桥接", "QFN引脚搭接"], "PROC_REFLOW", "高", "DEF_BRIDGE", ["MAT_IC"]),
    ("DEF_GLUE_OVER", "点胶溢胶", ["溢胶", "胶量过大", "胶水渗出"], "PROC_DISPENSE", "中", None, ["MAT_EPOXY", "MAT_REDGLUE"]),
    ("DEF_GLUE_BREAK", "点胶断胶", ["断胶", "胶线不连续", "少胶"], "PROC_DISPENSE", "中", None, ["MAT_EPOXY"]),
    ("DEF_BUBBLE_LAM", "压合气泡", ["贴合气泡", "压合空鼓"], "PROC_PRESS", "中", None, ["MAT_EPOXY"]),
    ("DEF_SHORT_INJECT", "缺胶", ["短射", "打不满", "充填不足"], "PROC_INJECT", "高", None, ["MAT_ABS", "MAT_PC"]),
    ("DEF_FLASH", "飞边", ["毛边", "批锋", "溢边"], "PROC_INJECT", "中", None, ["MAT_ABS"]),
    ("DEF_SINK", "缩水", ["缩痕", "凹陷"], "PROC_INJECT", "中", None, ["MAT_PC", "MAT_ABS"]),
    ("DEF_BUBBLE_INJ", "注塑气泡", ["真空泡", "制品气泡", "内部空洞"], "PROC_INJECT", "中", None, ["MAT_PC"]),
    ("DEF_SILVER", "银丝纹", ["银纹", "料花", "水纹"], "PROC_INJECT", "中", None, ["MAT_ABS"]),
    ("DEF_BURN", "烧焦", ["焦痕", "烧黑", "炭化纹"], "PROC_INJECT", "低", None, ["MAT_ABS"]),
    ("DEF_WARP", "翘曲", ["变形", "扭曲", "壳体翘曲"], "PROC_INJECT", "中", None, ["MAT_PC"]),
    ("DEF_ULTRA_CRACK", "超声波压裂", ["超声裂纹", "焊裂", "压伤开裂"], "PROC_ULTRA", "中", None, ["MAT_ABS"]),
    ("DEF_COAT_POOL", "三防漆堆积", ["堆漆", "漆层过厚", "积液"], "PROC_COATING", "低", None, ["MAT_COAT"]),
    ("DEF_COAT_MISS", "漏涂", ["缺涂", "三防漆漏喷", "涂覆盲区"], "PROC_COATING", "中", None, ["MAT_COAT"]),
    ("DEF_EARLY_FAIL", "早期失效", ["老化失效", "DOA", "开机不良"], "PROC_AGING", "高", None, ["MAT_IC"]),
    ("DEF_PARAM_DRIFT", "参数漂移", ["性能漂移", "指标超差", "参数跑飞"], "PROC_AGING", "中", None, ["MAT_XTAL", "MAT_IC"]),
]

# 产品（在制品型号，物料类实体，用于把原因/对策参数化以拉开图谱规模）
# kind: pcba=纯电路板（无注塑/超声壳件工序）  device=整机型（含壳件成型/焊接工序）
# (id, 规范名, 短别名, kind)
PRODUCTS = [
    ("MAT_PD_T01", "T01智能手表主板", "T01手表板", "device"),
    ("MAT_PD_T02", "T02运动手环主板", "T02手环板", "device"),
    ("MAT_PD_P30", "P30智能手机主板", "P30手机板", "device"),
    ("MAT_PD_P31", "P31智能手机主板", "P31手机板", "device"),
    ("MAT_PD_D12", "D12开关电源板", "D12电源板", "pcba"),
    ("MAT_PD_D15", "D15快充电源板", "D15快充板", "pcba"),
    ("MAT_PD_B7", "B7蓝牙通信模组", "B7蓝牙模组", "pcba"),
    ("MAT_PD_B9", "B9 WiFi6通信模组", "B9 WiFi模组", "pcba"),
    ("MAT_PD_R3", "R3无线路由器主板", "R3路由器板", "device"),
    ("MAT_PD_R6", "R6物联网网关主板", "R6网关板", "pcba"),
    ("MAT_PD_W2", "W2真无线耳机主板", "W2耳机板", "device"),
    ("MAT_PD_S5", "S5智能音箱主板", "S5音箱板", "device"),
    ("MAT_PD_C2", "C2车载充电器PCBA", "C2车充板", "device"),
    ("MAT_PD_M4", "M4监护仪主控板", "M4监护仪板", "device"),
    ("MAT_PD_K8", "K8空调内机控制板", "K8空调控制板", "device"),
    ("MAT_PD_V5", "V5智能门锁主板", "V5门锁板", "device"),
    ("MAT_PD_L1", "L1LED灯具驱动板", "L1驱动板", "pcba"),
    ("MAT_PD_E3", "E3电子价签控制板", "E3价签板", "pcba"),
    ("MAT_PD_H6", "H6手环心率小板", "H6心率小板", "pcba"),
    ("MAT_PD_Q1", "Q1 TWS充电盒主板", "Q1充电盒板", "device"),
    ("MAT_PD_N4", "N4笔记本电源板", "N4笔电电源板", "pcba"),
    ("MAT_PD_A8", "A8投影仪主控板", "A8投影仪板", "device"),
    ("MAT_PD_U2", "U2 UPS控制板", "U2 UPS板", "pcba"),
    ("MAT_PD_F9", "F9直流风扇控制板", "F9风扇板", "pcba"),
    ("MAT_PD_G3", "G3智能网关主板", "G3网关板", "pcba"),
    ("MAT_PD_H2", "H2智能体脂秤主板", "H2体脂秤板", "device"),
    ("MAT_PD_J6", "J6扫地机器人主板", "J6扫地机板", "device"),
    ("MAT_PD_K1", "K1智能温控器面板", "K1温控器板", "device"),
    ("MAT_PD_Y7", "Y7无人机飞控板", "Y7飞控板", "device"),
    ("MAT_PD_Z4", "Z4智能插座PCBA", "Z4插座板", "pcba"),
    ("MAT_PD_X9", "X9收银机主控板", "X9收银机板", "device"),
    ("MAT_PD_V8", "V8 VR眼镜主板", "V8 VR眼镜板", "device"),
    ("MAT_PD_T8", "T8智能门铃主板", "T8门铃板", "device"),
    ("MAT_PD_E6", "E6跑步机仪表板", "E6跑步机板", "device"),
    ("MAT_PD_M9", "M9指夹血氧仪板", "M9血氧仪板", "device"),
    ("MAT_PD_R2", "R2电视机顶盒主板", "R2机顶盒板", "device"),
    ("MAT_PD_W5", "W5智能水杯控制板", "W5水杯板", "device"),
    ("MAT_PD_C7", "C7行车记录仪板", "C7记录仪板", "device"),
    ("MAT_PD_D3", "D3护眼台灯驱动板", "D3台灯驱动", "pcba"),
    ("MAT_PD_S8", "S8直播声卡主板", "S8声卡板", "pcba"),
    ("MAT_PD_Q5", "Q5宠物喂食器主板", "Q5喂食器板", "device"),
    ("MAT_PD_L9", "L9LED灯管驱动电源", "L9灯管驱动", "pcba"),
    ("MAT_PD_B2", "B2温湿度采集板", "B2采集板", "pcba"),
    ("MAT_PD_P6", "P6标签打印机接口板", "P6打印机板", "device"),
    ("MAT_PD_I1", "I1逆变器控制板", "I1逆变板", "pcba"),
    ("MAT_PD_E9", "E9充电桩主控板", "E9充电桩板", "device"),
    ("MAT_PD_U7", "U7超声波水表板", "U7水表板", "pcba"),
    ("MAT_PD_N8", "N8网络录像机主板", "N8录像机板", "device"),
]

# 仅整机型产品经历的工序（注塑/超声焊壳件）
DEVICE_ONLY_PROCS = {"PROC_INJECT", "PROC_ULTRA"}

# ---------------------------------------------------------------- 参数化原因库
# kind: proc_param(原因文本含工序参数偏差，对策为调参，target=工序)
#       material(物料类，对策作用于物料)  device(设备/工装类)  method(法)  env(环)
# (key, factor, 原因模板, 对策模板, 动作类型, 目标类型, 适用工序集合[]表示全部有该参数的工序)
CAUSE_SPECS = [
    ("PARAM_HIGH", "法", "{proc}过程中{param}偏高，超出工艺窗口上限",
     "将{proc}{param}下调{delta}{unit}，由{old_v}{unit}调整至{new_v}{unit}并连续监控3批", "调参", "proc"),
    ("PARAM_LOW", "法", "{proc}过程中{param}偏低，低于工艺窗口下限",
     "将{proc}{param}上调{delta}{unit}，由{old_v}{unit}调整至{new_v}{unit}并连续监控3批", "调参", "proc"),
    ("PARAM_UNSTABLE", "机", "{dev}{param}波动大、参数漂移，过程能力Cpk不足",
     "对{dev}执行参数校准与预防性维护，恢复{param}过程能力，加严首件确认", "设备维护", "dev"),
    ("MAT_MOIST", "料", "{mat}受潮，高温过程中水分汽化，形成气孔与挥发缺陷",
     "{mat}使用前按材料规范烘烤除湿并复测含水率，严格管控开封暴露时长与先进先出", "更换", "mat"),
    ("MAT_OXIDE", "料", "{mat}氧化变质，焊接表面可焊性下降",
     "更换{mat}批次并要求供应商提供可焊性报告，上线前增加等离子清洗", "更换", "mat"),
    ("MAT_EXPIRED", "料", "{mat}超过有效期，性能衰减",
     "隔离超期{mat}并走MRB评审，库房按FEFO原则发料并设置到期预警", "更换", "mat"),
    ("MAT_SPEC", "料", "{mat}来料规格与BOM不符",
     "冻结该批{mat}并退换货，IQC加严来料规格抽检比例至AQL 0.65", "更换", "mat"),
    ("STENCIL_DIRTY", "机", "钢网开口堵塞/残留干膜，下锡不良",
     "按2小时频次清洗钢网开口并检查张力，干膜板立即下线清洗", "清洁", "dev"),
    ("STENCIL_WORN", "机", "钢网开口磨损变形，印刷体积失控",
     "更换新激光钢网并做首件SPI全检，建立钢网印刷次数寿命台账", "更换", "dev"),
    ("NOZZLE_DIRTY", "机", "贴片机吸嘴堵塞/磨损，取料不稳",
     "清洗或更换吸嘴并重新做吸嘴中心校正，班中增加吸嘴真空点检", "清洁", "dev"),
    ("FEEDER_BAD", "机", "飞达送料步距异常，供料位置偏移",
     "更换异常飞达并送修校验，台账记录该飞达不良履历", "设备维护", "dev"),
    ("DISPENSE_BLOCK", "机", "点胶喷嘴或胶阀堵塞，出胶通道不畅",
     "拆洗或更换点胶喷嘴与胶阀，校验出胶量并纳入周保养点检", "清洁", "dev"),
    ("COAT_BLOCK", "机", "三防漆喷涂喷嘴堵塞、气路不畅，局部成膜不连续",
     "清洗喷涂喷嘴与气路过滤器，每班试喷确认雾化状态并记录", "清洁", "dev"),
    ("PROFILE_WRONG", "法", "回流温度曲线设置不当，未按产品分类调用",
     "按产品热容量重新测温并固化温度曲线程序，过炉扫码防错", "调参", "proc"),
    ("PREHEAT_SHORT", "法", "预热不充分，助焊剂活化不足",
     "延长恒温区时间并复核测温板曲线，确保助焊剂充分活化", "调参", "proc"),
    ("COOL_FAST", "法", "冷却速率过快形成应力缺陷",
     "调缓冷却区风量与链速，控制冷却速率在工艺窗口内", "调参", "proc"),
    ("FLUX_LOW", "料", "助焊剂活性不足/比重偏低",
     "调整助焊剂比重至标准范围并定期检测，更换在有效期内的焊剂", "更换", "mat"),
    ("PAD_DESIGN", "法", "焊盘设计不对称，热容量不均衡",
     "输出DFM建议优化焊盘与阻焊定义，临时采用阶梯钢网平衡锡量", "工艺优化", "proc"),
    ("COPLANAR", "料", "元器件共面性超标，贴装后引脚与锡膏接触不良",
     "上料前增加元件共面性抽检，超标批次退回供应商", "加严检验", "mat"),
    ("MOLD_TEMP_LOW", "机", "模具温度偏低，熔体流动充填阻力大",
     "提高模具温度至标准窗口并稳定3模后再生产，检查模温机水路", "调参", "dev"),
    ("PRESS_LOW", "法", "注射/保压压力不足，型腔未完全充填",
     "分级提高注射与保压压力并验证重量一致性，避免飞边产生", "调参", "proc"),
    ("DRY_INSUF", "法", "原料预干燥不充分，含水率超标",
     "按料规执行除湿干燥（ABS 80℃/2h）并记录露点，停机超时重新烘干", "工艺优化", "mat"),
    ("VENT_BAD", "机", "模具排气不良，型腔气体无法排出",
     "清理加深模具排气槽，短射验证充填末端包风位置", "设备维护", "dev"),
    ("AMPLITUDE_HIGH", "法", "超声焊接能量过大，塑件被压裂",
     "下调焊接振幅与压力并做焊头平面度校准，按强度-外观双指标定标", "调参", "proc"),
    ("GLUE_VISC", "料", "胶水黏度随温度变化，出胶量不稳定",
     "点胶车间恒温管控并按黏度曲线调整点胶压力，胶水回温后使用", "调参", "mat"),
    ("COAT_THICK", "法", "涂覆走枪速度慢、湿膜过厚形成积液",
     "优化走枪速度与喷涂压力，膜厚抽检纳入每班首件", "调参", "proc"),
    ("ESD", "环", "防静电措施失效，静电损伤敏感器件",
     "复测工位静电接地与腕带，增加离子风机并做ESD日点检", "环境整改", "proc"),
]

CAUSE_BY_DEFECT = {
    "DEF_COLD": ["PROFILE_WRONG", "MAT_OXIDE", "PREHEAT_SHORT", "MAT_MOIST", "PARAM_UNSTABLE"],
    "DEF_BRIDGE": ["STENCIL_WORN", "PARAM_HIGH", "PAD_DESIGN", "PROFILE_WRONG", "PARAM_UNSTABLE"],
    "DEF_TOMB": ["PAD_DESIGN", "COPLANAR", "PARAM_HIGH", "NOZZLE_DIRTY"],
    "DEF_SHIFT": ["NOZZLE_DIRTY", "FEEDER_BAD", "COPLANAR", "PARAM_UNSTABLE"],
    "DEF_LESS": ["STENCIL_DIRTY", "PARAM_LOW", "STENCIL_WORN", "MAT_MOIST"],
    "DEF_MORE": ["STENCIL_WORN", "PARAM_HIGH", "PAD_DESIGN"],
    "DEF_BALL": ["MAT_MOIST", "PROFILE_WRONG", "STENCIL_DIRTY", "PREHEAT_SHORT"],
    "DEF_HEAD": ["PROFILE_WRONG", "PARAM_UNSTABLE", "MAT_OXIDE", "MAT_MOIST"],
    "DEF_VOID": ["MAT_MOIST", "PROFILE_WRONG", "PREHEAT_SHORT", "PAD_DESIGN"],
    "DEF_MISSING": ["NOZZLE_DIRTY", "FEEDER_BAD", "PARAM_UNSTABLE"],
    "DEF_SIDE": ["NOZZLE_DIRTY", "COPLANAR", "FEEDER_BAD"],
    "DEF_LIFT": ["COPLANAR", "FEEDER_BAD", "MAT_SPEC"],
    "DEF_NEEDLE": ["FLUX_LOW", "PARAM_UNSTABLE", "PARAM_HIGH"],
    "DEF_DULL": ["MAT_OXIDE", "FLUX_LOW", "MAT_EXPIRED"],
    "DEF_PRINT_SHIFT": ["PARAM_UNSTABLE", "STENCIL_WORN", "PAD_DESIGN"],
    "DEF_BGA_VOID": ["MAT_MOIST", "PROFILE_WRONG", "PREHEAT_SHORT"],
    "DEF_QFN_BRIDGE": ["STENCIL_WORN", "PAD_DESIGN", "PARAM_HIGH"],
    "DEF_GLUE_OVER": ["GLUE_VISC", "PARAM_HIGH", "PARAM_UNSTABLE", "DISPENSE_BLOCK"],
    "DEF_GLUE_BREAK": ["GLUE_VISC", "PARAM_LOW", "DISPENSE_BLOCK"],
    "DEF_BUBBLE_LAM": ["PARAM_LOW", "GLUE_VISC", "PARAM_UNSTABLE"],
    "DEF_SHORT_INJECT": ["MOLD_TEMP_LOW", "PRESS_LOW", "PARAM_LOW", "VENT_BAD", "MAT_SPEC"],
    "DEF_FLASH": ["PRESS_LOW", "PARAM_HIGH", "MOLD_TEMP_LOW"],
    "DEF_SINK": ["PRESS_LOW", "PARAM_LOW", "MAT_SPEC"],
    "DEF_BUBBLE_INJ": ["DRY_INSUF", "VENT_BAD", "PARAM_HIGH"],
    "DEF_SILVER": ["DRY_INSUF", "MAT_MOIST", "MAT_SPEC"],
    "DEF_BURN": ["PARAM_HIGH", "VENT_BAD", "PARAM_UNSTABLE"],
    "DEF_WARP": ["PARAM_HIGH", "MOLD_TEMP_LOW", "PARAM_UNSTABLE"],
    "DEF_ULTRA_CRACK": ["AMPLITUDE_HIGH", "PARAM_HIGH", "PARAM_UNSTABLE"],
    "DEF_COAT_POOL": ["COAT_THICK", "PARAM_HIGH", "COAT_BLOCK"],
    "DEF_COAT_MISS": ["PARAM_LOW", "PARAM_UNSTABLE", "COAT_BLOCK"],
    "DEF_EARLY_FAIL": ["ESD", "MAT_SPEC", "PARAM_UNSTABLE"],
    "DEF_PARAM_DRIFT": ["ESD", "MAT_EXPIRED", "PARAM_UNSTABLE"],
}

# 原因模板需要工序参数时，在该工序参数表中按参数名关键词挑选
PARAM_KEYWORD = {
    "DEF_BRIDGE": ("锡膏印刷", "锡膏厚度"),
    "DEF_LESS": ("锡膏印刷", "锡膏厚度"),
    "DEF_MORE": ("锡膏印刷", "锡膏厚度"),
    "DEF_COLD": ("回流焊接", "峰值温度"),
    "DEF_VOID": ("回流焊接", "恒温区时间"),
    "DEF_BGA_VOID": ("回流焊接", "恒温区时间"),
    "DEF_HEAD": ("回流焊接", "峰值温度"),
    "DEF_BALL": ("回流焊接", "升温斜率"),
    "DEF_TOMB": ("锡膏印刷", "锡膏厚度"),
    "DEF_NEEDLE": ("波峰焊接", "锡炉温度"),
    "DEF_DULL": ("波峰焊接", "锡炉温度"),
    "DEF_SHORT_INJECT": ("注塑成型", "注射压力"),
    "DEF_FLASH": ("注塑成型", "保压压力"),
    "DEF_SINK": ("注塑成型", "保压压力"),
    "DEF_BUBBLE_INJ": ("注塑成型", "料筒温度"),
    "DEF_SILVER": ("注塑成型", "料筒温度"),
    "DEF_BURN": ("注塑成型", "料筒温度"),
    "DEF_WARP": ("注塑成型", "模具温度"),
    "DEF_GLUE_OVER": ("点胶", "点胶压力"),
    "DEF_GLUE_BREAK": ("点胶", "点胶压力"),
    "DEF_BUBBLE_LAM": ("压合", "压合压力"),
    "DEF_ULTRA_CRACK": ("超声波焊接", "焊接振幅"),
    "DEF_COAT_POOL": ("三防涂覆", "膜厚"),
    "DEF_COAT_MISS": ("三防涂覆", "喷涂压力"),
    "DEF_SHIFT": ("贴片", "贴装压力"),
    "DEF_MISSING": ("贴片", "吸嘴真空度"),
    "DEF_PRINT_SHIFT": ("锡膏印刷", "印刷压力"),
    "DEF_EARLY_FAIL": ("老化测试", "老化温度"),
    "DEF_PARAM_DRIFT": ("老化测试", "老化时长"),
}


@dataclass
class Entity:
    id: str
    label: str
    name: str
    aliases: list[str] = field(default_factory=list)
    attrs: dict = field(default_factory=dict)


@dataclass
class CaseCause:
    cause: Entity
    spec_key: str
    countermeasure: Entity          # 工程对策（8D D6，通常只出现在工艺通知单）
    std_countermeasure: Entity      # 标准化/横展对策（8D D7：固化规程、更新FMEA、培训横展）
    temp_countermeasure: Entity     # 临时遏制措施（8D D3，可能出现在异常单）


@dataclass
class Scenario:
    """一条异常场景：产品×工序×设备实例×不良×（原因→对策），语料生成的基本单元。"""
    product: Entity
    process: Entity
    device: Entity
    material: Entity
    defect: Entity
    case_causes: list[CaseCause]


def build_kb(seed: int = 42) -> dict:
    """构建规范化实体池与别名索引。返回实体字典 + 全部实体列表。"""
    rng = random.Random(seed)
    entities: dict[str, Entity] = {}

    proc_by_id: dict[str, Entity] = {}
    for pid, name, aliases, seq, params in PROCESSES:
        e = Entity(pid, "Process", name, list(aliases),
                   {"seq": seq, "std_params": [f"{p[0]}({p[1]})" for p in params]})
        entities[pid] = e
        proc_by_id[pid] = e

    dev_by_type: dict[str, list[Entity]] = {}
    for did, name, aliases, proc_id, model in DEVICE_TYPES:
        dev_by_type[did] = []
        for i, line in enumerate(LINES):
            eid = f"{did}_L{i+1}"
            inst_name = f"{line}{name}"
            e = Entity(eid, "Device", inst_name,
                       [name] + list(aliases) + [f"{line}{a}" for a in aliases[:2]],
                       {"model": model, "line": line, "device_type": did})
            entities[eid] = e
            dev_by_type[did].append(e)

    mat_by_id: dict[str, Entity] = {}
    for mid, name, aliases, spec, unit, procs in MATERIALS:
        e = Entity(mid, "Material", name, list(aliases), {"spec": spec, "unit": unit})
        entities[mid] = e
        mat_by_id[mid] = e
    product_kind: dict[str, str] = {}
    for pid, name, short, kind in PRODUCTS:
        e = Entity(pid, "Material", name, [short],
                   {"spec": "在制品PCBA/整机", "unit": "片", "is_product": True})
        entities[pid] = e
        mat_by_id[pid] = e
        product_kind[pid] = kind

    defect_by_id: dict[str, Entity] = {}
    for did, name, aliases, proc_id, sev, parent, mats in DEFECTS:
        e = Entity(did, "Defect", name, list(aliases), {"severity": sev, "parent": parent})
        entities[did] = e
        defect_by_id[did] = e

    return {
        "entities": entities,
        "processes": proc_by_id,
        "devices_by_type": dev_by_type,
        "materials": mat_by_id,
        "defects": defect_by_id,
        "product_kind": product_kind,
        "rng": rng,
    }


def line_index(product_id: str) -> int:
    """产品 → 产线的确定性路由（跨进程稳定）。"""
    import hashlib as _hl

    return int(_hl.md5(product_id.encode("utf-8")).hexdigest(), 16) % len(LINES)


def _param_for_process(proc_tuple, keyword: str):
    params = proc_tuple[4]
    for p in params:
        if keyword in p[0]:
            return p
    return params[0] if params else None


def _proc_tuple(proc_id: str):
    return next(p for p in PROCESSES if p[0] == proc_id)


def _dev_type_for_process(proc_id: str) -> str | None:
    for did, _, _, pid, _ in DEVICE_TYPES:
        if pid == proc_id:
            return did
    return None


def build_scenarios(kb: dict, seed: int = 42) -> list[Scenario]:
    """为 产品×不良 组合生成场景；原因/对策按产品×工序参数化，构成独立实体。"""
    rng = random.Random(seed + 1000)
    scenarios: list[Scenario] = []

    cause_spec_map = {s[0]: s for s in CAUSE_SPECS}
    # 已生成原因实体去重：(产品id, 工序id, spec_key, 偏差方向参数名?) -> cause/countermeasure
    cause_pool: dict[tuple, CaseCause] = {}

    for prod_idx, (prod_id, _prod_name, _short, kind) in enumerate(PRODUCTS):
        product = kb["materials"][prod_id]
        # 按产品类型过滤可经历工序的不良：纯 PCBA 不经历注塑/超声焊壳件
        eligible = [
            d for d in DEFECTS
            if kind == "device" or d[3] not in DEVICE_ONLY_PROCS
        ]
        # 每个产品覆盖 11-13 种不良（确定性）
        chosen = rng.sample(eligible, k=min(11 + (prod_idx % 3), len(eligible)))
        for d_idx, drow in enumerate(chosen):
            defect_id = drow[0]
            _, _, _, proc_id, _, parent, mats = drow
            process = kb["processes"][proc_id]
            dev_type = _dev_type_for_process(proc_id)
            device = None
            if dev_type:
                devs = kb["devices_by_type"][dev_type]
                device = devs[line_index(prod_id) % len(devs)]
            # 物料：优先该不良关联物料，否则取产品自身
            mat_id = rng.choice(mats) if mats else prod_id
            material = kb["materials"].get(mat_id, product)

            spec_keys = CAUSE_BY_DEFECT[defect_id]
            case_causes: list[CaseCause] = []
            for sk in spec_keys:
                spec = cause_spec_map[sk]
                key_factor, direction = sk, ""
                # PARAM_HIGH/LOW 时把方向并入 key，取该不良对应工序的实际参数
                base_sk = sk
                if sk in ("PARAM_HIGH", "PARAM_LOW"):
                    kw = PARAM_KEYWORD.get(defect_id)
                    if kw is None:
                        continue
                    pt = _proc_tuple(proc_id)
                    param = _param_for_process(pt, kw[1])
                    if param is None:
                        continue
                    pname, punit, lo, hi, std = param
                    cache_key = (prod_id, proc_id, sk, pname)
                    direction = "HIGH" if sk == "PARAM_HIGH" else "LOW"
                else:
                    pname = punit = None
                    cache_key = (prod_id, proc_id, sk, defect_id if sk in ("MAT_MOIST", "MAT_OXIDE", "MAT_EXPIRED", "MAT_SPEC", "COPLANAR", "FLUX_LOW") else "")

                if cache_key in cause_pool:
                    cc = cause_pool[cache_key]
                else:
                    cc = _make_cause_cm(
                        spec, kb, prod_id, proc_id, pname, punit, lo if pname else None,
                        hi if pname else None, std if pname else None, rng,
                    )
                    cause_pool[cache_key] = cc
                    kb["entities"][cc.cause.id] = cc.cause
                    kb["entities"][cc.countermeasure.id] = cc.countermeasure
                    kb["entities"][cc.std_countermeasure.id] = cc.std_countermeasure
                    kb["entities"][cc.temp_countermeasure.id] = cc.temp_countermeasure
                case_causes.append(cc)

            scenarios.append(Scenario(product, process, device, material,
                                      kb["defects"][defect_id], case_causes))
    return scenarios


# 每个原因的 4-8 字要旨，用于临时/标准化对策的唯一性限定
CAUSE_GIST = {
    "PARAM_HIGH": "参数偏高", "PARAM_LOW": "参数偏低", "PARAM_UNSTABLE": "参数漂移",
    "MAT_MOIST": "物料受潮", "MAT_OXIDE": "物料氧化", "MAT_EXPIRED": "物料超期",
    "MAT_SPEC": "规格不符", "STENCIL_DIRTY": "钢网堵塞", "STENCIL_WORN": "钢网磨损",
    "NOZZLE_DIRTY": "吸嘴堵塞", "FEEDER_BAD": "飞达异常", "DISPENSE_BLOCK": "胶阀堵塞",
    "COAT_BLOCK": "喷嘴堵塞", "PROFILE_WRONG": "曲线不当", "PREHEAT_SHORT": "预热不足",
    "COOL_FAST": "冷却过快", "FLUX_LOW": "焊剂活性低", "PAD_DESIGN": "焊盘设计",
    "COPLANAR": "共面性超标", "MOLD_TEMP_LOW": "模温偏低", "PRESS_LOW": "压力不足",
    "DRY_INSUF": "干燥不足", "VENT_BAD": "模具排气差", "AMPLITUDE_HIGH": "能量过大",
    "GLUE_VISC": "胶黏度波动", "COAT_THICK": "湿膜过厚", "ESD": "静电防护失效",
}

# 8D D3 临时遏制措施模板（不解决根因，仅隔离风险，与根因对策形成对照）
TEMP_ACTIONS = [
    "隔离当批次在制品并挂红色待判标识，组织全检后按结果放行",
    "该工位临时调整为100%全检，由检验组长复判后流转",
    "暂停该工序生产，保留现场实物与参数记录，通知工程质量联合分析",
    "对在库同批次产品追溯复查，出货端加严抽检至AQL 0.4",
    "切换备用设备/备用料盘生产，异常设备停用并挂故障牌",
]

# 8D D7 标准化/横展对策模板（按工程对策动作类型选择，目标恒含责任工序）
STD_ACTIONS = {
    "调参": "更新工序工艺参数表与作业指导书，对操作员开展专项培训，并将参数窗口横展至同类产品",
    "更换": "更新IQC来料检验规范与供应商履历卡，将该失效模式纳入PFMEA库并通报SQE横展",
    "清洁": "修订设备清洁与钢网管理规程，将该检查项纳入TPM点检表并对设备组培训",
    "设备维护": "修订设备预防性维护(PM)规程与备件寿命标准，更新点检表并培训设备组",
    "工艺优化": "将改善措施固化进DFM设计规范，更新PFMEA控制计划，并横展至同类工序",
    "加严检验": "更新检验作业指导书判定标准，加严抽检比例并对检验员做GR&R一致性培训",
    "环境整改": "更新ESD/温湿度管理规范与点检频次，纳入班组日常稽核并横展所有产线",
}


def _make_cause_cm(spec, kb, prod_id, proc_id, pname, punit, lo, hi, std, rng) -> CaseCause:
    sk, factor, cause_tmpl, cm_tmpl, action, target_kind = spec
    product = kb["materials"][prod_id]
    process = kb["processes"][proc_id]
    dev_type = _dev_type_for_process(proc_id)
    device_type_name = next((d[1] for d in DEVICE_TYPES if d[0] == dev_type), process.name + "设备")

    fmt_kwargs = {
        "proc": process.name,
        "dev": device_type_name,
        "mat": "",
        "param": pname or "",
        "unit": punit or "",
    }

    if sk in ("PARAM_HIGH", "PARAM_LOW"):
        span = max(hi - lo, 1)
        if sk == "PARAM_HIGH":
            old_v = round(hi + span * 0.12, 2)
            new_v = round(std + (hi - std) * 0.4, 2)
        else:
            old_v = round(lo - span * 0.12, 2)
            new_v = round(std - (std - lo) * 0.4, 2)
        delta = round(abs(old_v - new_v), 2)
        fmt_kwargs.update(old_v=old_v, new_v=new_v, delta=delta)
    else:
        fmt_kwargs.update(old_v="", new_v="", delta="")

    # 物料类原因绑定具体物料：优先工序关联物料，否则产品
    proc_mats = [m for m in MATERIALS if proc_id in m[5]]
    if sk.startswith("MAT_") or sk in ("COPLANAR", "FLUX_LOW", "DRY_INSUF"):
        mat_row = rng.choice(proc_mats) if proc_mats else MATERIALS[0]
        fmt_kwargs["mat"] = kb["materials"][mat_row[0]].name
        bound_mat_id = mat_row[0]
    else:
        bound_mat_id = prod_id

    cause_name = f"{product.name}：{cause_tmpl.format(**fmt_kwargs)}"
    cm_name = f"{product.name}：{cm_tmpl.format(**fmt_kwargs)}"

    cid = f"CAU_{prod_id.removeprefix('MAT_')}_{proc_id.removeprefix('PROC_')}_{sk}"
    if pname:
        cid += "_" + hashlib.md5(pname.encode("utf-8")).hexdigest()[:6].upper()
    mid = "CM_" + cid.removeprefix("CAU_")

    # 临时遏制措施：由原因 id 确定性选取，跨进程稳定
    temp_idx = int(hashlib.md5(cid.encode()).hexdigest(), 16) % len(TEMP_ACTIONS)
    tcid = "CMT_" + cid.removeprefix("CAU_")
    # 标准化/横展对策：按工程对策动作类型选取
    sid = "CMS_" + cid.removeprefix("CAU_")
    std_text = STD_ACTIONS.get(action, STD_ACTIONS["工艺优化"])
    gist = CAUSE_GIST.get(sk, sk)
    qualifier = f"（{process.name}·{gist}）"

    cause = Entity(cid, "Cause", cause_name, [], {"factor_type": factor})
    cm = Entity(mid, "Countermeasure", cm_name, [],
                {"action_type": action, "target_kind": target_kind,
                 "target_proc": proc_id if target_kind == "proc" else None,
                 "target_dev_type": dev_type if target_kind == "dev" else None,
                 "target_mat": bound_mat_id if target_kind == "mat" else None})
    std_cm = Entity(
        sid, "Countermeasure",
        f"{product.name}：{std_text}{qualifier}", [],
        {"action_type": "标准化", "target_kind": "proc", "target_proc": proc_id,
         "target_dev_type": dev_type if action in ("清洁", "设备维护") else None},
    )
    temp_cm = Entity(
        tcid, "Countermeasure",
        f"{product.name}：{TEMP_ACTIONS[temp_idx]}{qualifier}", [],
        {"action_type": "临时遏制", "target_kind": "proc", "target_proc": proc_id},
    )
    return CaseCause(cause, sk, cm, std_cm, temp_cm)
