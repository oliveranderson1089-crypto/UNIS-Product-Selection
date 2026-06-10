# UNIS Product Selection

根据需求(文本 / 文档 / 图片)自动推荐合适的紫光恒越 (UNIS) 产品,并给出选择理由。
选型数据以**人工整理的权威 Excel 选型表**为准(全线选型库 + 名录对照表),
辅以 unisyue.com 爬虫数据;AI 模式由**本地大模型**(Ollama)驱动:语义召回 + 智能重排,零 API 成本。

---

## 功能

| 输入 | 支持 |
|---|---|
| 自然语言文本 | ✅ |
| 文档 (`.pdf` `.docx` `.xlsx` `.txt` `.md` `.csv`) | ✅ |
| 图片 (`.png` `.jpg` …) | ✅(需 AI 模式 + Claude key) |

| 模式 | 说明 |
|---|---|
| **规则模式 (默认)** | 关键字/正则提取需求 → SQL 过滤 → 规则评分。完全离线,无需任何模型。 |
| **AI 模式 (`--ai`)** | 本地 qwen2.5 解析需求 + bge-m3 **语义召回**(按意思匹配,补上 SQL 漏掉的候选)+ LLM 重排并生成中文理由。全程本地推理,失败自动回退规则结果。图片需求走 Claude vision。 |

### 选型范围与粒度(分阶段)

| 选型入口 | 返回粒度 | 数据源 |
|---|---|---|
| 🚀 创新型 / 📦 通用型 | **产品系列**(Phase 1) | UNIS 全线产品选型库.xlsx(13 表 × 创新/通用 × 9 品类) |
| 🏷️ 名录型 | **具体型号** | 名录选型对照表.xlsx(承诺型号 + 选型建议) |

> Phase 2:创新/通用的型号级表整理完成后,把 `config.yaml → selector.section_granularity`
> 从 `series` 改为 `model` 并导入新表即可,代码零改动。

---

## 快速开始

### 1. 安装依赖

```powershell
# 项目根目录
python -m venv .venv
.venv\Scripts\activate           # Windows;Linux/Mac 用 source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 本地大模型(AI 模式用,可选)

AI 模式跑在本地 [Ollama](https://ollama.com) 上,不花一分钱 API 费:

```powershell
# 装好 Ollama 后拉两个模型
ollama pull qwen2.5     # 对话/重排(7B,中文强)
ollama pull bge-m3      # 嵌入模型(1024 维,语义召回用)
```

> 不装 Ollama 也能跑 —— 规则模式完全离线;AI 模式不可用时自动回退规则结果。
> 图片需求解析需要 Claude key:`copy .env.example .env` 后在 `.env` 里填
> `ANTHROPIC_API_KEY`(可选,只影响图片输入)。

### 3. 初始化数据库 + 导入选型数据源

```powershell
python scripts/init_db.py

# 核心数据源:两份人工整理的 Excel(路径配在 config.yaml → selection_sources,支持 glob)
python -m src.cli catalog import-library    # 全线产品选型库 → 系列级(创新/通用)
python -m src.cli catalog import-xlsx       # 名录选型对照表 → 型号级 + 名录

# 语义索引(AI 模式的语义召回用;每次数据源更新后重跑)
python -m src.cli index build
python -m src.cli index status

# 辅助数据源:官网爬虫(可选)
python -m src.cli crawl --max 5     # 冒烟抓取(~30 秒)
python -m src.cli crawl              # 全量抓取(~20 分钟)
python -m src.cli inspect            # 查看抓取成果
```

> 在 TLS 拦截代理(FlClash / 企业防火墙)下需要先 `set CRAWLER_VERIFY_SSL=false`,
> 或者在 `config.yaml -> crawler.verify_ssl: false` 关掉证书校验。

### 4. 启动 Web UI(推荐)

```powershell
python -m src.cli ui          # 打开 http://127.0.0.1:7860
```

三个选型入口对应你工作中的三种场景:
- 🚀 **创新型选型** — 自主可控(-G)产品线,按**系列**推荐
- 📦 **通用型选型** — 行业通用产品线,按**系列**推荐
- 🏷️ **名录型选型** — 限定到名录内的**具体型号**(先在「名录管理」导入 PDF 承诺函或 Excel 对照表)

另有 📊 报价单编辑(Excel COM 无损格式化 + 版本记录 + 项目归档)、📁 项目管理
(扫描工作目录、状态/客户/备注维护)、🏷️ 名录管理、📂 参考文件管理。

### 5. 或者用 CLI

```powershell
python -m src.cli --help            # 看所有子命令

# 规则模式(无需任何模型)
python -m src.cli select "48口万兆三层核心交换机,自主可控,冗余电源"

# AI 模式(本地 qwen2.5 + 语义召回)
python -m src.cli select "能跑大模型推理的国产一体机" --ai

