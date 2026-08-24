---
name: paper2xhs
description: 把学术论文 PDF 转成小红书多图帖（标题 + 五问式正文 + 标签 + 封面 + 一问一图的设计卡片）。你主导设计的协调式：机械活（MinerU 解析 PDF、生成封面、卡片 HTML 截图、半自动发布）交给 scripts/ 下的小工具，论文理解、选题角度、文案撰写、卡片设计由你亲自完成并在关键点与用户确认。当用户说“论文转小红书”、“paper2xhs”、“把这篇论文发小红书”、“论文转社交媒体”、“PDF 转小红书帖子”时触发。
allowed-tools: Bash, Read, Write, Glob, Grep, AskUserQuestion, SendUserFile
---

# paper2xhs — 论文转小红书（你主导的协调式）

把一篇论文 PDF 转成小红书帖子。**你是主笔**：这份文件是配方，不是全自动脚本——
没有 `main.py`。机械步骤（解析 / 封面 / 发布）调用 `scripts/` 下的小工具；**论文理解、
选题角度、文案撰写由你亲自完成**（用 Read 看材料、用 Write 落产物），并在关键点用
`AskUserQuestion` 与用户确认。

```text
PDF
 → 解析            (parse_pdf.py：MinerU → parsed/ + figures/，图与表都进 PIR)
 → 你读懂论文       (读 parsed/ + 看 figures/) → understanding/paper_understanding.json   [确认选题角度]
 → 你写五问式详细版  (Q1~Q5，不设字数上限) → xhs_post_detailed.md                     [交人工精简]
 → 人工精简出最终版  (标题/标签/封面文字) → xhs_post.json + xhs_post.md               [确认文案]
 → 你手写配图卡片    (一问一图：post_cards/p1.html … p5.html，嵌论文原图与表格数据)
 → 封面+卡片渲染    (cover.py 封面：默认 API 生图 gpt-image-2、无 key 回退本地合成；
                    render_cards.py：无头 chromium 把卡片 HTML 截成 1080×1440 竖版 PNG)
 → 半自动发布       (publish.py：封面+卡片多图帖，可选)
 → 小红书帖子
```

## 运行方式

1. **一步步来**：机械步骤用 `Bash` 调脚本，创作步骤你自己用 `Read` / `Write` 做。不要试图一条命令跑完。
2. **每个 Bash 块开头就地算 `WORKDIR`**——各 Bash 调用是独立 shell、不共享变量，所以别指望 `export` 跨步存活：
   ```bash
   WORKDIR="$(dirname "$pdf_path")/.paper2anything/xhs/$(basename "${pdf_path%.*}")"
   ```
   其中 `$pdf_path` 是用户给的论文 PDF 路径（每个块都重新设一次）。脚本在 `${SKILL_DIR}/scripts`——`SKILL_DIR`
   是**本 skill 的目录**（见本 skill 顶部注入的 "Base directory for this skill: …"）；各 Bash 块独立 shell，
   用到它的块开头按需 `export SKILL_DIR=<那个目录>` 一次（和 `WORKDIR` 一样每块现设）。
3. **在两个决策点用 `AskUserQuestion` 暂停**：① 读懂论文后确认“选题角度”；② 文案成稿后确认。**第②处是硬性关卡，不可跳过、不可自行判断“看起来没问题就继续”**——哪怕处于自动/免确认模式，文案确认这一步也必须停下来等用户明确回应，拿到确认或改稿意见之前**不进入 Step 4（封面与配图卡片）**。
   文案本身分两步：你先写**不设字数上限的详细版**（`xhs_post_detailed.md`），交给用户**人工精简**成最终短文案，
   你负责把精简结果落成 `xhs_post.json`/`.md` 并再确认一次——**精简取舍权在用户，你不要自己抢着先压缩一遍**。
4. **小红书是“准确、不夸大的科普”**：忠实反映论文贡献，口语化、有钩子，但**绝不编造数据或夸大结论**。
5. **正文是固定的五问模板**（Step 3），**配图是你手写的 HTML 卡片、一问一图**（Step 4）——
   配图**不再是论文原图直出**，而是把原图与表格数据嵌进你设计的卡片里再截图。

