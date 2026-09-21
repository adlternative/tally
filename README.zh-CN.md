# tally

**用 Jev 分析社交媒体评论。每个百分比都能回到原文，一次请求出结果。**

[English](README.md)

指着一个评论区，选几个标签（`喜欢 / 中立 / 讨厌 / 无关`），它会把同一个问题逐条问给 Jev，
然后由**代码**数出答案。你得到的是一个百分比、它背后的分母、按人去重的人数，以及产生这个
百分比的那一串逐条判定。

```
30 条评论 · 一次请求 · 1.1 秒 · $0.00022

喜欢  46.7%  14
中立  30.0%   9
讨厌  16.7%   5
无关   6.7%   2
```

---

## 为什么是 Jev，而不是普通大模型

普通大模型当然能做这件事。区别在于这件事要花多少次往返。

判断 N 条评论，用 chat 模型通常只有两条路：**每条调一次**（N 次请求，耗时随评论数线性增长），
或者把 N 条塞进一次生成里让它输出结构化答案（一次请求，但解析、重试、以及 N 变大后输出格式
漂移全都要你自己扛）。这两条路都不会顺便给你每条评论一个可用的概率。

Jev 是另一种形状：**许多彼此独立的小问题，在一次请求里一起回答。** 评论作为一份共享状态进去，
每条评论各自拥有一个指向自己的问题，一次响应回来的是每条评论对应的选项概率。

| | chat 模型，每条调一次 | Jev |
|---|---|---|
| 30 条评论需要几次往返 | 30 | **1** |
| 实测耗时（30 条） | — | **1.1 秒** |
| 拿到的输出 | 自由文本 / 你提示出来的 JSON | 类型化：`choice`、`noul`、`score` |
| 每条评论的概率 | 不返回 | 返回 |
| 输出 token 计费 | 计费 | 免费（只算输入） |

这就是这个工具存在的理由。判断一千条评论不是一千倍的工作量，只是同一个请求里更大的状态。
费用只算输入 token，大约 **每 1 万条评论 $0.02–0.03**。

实测数据：一个 33 条回复的 V2EX 帖，输入 5,198 tokens，$0.000218，1.13 秒。
想对比的话，把同样这 30 条丢给你常用的 chat 模型跑一遍，结论由你自己得出。

---

## 快速开始

```bash
git clone https://github.com/adlternative/tally.git
cd tally/frontend && npm ci && npm run build && cd ..
python3 tally.py --serve          # → http://127.0.0.1:8020
```

需要 Python 3.11+；Node 22.12+ **只在构建界面时需要**，构建好的版本用 Python 就能跑。
首次打开会弹出设置：填入 [TypeSafe API key](https://console.typesafe.ai/)，验证通过后以
`0600` 权限存到 `~/.jev-tally/token`。

命令行不需要构建：

```bash
python3 tally.py --source v2ex --ident https://www.v2ex.com/t/1243550 --limit 30 \
                 --kind choice --options '喜欢,中立,讨厌,无关' '这条评论是什么态度？'

python3 tally.py https://example.com '这一页讲了什么'
```

---

## 数据源

一个数据源 = **一条打印 JSON 的命令 + 指出哪个字段是正文的路径**，仅此而已。所以九个站点能塞进
一个文件，你自己的爬虫也同样能接进来。

```json
"v2ex": {
  "label": "V2EX 帖子回复",
  "ident": "话题 id（t/ 后面的数字）",
  "run":   "opencli v2ex replies {ident} --limit {limit} -f json",
  "text": "content", "author": "author"
}
```

内置：B站、微博、小红书、知乎回答评论、YouTube、V2EX、Hacker News，另有一个完全不用爬虫框架、
只用 `curl` 的示例插件。

多数内置源是 [OpenCLI](https://github.com/jackwener/opencli) 的一行命令（公开 npm 包，需自行安装）。
V2EX 和 Hacker News 不需要登录；另外几个需要浏览器已登录对应站点，部分 adapter 可能还要额外安装。
命令以 **argv 执行，绝不经过 shell**，所以标识里带 `; rm -rf /` 也只是一个文件名。

自己的源放进 `sources.d/`，只要能打印 JSON：

```json
{
  "my-crawler": {
    "label": "我的来源",
    "ident": "帖子 ID",
    "run": "python3 /absolute/path/to/source.py {ident} --limit {limit}",
    "items": "data.comments",
    "text": "content", "author": "user.name"
  }
}
```

两个可选字段很顶用：`ident_from`（用正则从粘贴的链接里取出 ID，链接和 ID 都能用）、
`skip`（在进入分母之前丢掉不是观点的行 —— 帖子本体、楼中楼、「[+N more replies]」占位行）。

---

## 你会得到什么

- **分母。** `46.7%` 意味着 30 条里数出来 14 条。抓了多少条、真正判断了多少条，都看得见。
- **逐条明细。** 每条评论的判定与把握度，可按最不确定排序 —— 在引用这个百分比之前，先读分歧。
- **不花钱的筛选。** 点分类、搜正文或作者、改排序：这是重绘已经付过费的判断，不会重新请求。
- **评论数 ≠ 人数。** 一个作者发五条只算一个观点，两种口径都会给出。
- **缓存。** 抓取按「来源 + 标识 + 条数」缓存；问第二个问题直接复用，只有点「重新获取」才会再抓。

---

## 这些数字的边界

- **判断不是事实。** Jev 给的是每条评论的概率。连跑两次会有评论翻转 —— 逐条明细就是为这个存在的。
- **样本不是全体。** B站/微博默认返回按热度排序的一页。`30 条里的 46.7%` 不等于
  「那个视频下所有评论者的 46.7%」。
- **`无关` 必须单独一档。** 只给「喜欢/反对」时，所有没提主题的评论都会被算成反对，得到的是一个
  看起来精确、实则错误的比值。
- **超大线程。** 当前版本不会把超限任务自动拆成多次请求；状态超出上下文预算时请下调条数。

---

## 开发

```bash
python3 test_tally.py     # 引擎：抽取、检索、统计、token 处理、静态文件
python3 test_sources.py   # 数据源层：argv 组装、缓存、ID 规则、插件加载
python3 test_e2e.py       # 23 个场景，打真实服务（上游与数据源为本地 fixture）
cd frontend
npm run dev               # Vite 开发服务，API 代理到 127.0.0.1:8020
npm test                  # 32 个 Playwright 场景，操作真实界面
npm run build             # 类型检查 + 生产构建
```

测试不调用任何收费模型、也不碰真实平台：假上游与固定数据源让百分比可断言、跑一次不花钱，
并且隔离了 `HOME`，开发者自己的 token 不可能被发出去。也正因为如此，测试全绿只证明这条流水线
是通的，不代表某个外部 adapter 一定可用。

```
tally.py                     引擎 + CLI + 静态/API 服务（仅标准库）
sources.py, sources.json     数据源协议与内置源
sources.d/                   你自己的源
frontend/src/                React 工作台（Vite、Tailwind、shadcn/ui、i18next）
frontend/tests/              Playwright 场景与隔离的 fixture 后端
scripts/package_release.py   打一个本地、无需 Node 的运行包
```

环境变量：`JEV_TALLY_API_BASE`（指向另一个 Jev 兼容端点 —— 你的 key 会发给它）、
`JEV_TALLY_SOURCES_D`、`JEV_TALLY_CACHE_DIR`、`JEV_TALLY_TOKEN_FILE`。

## 许可证

MIT，第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
