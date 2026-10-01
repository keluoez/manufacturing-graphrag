# kg-manufacturing · Frontend

制造工艺质量知识图谱平台前端：React 18 + TypeScript + Vite + AntV G6 + ECharts。

- `src/pages/PipelineFlow.tsx`：Dify 风格九节点流水线编排（SSE 实时状态/日志）
- `src/pages/GraphBrowser.tsx`：G6 力导向归因子图浏览
- `src/pages/QaCompare.tsx`：BM25 / 向量 / GraphRAG 三路召回与归因路径对比
- `src/pages/EvalDashboard.tsx`：ECharts 评测看板（F1 / Hit@3 / 消融 / 逐题明细）

## 开发

```powershell
npm install
npm run dev      # http://localhost:5173 ，已代理 /api → http://127.0.0.1:8010
```

后端启动方式与完整项目说明见根目录 [README.md](../README.md)。