---

## Step 0：环境与凭据

> **统一环境**：所有 `python` 命令都在 paper2anything 的统一 conda 环境里（顶层 `environment.yml` 创建），命令以 `conda run -n paper2anything --no-capture-output` 为前缀。

凭据集中在 paper2anything 包根的 `.env`（从 `.env.example` 复制，已 gitignore）。每个新 shell 先导出一次：

```bash
set -a; source <paper2anything 包根>/.env; set +a
```

本 skill 用到的 key（**理解与文案由你亲自做，不调用任何 LLM API**）：
- `MINERU_API_TOKEN` — 解析 PDF（必填）
- `OPENAI_API_KEY`(+ `OPENAI_BASE_URL`) — 封面默认走它生图（gpt-image-2）；无 key 或 key 不可用时回退本地合成（复用论文原图）
- `XHS_MCP_BIN` — 可选：自定义 [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp) 二进制位置；**不设则发布时 skill 自动按平台下载**到 `~/.paper2anything/xhs/`。另可选 `XHS_MCP_URL`（自定义服务地址/端口，默认 `http://localhost:18060`）。

依赖自检（缺啥按提示装；依赖统一在 `environment.yml`）：

```bash
conda run -n paper2anything --no-capture-output python -c "import requests, rich, dotenv, playwright, PIL" 2>&1
# render_cards.py（Step 4 卡片截图）需要 chromium 引擎，首次运行前装一次：
#   conda run -n paper2anything --no-capture-output python -m playwright install chromium
```

---

## Step 1：解析 PDF（脚本）

```bash
pdf_path="/path/to/paper.pdf"          # ← 用户的论文 PDF
WORKDIR="$(dirname "$pdf_path")/.paper2anything/xhs/$(basename "${pdf_path%.*}")"
conda run -n paper2anything --no-capture-output \
  python "${SKILL_DIR}/scripts/parse_pdf.py" "$pdf_path" --workdir "$WORKDIR"
```

产出（`$WORKDIR` 下）：
- `parsed/paper_meta.json`（title / authors / abstract）、`parsed/sections.json`（`[{title, content}]`）
- `parsed/figures_index.json`（`[{figure_id, caption, image_path, page}]`，`image_path` 已指向 `figures/` 实体）
- `parsed/tables_index.json`（`[{table_id, caption, image_path, table_html, page}]`）——结果表**同时给截图和重建 HTML**：
  截图保原排版与公式，`table_html` 便于你在卡片里重排成原生表格（取舍见 `references/card-design.md`）。
  个别表可能只有 `table_html`、`image_path` 为空。
- `parsed/references.json`；`figures/*` 论文插图与表格截图实体

解析完，先 `Read` `parsed/sections.json` 与 `parsed/paper_meta.json` 通读全文。

---

## Step 2：读懂论文 → 写 understanding（你来做）[确认]

这是创作的地基，**你自己做判断**，不要交给脚本：