# 限定到创新产品 / 通用产品 / 名录范围
python -m src.cli select "..." --section innovation
python -m src.cli select "..." --section general
python -m src.cli select "..." --catalog "紫光恒越2025年V1名录"

# 上传文档 / 图片(图片需 --ai + Claude key)
python -m src.cli select --doc 客户需求.docx
python -m src.cli select --image spec.png --ai

# 选型数据源 / 名录管理
python -m src.cli catalog import-library                          # Excel 选型库(系列)
python -m src.cli catalog import-xlsx                             # Excel 名录(型号)
python -m src.cli catalog import "名录承诺函.pdf" --name "2025-V1-名录"   # PDF 名录
python -m src.cli catalog list / show <名录名> / rematch

# 语义索引
python -m src.cli index build / status
```

---

## 功能进度

| 子命令 | 用途 | 状态 |
|---|---|---|
| `select` | 文本/文档/图片 → 推荐 UNIS 产品(规则 / AI) | ✅ |
| `catalog import-library / import-xlsx` | 导入 Excel 选型数据源(系列 / 型号) | ✅ |
| `catalog import / list / show / rematch` | PDF 名录管理 | ✅ |
| `index build / status` | 语义向量索引(bge-m3 嵌入) | ✅ |
| `crawl` | 抓取 unisyue.com 全量产品 + 彩页 | ✅ |
| `inspect` | 看产品库健康度(数量、字段覆盖) | ✅ |
| `ui` | Web 界面(3 选型入口 + 名录/项目/报价/参考文件) | ✅ |
| `projects scan / list / show / status` | 项目扫描与管理 | ✅ |
| `quote format / list-rules` | 报价单格式化(Excel COM 无损)+ 版本记录 | ✅ |

参考 `ARCHITECTURE.md` 了解每个 Phase 的落地位置。

---

## 定时刷新

抓取范围由 `config.yaml -> crawler.start_paths` 控制。要让它自动每周刷一次:

```yaml
# config.yaml
scheduler:
  enabled: true
  crawl_cron: "0 3 * * 1"   # 每周一 03:00
```

```powershell
python -m src.scheduler.jobs   # 阻塞型常驻进程
```

---

## 报价单格式化(Excel COM)

UI 的 **📊 报价单编辑** 标签(以及 CLI `python -m src.cli quote format <文件.xlsx>`)
会驱动本机安装的 **Excel 或 WPS**,通过 COM 自动化按预设规则改写 H3C 配置器导出的报价单。
走 COM 是为了**无损**:图片、合并单元格、列宽、跨表公式都原样保留 —— 纯 Python 的
openpyxl / xlrd 回退路径会把这些全丢掉,只剩单元格里的数值/文本。

**进程清理(自动,且安全)**
每次格式化都会用 `DispatchEx` 新起一个**隐藏、独立**的 Excel 进程,用完即清理。隐藏的
COM Excel 经常在 `Quit()` 之后仍然残留(这是 COM 编组的固有现象,不是 Python 引用泄漏),
所以程序会按 PID **强制结束它自己刚拉起的那个进程**。清理只针对"本次启动前不存在、且确实
由本程序启动"的进程 —— **永远不会动你自己打开的 Excel / WPS 文档**。

**"跑久了就丢格式、重启就好"—— 已加硬保护**
老现象:程序在后台跑久了,偶尔会输出一个**没有图片 / 合并格 / 列宽**的精简表格(约 18KB),
重启程序或刷新页面又好了。根因是长时间运行后残留的 `excel.exe` 越积越多,直到再也起不来新的
Excel 实例;COM 调用失败后,旧逻辑会**悄悄**退回到丢格式的纯 Python 路径,用户毫无察觉。

现在遇到这种情况会**直接报错并中止**,不再悄悄产出残缺文件。错误信息里写明了恢复办法:

> 1. 刷新浏览器页面后重新上传;
> 2. 若仍不行,重启本程序;
> 3. 确认源文件没有在 Excel / WPS 里打开占用。

上面任一步骤都能清掉残留进程、释放资源,恢复后再次格式化即可得到带完整格式的结果。
(上述自动清理已基本杜绝进程堆积;若机器上本来就残留了历史 `excel.exe`,任务管理器里
手动结束一次,或重启一次电脑即可。)

---

## 维护 / 调试工具

日常使用统一从 `python -m src.cli <subcmd>` 入口。下面这些脚本是"裸金属"调试工具,
站点改版/规则失效时方便定位问题。

| 脚本 | 用途 |
|---|---|
| `scripts/init_db.py` | 建库(空表) |
| `scripts/debug_crawl.py <url>` | 探测一个产品/分类页,看选择器抓到了什么 |
| `scripts/debug_pdf.py <pdf or --product CODE>` | 打印彩页的正文 + 所有表格,排查规格抽取问题 |
| `scripts/run_crawler.py [--max N]` | 等价于 `python -m src.cli crawl`(保留兼容) |
| `scripts/inspect_db.py` | 等价于 `python -m src.cli inspect`(保留兼容) |

---

## 项目结构

```
src/
├── config.py            # 全局配置加载(config.yaml + .env)
├── llm/                 # 多 LLM 适配 + 任务路由
│   ├── base.py          # Provider 接口(call + embed)
│   ├── ollama.py        # ← 现在用:本地 qwen2.5(chat)+ bge-m3(embedding)
│   ├── deepseek.py      # 备用(reasoning 任务)
│   ├── claude.py        # 视觉用(图片需求解析)
│   ├── pricing.py       # 估算每次调用成本(CNY,本地模型恒为 0)
│   └── router.py        # 业务只调 router.call(...) / router.embed(...)
├── extractors/          # 文档/图片提取(PDF/DOCX/XLSX/TXT/CSV/Image)
├── requirement/         # 需求解析 (rule_parser, ai_parser)
├── storage/             # SQLite + ORM(Product 带 granularity 系列/型号)
├── catalog_lists/       # 名录 + 选型数据源导入(PDF 承诺函 / Excel 选型库与对照表)
├── scraper/             # unisyue.com 爬虫 + 彩页下载
├── parser/              # 彩页 PDF → 结构化规格
├── selector/            # 匹配引擎 (RuleMatcher / AIMatcher / SemanticIndex)
│   └── semantic_index.py  # 语义索引:可插拔后端(embedded numpy / chroma)
├── quotes/              # 报价单格式化(Excel COM 无损改写 + 规则)
├── projects/            # 项目扫描 / 状态 / 报价版本记录
├── ui/                  # Gradio Web 界面(7 个标签页)
├── scheduler/           # 定时任务
└── cli/                 # 命令行入口(select/catalog/index/crawl/projects/quote/ui)
scripts/
├── init_db.py
├── seed_demo.py         # 写入若干 demo 产品
└── run_crawler.py
```

---

## 切换/扩展 LLM

业务代码永远用 `router.call("chat" | "reasoning" | "vision", ...)` 和
`router.embed("embedding", texts)`,不直接引用厂商。
切换厂商**只改 `config.yaml`**,代码零改动。当前生效配置:

```yaml
llm:
  chat:
    provider: ollama            # 本地 qwen2.5(零成本;换回云端只改这两行)
    model: qwen2.5
  embedding:
    provider: ollama            # bge-m3,1024 维,语义召回用
    model: bge-m3
  vision:
    provider: claude            # 图片需求解析
    model: claude-haiku-4-5
