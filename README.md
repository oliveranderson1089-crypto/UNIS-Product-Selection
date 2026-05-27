# UNIS Product Selection

根据需求(文本 / 文档 / 图片)自动推荐合适的紫光华三 (UNIS) 产品型号,并给出选择理由。
内置 unisyue.com 自动爬虫,定期同步产品页和彩页(PDF),解析后入库,供选型引擎使用。

---

## 功能

| 输入 | 支持 |
|---|---|
| 自然语言文本 | ✅ |
| 文档 (`.pdf` `.docx` `.xlsx` `.txt` `.md` `.csv`) | ✅ |
| 图片 (`.png` `.jpg` …) | ✅(需 AI 模式 + Claude key) |

| 模式 | 说明 |
|---|---|
| **规则模式 (默认)** | 关键字/正则提取需求 → SQL 过滤 → 规则评分。无需 API key,完全离线。 |
| **AI 模式 (`--ai`)** | 用 DeepSeek 解析需求,LLM 二次重排并生成自然语言理由。图片需求需要 Claude。 |

---

## 快速开始

### 1. 安装依赖

```powershell
# 项目根目录
python -m venv .venv
.venv\Scripts\activate           # Windows;Linux/Mac 用 source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置密钥(可选)

```powershell
copy .env.example .env
# 用编辑器打开 .env,填入 DEEPSEEK_API_KEY 和(可选)ANTHROPIC_API_KEY
```

> 没有 API key 也能跑 —— 规则模式不需要任何 key。

### 3. 初始化数据库 + 抓取真实产品

```powershell
python scripts/init_db.py
python -m src.cli crawl --max 5     # 冒烟抓取(~30 秒)
python -m src.cli crawl              # 全量抓取(~20 分钟,13 个品类,400+ 产品)
python -m src.cli inspect            # 查看抓取成果
```

> 在 TLS 拦截代理(FlClash / 企业防火墙)下需要先 `set CRAWLER_VERIFY_SSL=false`,
> 或者在 `config.yaml -> crawler.verify_ssl: false` 关掉证书校验。

### 4. 启动 Web UI(推荐)

```powershell
python -m src.cli ui          # 打开 http://127.0.0.1:7860
```

三个选型入口对应你工作中的三种场景:
- 🚀 **创新型选型** — `Autonomous_Controllable` 系列(100% 国产化)
- 📦 **通用型选型** — `Commercial_Product` 系列(行业通用)
- 🏷️ **名录型选型** — 限定到政府名录范围内的产品(先在"名录管理"导入 PDF)

加 📊 报价单编辑 / 📁 项目管理 两个占位标签,功能逐步上线。

### 5. 或者用 CLI

```powershell
python -m src.cli --help            # 看所有子命令

# 规则模式(无需 API key)
python -m src.cli select "48口万兆三层核心交换机,自主可控,冗余电源"

# 限定到创新产品 / 通用产品 / 名录范围
python -m src.cli select "..." --section innovation
python -m src.cli select "..." --section general
python -m src.cli select "..." --catalog "2025-V1-名录"

# 上传文档 / 图片(图片需 --ai + Claude key)
python -m src.cli select --doc 客户需求.docx
python -m src.cli select --image spec.png --ai

# 名录管理
python -m src.cli catalog import "名录承诺函.pdf" --name "2025-V1-名录"
python -m src.cli catalog list
python -m src.cli catalog show "2025-V1-名录"
python -m src.cli catalog rematch      # 抓新产品后跑这个
```

---

## 功能进度

| 子命令 | 用途 | 状态 |
|---|---|---|
| `select` | 文本/文档/图片 → 推荐 UNIS 产品 | ✅ |
| `crawl` | 抓取 unisyue.com 全量产品 + 彩页 | ✅ |
| `inspect` | 看产品库健康度(数量、字段覆盖) | ✅ |
| `catalog import / list / show / rematch` | 政府名录管理 | ✅ |
| `ui` | Web 界面(3 个选型入口 + 名录管理) | ✅ |
| `projects new / list / show / status` | 项目/标书/报价单归档 | 🔜 Phase 4 |
| `quote format / attach` | 报价单格式化 | 🔜 Phase 5 |

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
│   ├── base.py          # Provider 接口
│   ├── deepseek.py      # ← 现在用
│   ├── claude.py        # ← 视觉用 + 预留文本切换
│   ├── pricing.py       # 估算每次调用成本(CNY)
│   └── router.py        # 业务只调 router.call("chat"/"vision"/...)
├── extractors/          # 文档/图片提取(PDF/DOCX/XLSX/TXT/CSV/Image)
├── requirement/         # 需求解析 (rule_parser, ai_parser)
├── storage/             # SQLite + ORM
├── scraper/             # unisyue.com 爬虫 + 彩页下载
├── parser/              # 彩页 PDF → 结构化规格
├── selector/            # 匹配引擎 (RuleMatcher / AIMatcher)
├── scheduler/           # 定时任务
└── cli/                 # 命令行入口
scripts/
├── init_db.py
├── seed_demo.py         # 写入若干 demo 产品
└── run_crawler.py
```

---

## 切换/扩展 LLM

业务代码永远用 `router.call("chat" | "reasoning" | "vision", ...)`,不直接引用厂商。
切换厂商**只改 `config.yaml`**,代码零改动:

```yaml
llm:
  chat:
    provider: claude            # ← 这一行
    model: claude-sonnet-4
```

新增厂商(Qwen / GLM / 本地 Ollama)只需:
1. 在 `src/llm/` 加一个 `XxxProvider(LLMProvider)`
2. 在 `src/llm/router.py::LLMRouter._build` 加一行 elif

---

## 成本

`src/llm/pricing.py` 维护每个模型的 CNY 价格,每次 LLM 调用都会附带 `cost_cny`
估算,方便监控月度账单。

典型一次选型 (≈ 2K input + 500 output tokens) 成本:

| 模型 | 单次 |
|---|---|
| DeepSeek-V3 | ≈ ¥0.008 |
| DeepSeek-R1 | ≈ ¥0.016 |
| Claude Haiku | ≈ ¥0.026 |
| Claude Sonnet 4 | ≈ ¥0.097 |

---

## 路线图

- [x] 文本 / 文档 / 图片需求输入
- [x] 规则匹配引擎(默认)
- [x] AI 匹配引擎(DeepSeek 重排 + 推理)
- [x] unisyue.com 爬虫 + 彩页下载 + 规格解析
- [x] 定时刷新
- [ ] Web UI (Gradio / 简单 HTML 上传页)
- [ ] 向量检索(chromadb 已在 requirements 但未使用)
- [ ] 多产品组合方案(交换机 + 服务器 + 存储一体化推荐)
- [ ] PowerPoint / 招标模板自动填充