1. `Read` `parsed/sections.json`（全文）+ `parsed/paper_meta.json`；`Read` `parsed/figures_index.json` 与 `parsed/tables_index.json` 看图注表注（个别 caption 可能为空；多面板大图可能被解析器拆成两半、完整图注只挂在其中一半上，且拆缝处图例/轴标签可能被裁——一律以实际看图为准），并**实际 `Read` 几张候选图片**（`figures/` 下）判断哪些清晰、适合做封面或进卡片——图注说“framework”的图在小图里未必好看，只有你的眼睛能判断。
2. 用 `Write` 落 `understanding/paper_understanding.json`，schema：
   ```json
   {
     "paper_title": "...", "method_name": "方法简称（如 AccKV）",
     "one_sentence_summary": "一句话讲清这篇做了什么",
     "problem": "解决什么问题", "method": "怎么做的",
     "highlights": ["有数据支撑的亮点1", "创新点2", "应用价值3"],
     "experiment_results": ["关键数据1（含数字）", "..."],
     "keywords": ["领域关键词", "..."],
     "cover_palette": {"bg": "#F4F5F7", "accent": "#2E86AB"},
     "card_design": {"design_language": "数据仪表盘", "bg": "#F4F5F7",
                     "accent": "#2E86AB", "ink": "#1B1F24", "radius": "16px"},
     "important_figures": [
       {"figure_id": "fig_1", "image_path": "<figures_index.json 里的真实路径>",
        "suitable_for_cover": true, "importance_score": 0.9, "description": "图说明"}
     ],
     "post_cards": [
       {"q": "Q1", "question": "论文的主要内容是什么？",
        "visuals": [{"kind": "figure", "image_path": "<figures_index 里的真实路径>", "caption": "..."}],
        "note": "这张卡打算怎么排（一句话给自己的设计备忘）"}
     ]
   }
   ```
   - `important_figures` 必须含 `image_path`（取自 `parsed/figures_index.json`，指向真实存在的图）、`suitable_for_cover`、`importance_score`——封面默认走 API 生图（gpt-image-2），仅当 `OPENAI_API_KEY` 未配/不可用时回退本地合成、靠这几个字段复用原图；漏了则回退时无图 → 封面 `skipped`。
   - `post_cards` **固定 5 条、与 Step 3 的五个问题一一对应**（一问一图）。每条的 `visuals` 是这张卡要嵌的素材，
     0~2 项：`kind` 取 `figure`（用 `figures_index` 的 `image_path`）或 `table`（用 `tables_index` 的 `image_path`
     截图，或 `table_html` 在卡里重排原生表格）。**没有合适素材就留空数组，那张卡走纯排版**——
     硬塞一张糊图凑数比留白更糟。只写你亲眼 `Read` 过的图。
   - `card_design`：五张卡共用的设计 token（设计语言 + 配色 + 圆角），保证图集是一套而不是五张各画各的；
     配色与 `cover_palette` 同源，让封面到末卡一个调性。
   - `cover_palette`（可选）：本地合成回退路径的配色，按论文领域选 `bg`(浅色打底) + `accent`(强调色)，标题字色会随底色深浅自动适配。参考浅色调：通用 `#F4F5F7`+`#2E86AB`、生物 `#EEF6F0`+`#2D8A5F`、物理数学 `#F1ECF8`+`#6A30C2`、工程 `#FBF0EC`+`#D85A3C`、社科 `#F4EEF2`+`#8A5A78`、化学 `#EAF4F8`+`#0E86C0`。
3. 用 `AskUserQuestion` 与用户确认**选题角度**：这篇论文发小红书主打哪个亮点 / 用什么钩子 / 面向哪类读者。带着确认结果再写文案。

---

## Step 3：写小红书帖子（你来做）[确认]

**分两步走：你先写不受字数限制的详细版，人工在此基础上精简出最终版**——不要跳过详细版直接一步到位写短文案，
精简取舍是用户的活，你的活是把材料摊全。

**正文走固定的五问模板**——每篇论文都是这五问、这个顺序，**问句原文照抄、不许改写**，
在正文里作为小标题出现（这样才能和 Step 4 的一问一图卡片对齐）：

| # | 问题（原文照抄） | 答什么 |
|---|---|---|
| Q1 | 论文的主要内容是什么？ | 一句话讲清这篇做了什么，再补一两句定位（哪个领域、什么形态：方法/系统/benchmark/理论） |
| Q2 | 论文试图解决什么问题？ | 现有做法卡在哪、为什么这是个真问题——**讲痛点，别讲术语** |
| Q3 | 论文如何解决这个问题？ | 核心思路 + 关键设计，拆成读者能跟上的 2–3 步 |
| Q4 | 论文做了哪些实验？ | 在什么数据/基线上测的 + **具体数字**（几个点、多少倍），数字必须来自论文 |
| Q5 | 论文有哪些启发？ | 对读者的实际价值：能用在哪、什么值得借鉴、留下什么开放问题 |

### 3a：详细版初稿（你写，不设字数上限）

用 `Write` 落 `xhs_post_detailed.md`：按上表五问，每问一个 `## Q1 ...` 小节，**不设字数硬顶**——
把论文里能支撑这问的信息尽量摊开写全（背景铺垫、多个数据点、多层次推理都可以先写进去，
比最终版长很多也没关系，比如每问 300–600 字甚至更多）。**这一步不用照顾小红书正文的字数上限**，
目的是给用户提供充分、准确的原材料，而不是替用户先压缩一遍——别自己悄悄按最终版的字数标准来写。