```

新增厂商(GLM / 通义 API / …)只需:
1. 在 `src/llm/` 加一个 `XxxProvider(LLMProvider)`
2. 在 `src/llm/router.py::LLMRouter._build` 加一行 elif

---

## 成本

`src/llm/pricing.py` 维护每个模型的 CNY 价格,每次 LLM 调用都会附带 `cost_cny`
估算,方便监控月度账单。

**当前默认配置(本地 Ollama)单次选型成本为 ¥0** —— qwen2.5 重排和 bge-m3 嵌入都跑本机。
只有图片需求解析(Claude vision)和切回云端模型时才产生费用:

| 模型 | 单次(≈ 2K in + 500 out) |
|---|---|
| qwen2.5 / bge-m3(本地 Ollama) | **¥0** |
| DeepSeek-V3 | ≈ ¥0.008 |
| DeepSeek-R1 | ≈ ¥0.016 |
| Claude Haiku | ≈ ¥0.026 |
| Claude Sonnet 4 | ≈ ¥0.097 |

---

## 路线图

- [x] 文本 / 文档 / 图片需求输入
- [x] 规则匹配引擎(默认)
- [x] AI 匹配引擎(本地 qwen2.5 重排,失败回退规则)
- [x] 向量语义召回(bge-m3 嵌入 + 内嵌 numpy 余弦后端;Chroma 后端可选,
      本机 Windows 轮子崩溃故默认不用)
- [x] 权威 Excel 选型数据源(全线选型库 → 系列;名录对照表 → 型号)+ 粒度范围路由
- [x] unisyue.com 爬虫 + 彩页下载 + 规格解析 + 定时刷新
- [x] Web UI(Gradio,7 个标签页)
- [x] 报价单格式化(Excel COM 无损)+ 版本记录 + 项目归档
- [x] 项目管理(work_dir 扫描 / 状态 / 客户 / 备注)
- [ ] Phase 2:创新/通用切换到型号级(`selector.section_granularity: model` + 新表导入)
- [ ] 多产品组合方案(交换机 + 服务器 + 存储一体化推荐)
- [ ] PowerPoint / 招标模板自动填充
