# 架构与扩展指南

本文档说明:
1. 项目结构 — 哪些是**共享基础设施**,哪些是**功能专属**
2. 加新功能的标准步骤
3. 每个 Phase 落到代码的位置

写给"半年后的我"或"刚加入的同事"。如果你只想用工具,看 `README.md`。

---

## 1. 项目分层

```
src/
├── ─── 共享基础设施(任何新功能都可白嫖) ──────────────────────────────
├── config.py          全局配置加载 (config.yaml + .env)
├── llm/               LLM 抽象 + 任务路由 + 成本统计
│   ├── base.py        Provider ABC
│   ├── deepseek.py    DeepSeek 实现(主力)
│   ├── claude.py      Claude 实现(预留 + 视觉)
│   ├── pricing.py     CNY 价格表 + 成本估算
│   └── router.py      router.call("chat"/"vision"/...)
├── extractors/        文档/图片解析(PDF/DOCX/XLSX/TXT/CSV/IMG)
│   ├── pdf.py / docx.py / excel.py / text.py / image.py
│   └── dispatcher.py  统一 extract(path) 入口
├── storage/           SQLite + SQLAlchemy
│   ├── models.py      ORM 表定义
│   └── database.py    引擎 + session 管理
├── scraper/           礼貌爬虫 + 下载器
│   ├── http.py        PoliteClient (TLS 可控 / 重试 / 限速)
│   ├── crawler.py     unisyue.com 站点爬虫
│   ├── pdf_downloader.py
│   └── categories.py  section / category 映射(权威表)
│
├── ─── 选型功能(Phase 0,现有) ────────────────────────────────────
├── requirement/       自然语言/文档/图片 → 结构化 Requirement
│   ├── schema.py
│   ├── rule_parser.py  规则解析(无 AI)
│   └── ai_parser.py    LLM 解析(失败回退规则)
├── parser/            彩页 PDF → 产品规格
│   ├── brochure_parser.py     调度器
│   ├── port_patterns.py       端口数 / 速率(交换机)
│   └── feature_patterns.py    layer / PoE / rack-units
├── selector/          匹配引擎
│   ├── rule_matcher.py        SQL 过滤 + 加权打分
│   └── ai_matcher.py          rule-first + LLM 重排
├── scheduler/         APScheduler 定时刷新
│
├── ─── 后续功能占位(下面详述) ──────────────────────────────────
├── catalog_lists/     [Phase 2] 名录管理
├── projects/          [Phase 4] 项目管理
├── quotes/            [Phase 5] 报价单编辑
├── ui/                [Phase 3] Web UI
│
└── cli/               多子命令 CLI 入口
    ├── _common.py     共享(console / logging)
    ├── main.py        Click group
    ├── select.py / crawl.py / inspect_cmd.py
    ├── catalog.py / projects.py / quote.py    占位
    └── __main__.py    `python -m src.cli` 入口
```

### 1.1 分层原则

- **基础设施**(`config / llm / extractors / storage / scraper`)对所有功能开放;改它们需要谨慎,会影响所有人
- **功能模块**(`requirement / parser / selector / catalog_lists / projects / quotes / ui`)只为自己负责;它们**消费**基础设施,不互相依赖
- **CLI 子命令**是功能模块的"门面",一个子命令对应一个功能模块

---

## 2. 如何加一个新功能

以"加竞品对比报告"为例。

### Step 1 — 创建模块目录

```bash
mkdir src/competitive
touch src/competitive/__init__.py
```

### Step 2 — 复用基础设施

```python
# src/competitive/scraper.py
from ..scraper.http import PoliteClient        # 复用爬虫客户端

# src/competitive/analyzer.py
from ..llm import get_router, Message          # 复用 LLM 调度

# src/competitive/storage.py
from ..storage import get_db                    # 复用数据库
from ..storage.models import Base               # 加新表也用同一 Base
```

### Step 3 — 加 CLI 子命令

```python
# src/cli/competitive.py
import click

@click.group(name="competitive", help="竞品对比报告。")
def cmd(): pass

@cmd.command("report")
@click.argument("name")
def _report(name: str):
    from ..competitive.analyzer import run
    run(name)
```

```python
# src/cli/main.py 里加一行
from . import competitive
app.add_command(competitive.cmd)
```

完成。`python -m src.cli competitive report <name>` 就能用。

---

## 3. Phase 路线图

每个 Phase 是一次可单独 commit 的小迭代。

### ✅ Phase 0 — 选型 MVP (已完成)
- 抓 unisyue.com 全量(119 产品 / 118 彩页)
- 规则 + AI 双引擎匹配
- 文本 / 文档 / 图片 输入

### 🔜 Phase 1 — 多命令 CLI + 架构文档 (本提交)
- `python -m src.cli {select|crawl|inspect|catalog|projects|quote}`
- 占位命令明确告诉用户未实现部分在哪个 Phase

### 📌 Phase 2 — 名录管理 (`src/catalog_lists/`)

**新增**:
- `src/catalog_lists/models.py`  
  `CatalogList(id, name, source_file, updated_at)` + 关联表 `product_catalog`