写完把 `xhs_post_detailed.md` 的内容发给用户（或提示去看这个文件），明确告诉用户：
**最终发布用的短文案由用户人工精简**，不是你自动压缩决定取舍；用户可以直接编辑这个文件，
也可以在对话里告诉你想保留哪些点、砍掉哪些点。

### 3b：最终版（人工精简的结果，你负责落地成 schema）

拿到用户精简后的文本（用户直接改的文件内容，或口述的取舍方向）后，你再据此整理出正式的
`xhs_post.json` 和 `xhs_post.md`——**取舍判断权在用户，你不要自作主张地二次改写或再精简**，
你的活是把用户定下的内容按下面的规则和 schema 整理规范：

- **字数**：每问答案 **100–160 字**，全文正文（含钩子与结尾）**控制在 600–900 字**，不含标签。
  小红书正文上限 1000 字（`publish.py` 会 `[:1000]` 硬截断），留 ≥100 字余量，别顶格。
  超了就砍，别靠缩写硬塞——放不下的细节留给卡片。
  **注意 Latin 词与数字按实际字符计**（`Qwen2.5-7B`＝10 字、`74.00` ＝5 字），中文写作直觉会严重低估；
  超预算时先删枚举（模型名清单、agent 名清单）再删修饰，别删可回溯的数字。
- **结构**：开头 1–2 句钩子（≤40 字）→ Q1…Q5 五段 → 结尾 1 句互动引导（如“你觉得这方法能用在哪？”）→ 标签。
- **每问小标题前带一个 emoji**，五个 emoji 各不相同，和卡片上的序号徽标呼应。
- **风格**：口语化、易读、不端学术腔，但**忠实准确、不夸大、不编数据**。Q4 的每个数字都要能在 `parsed/` 里查到出处。
- **标题** ≤20 字，吸睛：含核心价值、或数字、或对比、或悬念式提问。
- **标签** 8–10 个（**小红书话题上限 10 个**，多写的会被静默丢掉），写在正文末尾；`hashtags` 字段同步放这些标签（发布脚本读 `hashtags`）。
- **封面文字** `cover_text` ≤15 字（封面大字用）。

产物 schema —— `xhs_post.json`：
```json
{"title": "...",
 "hook": "开头 1–2 句钩子",
 "qa": [
   {"q": "Q1", "question": "论文的主要内容是什么？", "answer": "100–160 字"},
   {"q": "Q2", "question": "论文试图解决什么问题？", "answer": "..."},
   {"q": "Q3", "question": "论文如何解决这个问题？", "answer": "..."},
   {"q": "Q4", "question": "论文做了哪些实验？",     "answer": "..."},
   {"q": "Q5", "question": "论文有哪些启发？",       "answer": "..."}
 ],
 "ending": "结尾互动句",
 "body": "把 hook + 五问五答 + ending + 标签拼成的完整正文（含 emoji/换行）",
 "hashtags": ["#标签1", "#标签2"], "cover_text": "≤15字封面词", "paper_title_zh": "论文中文标题"}
```
`qa` 是结构化来源、**必须是这 5 条**（`render_cards.py` 用它的条数校验一问一图）；`body` 是拼装好的成品，
**发布脚本只读 `body`**——两者要一致，改了 `qa` 记得同步 `body`。

`xhs_post.md`：第一行 `# {title}`，然后正文；可在顶部放 `![封面](cover.png)` 占位（封面在 Step 4 生成）。

落完最终版用 `AskUserQuestion` 给用户看标题 + 五问答案摘要，确认这就是用户精简后想要的样子（可能用户精简时有笔误或想再调整）。

> **硬性关卡**：最终版文案未经用户明确确认，**不得进入 Step 4 生成封面/配图卡片**；同样，`xhs_post_detailed.md`
> 未交给用户精简、拿到精简结果之前，**不得跳过 3a/3b 自己直接定稿**。用户提出修改就改完再问一次，
> 直到拿到明确的"可以/确认"再往下走——不要因为看起来已经写得不错就自行放行，更不要替用户完成精简这一步。

