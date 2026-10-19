# 医药/医疗健康 合成评测语料（虚构示例）

随项目开源发布的评测用**合成数据**，用于复现 golden 评测基线。

- 企业「清源生物制药」、产品「清源缓释片」、活性成分「YSX-01」、批号
  「QS-2024-1118C / QS-2025-0812F」等均为**虚构**，不代表任何真实企业、药品或医学事实，
  **不构成任何用药或医学建议**。
- 语料刻意贴近真实企业知识库的文件类型分布：**PDF（含扫描件）+ Word + Excel + txt + md**，
  而不是全用最好解析的 md——同时验证"不同格式的解析入库"、"扫描件 OCR"与"分块策略"
  三条链路。配套代码已支持 `.txt/.md/.pdf/.docx/.xlsx` 入库，Word 走 python-docx、Excel
  走 openpyxl、PDF 文字层走 pypdf，均带 MIME 内容嗅探（docx/xlsx 必须是含 OOXML 签名
  条目的 ZIP，防改后缀冒充）；**无文字层的扫描/图像 PDF 自动走 MinerU Cloud OCR**。

| 文件 | 类型 | 主题 / 说明 |
| --- | --- | --- |
| 清源缓释片说明书.pdf | PDF | 产品规格 / 用法用量（单页文字层 PDF） |
| 供应商质量审计管理规程.pdf | PDF | 审计频次 / 新供应商准入（单页） |
| 仓储温控与效期管理SOP.pdf | PDF | **2 页 + 温湿度分区表**；默认分块(1500/150)按页切成 2 块，近效期/超温处理落在第 2 块 |
| 清源缓释片出厂检验报告扫描件.pdf | PDF(扫描) | **图像页、无文字层**；出厂检验结论 / 溶出度、含量限度。入库时 loader 探测后走 MinerU OCR |
| 清源缓释片召回管理规程.docx | DOCX | 召回分级 / 一级召回 24h 时限 / 效果评价（Word 制度） |
| 近效期预警批次清单.xlsx | XLSX | 批号台账：QS-2024-1118C 近效期处置（Excel 表格） |
| 药物警戒与不良反应报告制度.txt | TXT | 报告时限，旧系统导出的纯文本样式 |
| GMP偏差处理管理规程.md | MD | 偏差分级 / 上报时限 / CAPA |
| 药品注册申报资料要求CTD.md | MD | CTD 五模块 / 稳定性数据 |

## 为什么这么设计

1. **类型多样**：golden 同一批问题横跨 pdf/docx/xlsx/txt/md + 扫描 pdf，任何一条都能
   看到解析链路（PDF 文字层走 pypdf、扫描件走 MinerU OCR、Word 走 python-docx、Excel 走
   openpyxl 逐行拼文本、txt/md 走纯文本），不会像"全 md"那样被当成挑软柿子。
2. **扫描件链路真实可见**：《出厂检验报告扫描件》是 Pillow 把排版渲染成 A4 图像页再转
   的 PDF（无文字层，同真实拍照/扫描件形态）。上传后由 `app.rag.loader` 探测整份文字层
   为空 → 调 `app.services.mineru`（MinerU v4：上传 → 轮询 → 下载 zip 取 Markdown）入库。
   golden 里为它配了 3 条题，锚点均取自**真实 OCR 输出文本**；不开 rerank 时这 3 条全部
   只落在第 2 名（recall@1 掉到 0.47），开 rerank 后全部回到第 1——顺带把 rerank 的价值
   从"扫出来的扫描件"这个更贴近现实的场景里量化出来。
3. **分块可见**：《仓储温控SOP》超过一页后被默认参数切成 2 块，其中"近效期产品怎么管理"
   的答案在第 2 块——如果分块/检索没起作用，这条的 recall@1 就会掉，一眼能看出分块与
   分块级召回是否正常。
4. **表格入库**：PDF 里的温湿度分区表可被 pypdf 提取成文字；xlsx 台账按行拼成
   "批次号 \| 产品 \| ..." 文本，验证带表格文档的解析质量。

## 复现 / 重新生成

PDF/Word/Excel 由 `_gen_corpus.py` 生成（文字层 PDF 用 fpdf2 + 系统 WenQuanYi 字体，
扫描件用 Pillow 渲染成图像页、Office 用 python-docx / openpyxl）：

```bash
.venv/bin/python -m pip install fpdf2 python-docx openpyxl pillow   # 已装可跳过
.venv/bin/python samples/medical/_gen_corpus.py
```

> 说明：扫描件文件本身是"可识别图像页"（非文字层），重新上传入库前需在 `.env` 配好
> `MINERU_API_KEY`（MinerU Cloud，每日 1000 页高优先级额度），否则该文件会解析失败并
> 提示"需配置 MINERU_API_KEY"。其余文字层 / Office / 文本文件不受影响。

评测：以 `admin` 上传全部文件入库后 `POST /api/v1/rag/eval`（黄金问题集在
`data/eval/golden.json`，锚点均已按真实 loader/OCR 提取文本核对过）。