- `src/catalog_lists/importer.py`  
  解析"政府名录承诺函" PDF → 提取 UNIS 型号 → 写入数据库
- `src/catalog_lists/filter.py`  
  给 selector 加 `--catalog <name>` 过滤维度
- `src/cli/catalog.py`  
  `import` / `list` / `show` 子命令(已占位)

**修改**:
- `src/storage/models.py` — 加 `CatalogList` 表
- `src/selector/rule_matcher.py` — `find_products()` 加 catalog 过滤参数

**用法**:
```bash
python -m src.cli catalog import "2025年V1名录承诺函.pdf"
python -m src.cli catalog list
python -m src.cli select "万兆三层" --catalog "2025年V1名录"
```

### 📌 Phase 3 — Web UI (`src/ui/`)

**技术选型**:Gradio(快)+ Tab 布局,三个入口对应三种选型模式

**新增**:
- `src/ui/app.py` — Gradio 主入口
- `src/ui/pages/select_innovation.py`  — 创新型 (`section=innovation`)
- `src/ui/pages/select_general.py`     — 通用型 (`section=general`)
- `src/ui/pages/select_catalog.py`     — 名录型 (`catalog=<active>`)

**修改**:无 — 复用现有 selector

**用法**:
```bash
python -m src.cli ui          # 启动 http://127.0.0.1:7860
```

### 📌 Phase 4 — 项目管理 (`src/projects/`)

**新增**:
- `src/projects/models.py`  
  `Project(id, name, customer, owner, status, created_at)`  
  `Quote(id, project_id, version, requirement_snapshot, file_path)`
- `src/projects/service.py` — CRUD 业务逻辑
- `src/projects/scanner.py` — 扫 `D:\Work\紫光恒越\日常工作\<人名>\<项目>\` 文件夹,自动建项目
- `src/cli/projects.py` — 现有占位填实
- `src/ui/pages/projects.py` — Web 端项目列表/详情

**用法**:
```bash
python -m src.cli projects scan     # 扫工作目录,自动建项目
python -m src.cli projects list     # 表格列出所有项目
python -m src.cli projects show <id>
python -m src.cli projects status <id> 中标
```

### 📌 Phase 5 — 报价单编辑 (`src/quotes/`)

最复杂的 Phase,因为规则多。**分两层**:

**第一层 通用规则**(对所有报价单都做):
- 删除 价格汇总表 的"产品名称 / 详细描述 / 要求提前报备周期 / 订单准备周期"列
- 删除左上角 H3C logo
- 若 产品型号 为空,从 描述列复制第一个型号代码

**第二层 服务器专属规则**:
- 删除内部连接组件行(假内存、电源线、滑轨、Riser、风扇线等)
- R4930 G7 / R3935 G7 → 替换服务行,从 `IT产品BOM编码20260331.xlsx` 查找
- R3800FT20 G3 → 根据配置模板复制描述到 价格汇总表 + 价格明细清单,保留公式

**新增**:
- `src/quotes/reader.py` — 读 H3C 配置器导出的 .xls (openpyxl)
- `src/quotes/rules/`  
  ├── `common.py` — 通用规则  
  ├── `server.py` — 服务器规则  
  └── `r3800ft20.py` — R3800FT20 G3 模板填充
- `src/quotes/bom_lookup.py` — 查 IT产品BOM 表(缓存)
- `src/quotes/writer.py` — 导出格式化后 .xls,保留公式
- `src/cli/quote.py` — 现有占位填实

**用法**:
```bash
python -m src.cli quote format <quote.xls>                     # 应用所有规则
python -m src.cli quote format <quote.xls> --skip server       # 跳过服务器规则
python -m src.cli quote attach <project-id> <quote.xls>        # 关联项目
```

---

## 4. 约定速查

| 主题 | 约定 |
|---|---|
| LLM 调用 | 永远走 `router.call(task=...)`,不直接 import provider |
| 新表 | 加到 `src/storage/models.py`,继承 `Base`,`init_db.py` 自动建表 |
| 新 CLI 命令 | 一个文件一个命令组,在 `main.py::_register_subcommands` 注册 |
| 配置项 | 静态默认值进 `config.yaml`,密钥/环境特化进 `.env` |
| 中文输出 | 用 `_common.console`,不要自建 Console |
| 文档提取 | 走 `src.extractors.extract(path)`,不要直接 import pdfplumber |
| 网络请求 | 走 `src.scraper.http.PoliteClient`,享受限速 + 重试 + 代理 fallback |

---

## 5. 测试

```bash
pytest                          # 跑所有测试
pytest tests/test_xxx.py -k foo # 单项
```

新功能强烈建议加单测,特别是规则类(quotes/rules、catalog_lists/importer)。

---

## 6. 提交规范

参考现有 commit log。一句话:**主语用动词原型,描述"为什么"而不是"做了什么"**。

```
feat(scraper): full catalog crawl across both sections, ...
fix(parser): reject garbage spec matches; add catalog inspector
chore: remove demo seed; real crawl is the primary quick-start
```