---

## Step 4：封面（脚本）+ 配图卡片（你手写 HTML → 脚本截图）

### 先生成封面（图集第 1 张）

**封面主/副标题此刻由你现拟**（你已读透论文，比从 JSON 里捡更贴切），经 `--title`（主标题大字）/ `--subtitle`（副标题小字）传入：

```bash
pdf_path="/path/to/paper.pdf"
WORKDIR="$(dirname "$pdf_path")/.paper2anything/xhs/$(basename "${pdf_path%.*}")"
conda run -n paper2anything --no-capture-output \
  python "${SKILL_DIR}/scripts/cover.py" --workdir "$WORKDIR" \
  --title "你拟的封面主标题大字" --subtitle "你拟的副标题小字"
```

逻辑：**默认用 `OPENAI_IMAGE_MODEL`（默认 `gpt-image-2`）生成竖版封面**，主标题大字用你传入的 `--title`、副标题小字用 `--subtitle`（留空才分别回退 `xhs_post.cover_text` / 论文标题）；未配 `OPENAI_API_KEY` 或 key 不可用时回退本地合成——复用 `understanding.important_figures` 里 `suitable_for_cover` 最高分的论文原图（叠加 `--title`，配色取 `cover_palette`）；两者都不可用则 `skipped`（不阻断流程）。产出 `cover.png`。
**生图 API 单次可能要好几分钟（经中转可达 6~7 分钟）**——本命令的 Bash 超时设 ≥10 分钟（600000ms），别用默认 2 分钟，超时被杀时 `logs/` 不会留 cover_result.json。

### 再做正文配图卡片（多图帖的第 2~6 张）

配图**不是论文原图直出**，而是**你亲手写的 HTML 卡片**——把论文原图与表格数据嵌进你设计的版式里，
再由脚本截成竖图。风格与 paper2html 一脉相承：先立设计概念，再排版。

**① 先 `Read` `references/card-design.md`**（硬规格、字号下限、图片 CSS 铁律、表格取舍）。

**② 用 `Write` 手写 `$WORKDIR/post_cards/p1.html … p5.html`**，一问一图、`pN` 对应 `QN`。要点（细节见规范）：
- 画布 `1080×1440` CSS px（3:4 竖版），四周留白 ≥64px、底部 ≥96px；
- 图片用相对路径 `../figures/<文件名>` 引用（卡片在 `post_cards/`、图在 `figures/`），`alt` 非空；
- 正文字号 ≥32px（画布上的 45px 到手机上只有 ~15px），每张卡文字 ≤120 字；
- 五张共用 `understanding.card_design` 的设计 token，**外壳统一、内容形态各异**；
- 嵌原图**绝不同时钉死宽高**（会拉变形）；嵌表格可用截图保真，或用 `table_html` 砍到 3–4 列重排；
- **别写 `html,body{overflow:hidden}`**——它只会把装不下的内容悄悄裁掉，让你看不见问题。

**③ 渲染 + 自检**（无头 chromium，纯本地、不调 API）：

```bash
pdf_path="/path/to/paper.pdf"
WORKDIR="$(dirname "$pdf_path")/.paper2anything/xhs/$(basename "${pdf_path%.*}")"
conda run -n paper2anything --no-capture-output \
  python "${SKILL_DIR}/scripts/render_cards.py" --workdir "$WORKDIR"
```

把 `post_cards/p<N>.html` 截成 `post_images/p<N>.png`（2160×2880，2x）并做渲染层自检，
四项硬指标任一不过即 FAIL、退出码 1：**图裂**（`../figures/` 路径写错）、**图变形**（同时钉死宽高）、
**竖向溢出**（内容装不下、被截掉）、**横向溢出**。卡片数与 `xhs_post.qa` 条数不符会告警（一问一图）。
FAIL 时 PNG 仍会写出，`Read` 看着改 HTML 后重跑（重跑会清掉旧 `p*`）。

**④ 逐张 `Read` 亲眼过一遍**：脚本只管渲染层缺陷，好不好看、字够不够大、留白匀不匀、
五张连起来是不是一套、和 `cover.png` 是不是一个调性——只有你的眼睛能判断。

---

## Step 5：发布到小红书（脚本 + 你协调，可选）

发布走开源的 **[xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)**（自带无头 Chromium 的单二进制 + REST API）。**登录一次后 cookies 持久、之后免登录**。二进制由 ① 自动备好（`XHS_MCP_BIN` 仅自定义位置时配，见 Step 0）。**首次配置/登录的分环境完整步骤见 `references/publish-guide.md`**——先 `Read` 它。不发布就跳过本步，把产物路径告诉用户手动发。

**① 确保 mcp 二进制就位并在固定持久目录运行**（二进制不存在会自动下载；cookies 落这里、跨论文复用）：
```bash
export XHS_MCP_DIR="$HOME/.paper2anything/xhs"; mkdir -p "$XHS_MCP_DIR"
# 解析二进制：优先 .env 的 XHS_MCP_BIN；否则用持久目录里的；都没有就按平台自动下载
if [ -n "$XHS_MCP_BIN" ] && [ -x "$XHS_MCP_BIN" ]; then BIN="$XHS_MCP_BIN"; else
  case "$(uname -s)-$(uname -m)" in
    Linux-x86_64)  ASSET=xiaohongshu-mcp-linux-amd64 ;;
    Darwin-arm64)  ASSET=xiaohongshu-mcp-darwin-arm64 ;;
    Darwin-x86_64) ASSET=xiaohongshu-mcp-darwin-amd64 ;;
    *) ASSET= ; echo "未知平台，请手动下载 xiaohongshu-mcp 并在 .env 设 XHS_MCP_BIN" ;;
  esac
  BIN="$XHS_MCP_DIR/$ASSET"
  if [ -n "$ASSET" ] && [ ! -x "$BIN" ]; then
    echo "未找到 mcp 二进制，自动下载 $ASSET …"
    curl -fL -o "$XHS_MCP_DIR/$ASSET.tar.gz" "https://github.com/xpzouying/xiaohongshu-mcp/releases/latest/download/$ASSET.tar.gz" \
      && tar xzf "$XHS_MCP_DIR/$ASSET.tar.gz" -C "$XHS_MCP_DIR" && chmod +x "$BIN"
  fi
fi
# 起服务（已在跑就跳过；BIN 不可用则报错、不硬起）
if ! curl -sf http://localhost:18060/api/v1/login/status >/dev/null 2>&1; then
  if [ ! -x "$BIN" ]; then
    echo "mcp 二进制不可用（$BIN）——下载失败或平台不支持，无法发布；手动下载并设 XHS_MCP_BIN，见 references/publish-guide.md"
  else
    ( cd "$XHS_MCP_DIR" && nohup "$BIN" -port=:18060 > mcp.log 2>&1 & )
    for i in $(seq 1 30); do curl -sf http://localhost:18060/api/v1/login/status >/dev/null 2>&1 && break; sleep 2; done
  fi
fi
```
（首次会下载 mcp 二进制 + 其 Chromium（约 150MB），可能要等；日志见 `$XHS_MCP_DIR/mcp.log`。macOS 若被 Gatekeeper 拦：`xattr -c "$BIN"`。）

**② 查登录态**：
```bash
conda run -n paper2anything --no-capture-output python "${SKILL_DIR}/scripts/publish.py" --check-only
```
`已登录` → 跳到 ④。`未登录` → 走 ③。

**③ 登录（仅首次或会话失效时）**：登录要换带界面/monitor 的方式起 mcp，**先停掉 ① 起的那个**（按进程名精确停，别用 `pkill -f`，会误杀自身）：
```bash
pkill -x xiaohongshu-mcp; sleep 1
```
再照 `references/publish-guide.md` 按环境操作。无头服务器要点：带 `-rod "monitor=:9273"` 重起 mcp（保持默认无头）→ `xhs_login.py` 取码 → `SendUserFile` 把 `qr.png` 发用户、提醒**首次可能要先在 monitor 端口(:9273)的浏览器界面里扫一道「新设备验证」码** → `AskUserQuestion` 等用户确认扫完 → 监测 cookies 写出 → 成功后**再 `pkill -x xiaohongshu-mcp` 停掉、回 ① 重启**（去掉 monitor、加载 cookies）。
```bash
conda run -n paper2anything --no-capture-output python "${SKILL_DIR}/scripts/xhs_login.py" \
  --out "$XHS_MCP_DIR/qr.png" --cookies "$XHS_MCP_DIR/cookies.json" --wait
```

**④ 发布前给用户过目**：`Read` `xhs_post.json` 把**标题 + 五问正文**发给用户看，`SendUserFile` 发 `cover.png` 与 `post_images/` 下全部卡片；用 `AskUserQuestion` 让用户**确认发布并选可见性**（选项默认「公开可见」，另有「仅自己可见」「仅互关好友可见」）。

**⑤ 发布**（传入用户选的可见性）：
```bash
pdf_path="/path/to/paper.pdf"
WORKDIR="$(dirname "$pdf_path")/.paper2anything/xhs/$(basename "${pdf_path%.*}")"
conda run -n paper2anything --no-capture-output \
  python "${SKILL_DIR}/scripts/publish.py" --workdir "$WORKDIR" --visibility "公开可见"
```
图集自动取**封面 + `post_images/` 下卡片**（按 p1、p2… 排序，含封面最多 18 张）。返回「发布成功」即完成。

---

## Step 6：把成品归集到 PDF 旁

成品默认埋在 `.paper2anything/xhs/<stem>/` 里不好找。文案+封面定稿后（无论是否走 Step 5 发布），把它们复制一份
到**与 PDF 同级**的 `<stem>_xhs/` 目录（`.paper2anything` 内副本保留不动），让用户在论文旁直接取用：

```bash
pdf_path="/path/to/paper.pdf"
WORKDIR="$(dirname "$pdf_path")/.paper2anything/xhs/$(basename "${pdf_path%.*}")"
DEST="${pdf_path%.*}_xhs"             # 与 PDF 同目录、同名 + _xhs 后缀
i=2; while [ -e "$DEST" ]; do DEST="${pdf_path%.*}_xhs_v$i"; i=$((i+1)); done   # 重名则追加 _v2、_v3
mkdir -p "$DEST"
cp "$WORKDIR/xhs_post.md" "$WORKDIR/xhs_post.json" "$DEST/"
[ -f "$WORKDIR/cover.png" ] && cp "$WORKDIR/cover.png" "$DEST/"   # 封面可能 skipped，存在才复制
[ -d "$WORKDIR/post_images" ] && cp -r "$WORKDIR/post_images" "$DEST/"   # 卡片同理，存在才复制
```

`xhs_post.md` 以 `![封面](cover.png)` 相对引用封面，故文案、封面与卡片整组放进 `<stem>_xhs/` 子目录、引用不破。
卡片源码 `post_cards/` 属中间产物，**不归集**——它引用 `../figures/`，搬出去会断；要改版回工作区改再重跑 Step 4。

---

## 产物位置

中间产物落在论文旁 `<pdf目录>/.paper2anything/xhs/<stem>/`（同目录多篇论文按 `<stem>` 分篇、互不覆盖），**最终成品另复制到 PDF 同级的 `<stem>_xhs/`**（Step 6）：

| 路径 | 内容 | 谁写 |
|---|---|---|
| `.paper2anything/xhs/<stem>/parsed/` | MinerU PIR（meta/sections/figures_index/tables_index/references） | parse_pdf |
| `.paper2anything/xhs/<stem>/figures/` | 论文插图与表格截图实体 | parse_pdf |
| `.paper2anything/xhs/<stem>/understanding/paper_understanding.json` | 论文理解 + important_figures + post_cards | **你** |
| `.paper2anything/xhs/<stem>/xhs_post_detailed.md` | 五问式详细版初稿（不设字数上限，供人工精简） | **你**（3a） |
| `.paper2anything/xhs/<stem>/xhs_post.json` `xhs_post.md` | 人工精简后的五问式最终文案 | 用户精简、**你**落地成 schema（3b） |
| `.paper2anything/xhs/<stem>/cover.png` | 封面 | cover |
| `.paper2anything/xhs/<stem>/post_cards/` | 配图卡片源码 `p1.html … p5.html` | **你** |
| `.paper2anything/xhs/<stem>/post_images/` | 卡片渲染图 `p1.png … p5.png`（2160×2880） | render_cards |
| `.paper2anything/xhs/<stem>/logs/` | 各脚本 `*_result.json` | 脚本 |
| **`<pdf目录>/<stem>_xhs/`** | **成品归集**：`xhs_post.md` + `.json` + `cover.png` + `post_images/`，与 PDF 同级 | **你（Step 6）** |

重跑覆盖工作区 `.paper2anything/xhs/<stem>/`（中间产物）；归集步骤遇同名 `<stem>_xhs/` 会另存为 `_v2`、`_v3`，不覆盖旧成品。

---

## 排错

- **MinerU 解析失败**：核对 `.env` 的 `MINERU_API_TOKEN`（在 https://mineru.net 申请）；PDF 应 ≤200MB / ≤200 页；能访问 `mineru.net`。重跑 Step 1 即可（覆盖）。
- **封面没生成（`skipped`）**：通常是既没配可用 `OPENAI_API_KEY`、又没有可复用的论文原图。配上 key 走 AI 生图，或确保 `understanding.important_figures` 有 `suitable_for_cover:true` 且 `image_path` 存在的图以供本地合成回退。
- **卡片没生成（`failed: post_cards 下无 p<N>.html`）**：卡片是**你手写**的，脚本只负责截图。先按 `references/card-design.md` 把 `$WORKDIR/post_cards/p1.html … p5.html` 写出来再跑。
- **卡片渲染自检 FAIL**：
  - *图裂* → `<img src>` 必须是 `../figures/<文件名>`（卡片在 `post_cards/`、图在 `figures/`），文件名从 `figures_index.json`/`tables_index.json` 抄，别手打哈希名。
  - *图变形* → 同时钉死了 `width`+`height`（或定宽盒子里 `object-fit`）。只定一个轴，另一轴 `auto`。
  - *竖向溢出* → 内容装不下、被截掉。删字或缩图，别缩字号到 32px 以下；写了 `overflow:hidden` 也瞒不过自检（脚本量高度前会放开它），但会骗过你自己的眼睛，所以别写。
- **卡片数与问题数不符（告警）**：一问一图，`post_cards/p<N>.html` 要与 `xhs_post.qa` 的 5 条一一对应。
- **表格没进 PIR**：`tables_index.json` 为空说明 MinerU 没识别出表格块；退一步从 `sections.json` 正文里取数字，自己在卡片里排版（数字照抄，不许编）。
- **发布图集少图**：发布只认 `post_images/` 下的 `p<序号>.*`，且需要 `cover.png` 存在（封面 `skipped` 时 publish 直接报错）。
- **发布步骤报错**：`未登录` → 按 `references/publish-guide.md` 完成登录（首次注意「新设备验证」）；`连不上 mcp` → 看 ① 是否成功起服务（二进制下载/启动失败查 `$XHS_MCP_DIR/mcp.log`）。**登录成功后须重启 mcp 才会加载 cookies**。不发布可跳过 Step 5、手动发产物。publish.py 非零退出（2=未登录、3=连不上）时 `conda run` 会附带打印一行 `ERROR conda.cli.main_run`——那只是退出码传播，不是脚本崩溃。
- **理解/文案/卡片不需要 API key**：这三步是你亲自做的，不调用任何 LLM API（只有封面 AI 生图用 `OPENAI_API_KEY`）。
- **`playwright` / chromium 缺失**：`render_cards.py` 报未安装时跑一次 `conda run -n paper2anything --no-capture-output python -m playwright install chromium`。

---

## references/

- `card-design.md` — 配图卡片设计规范：3:4 硬规格、字号下限、系列感、嵌原图/表格的 CSS 铁律、六种设计语言。
- `publish-guide.md` — 分环境的 xiaohongshu-mcp 首次配置与登录完整步骤。
