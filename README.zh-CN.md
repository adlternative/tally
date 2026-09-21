# tally

**逐条判断评论，用代码统计比例，每个结论都能回到原文。**

[English](README.md)

## 界面与技术栈

前端采用 React、TypeScript、Vite、Tailwind CSS v4、正式 shadcn/ui / Radix 组件、Lucide 图标与 i18next。
左侧集中配置来源与问题，右侧展示分布、评论明细、来源原文和会话历史。
语言切换与 token 配置统一放在设置弹窗。

保留 Python 引擎、命令行和自定义来源插件；不是给旧 HTML 套一个 React 外壳。
支持五种预设、自定义四种问法、整页/逐条、按评论/作者统计、分类筛选、文本/作者搜索、排序及展开全文。

## 从源码启动

构建需要 Python 3.11+、Node.js 22.12+ 和 npm：

```bash
cd frontend
npm ci
npm run build
cd ..
python3 tally.py --serve
```

打开 http://127.0.0.1:8020 。Python 提供 `frontend/dist/` 中的构建页面和静态资源。
运行已经构建好的版本不需要 Node；CLI 也不依赖前端构建。

开发时保持 Python 服务运行，在另一个终端执行：

```bash
cd frontend
npm run dev
```

Vite 会将 API 请求代理到 8020。依赖使用公共 npm registry，并提供锁文件。

## 本地发布包

构建完成后执行：

```bash
python3 scripts/package_release.py
```

生成 `artifacts/tally-runtime.zip`。解压后运行 `python3 tally/tally.py --serve` 即可。
包内只含运行需要的 Python、构建前端、内置来源、示例插件及文档/许可，不含凭据、缓存、node_modules、私有插件或测试产物。
此命令仅在本地打包，不提交、不上传、不发布。

## Token 与隐私

在设置中填写你自己的 [TypeSafe API key](https://console.typesafe.ai/)。
使用免费 models 接口验证，成功后以 0600 权限保存到 `~/.jev-tally/token`，不回显已有 token、不写入 localStorage。
优先级：`TYPESAFE_API_KEY` → `JEV_TOKEN` → `~/.jev-tally/token` → 兼容的 `~/jev_token`。
环境变量优先于设置中写入的文件。

这是可信本机工具，不是多租户服务。点击分析会把问题与来源内容发送给 TypeSafe。
插件会执行本地命令，只安装可信插件；部分来源借助已登录浏览器。不要直接公开暴露服务。

## 来源与统计边界

内置 B站、微博、小红书、知乎回答评论、YouTube、V2EX、Hacker News，不含雪球。
大部分来源依赖另行安装的 OpenCLI 命令；B站的 `comments-all` 可能需要单独安装适配器。
插件不限 OpenCLI：任何输出 JSON 的命令都可以。协议和有效 JSON 示例见英文 README。

- 粘贴链接可自动提取部分来源的 ID；知乎需回答链接，小红书需包含签名的完整链接。
- 来源抓取有本地缓存；再次提问复用缓存，点击“重新获取”才强制更新。
- 判断结果不缓存，真实模型重复运行可能得到不同判断；整页 URL 问答会重新获取网页。
- 筛选、排序、搜索和语言切换不重新调用模型。
- 按作者统计时，同一作者计一次：是/否取平均，分类取该作者最常见判定；平票不代表明确倾向。
- 缺失作者信息时不提供按人统计。百分比仅描述抓到的样本，不能代表整个平台。
- 当前不自动拆分多个模型请求，大数据集可能超出上游上下文限制，请选择合适的条数。

切换语言时，未手改的预设问题和选项随界面翻译；手改问题、原文和已有结果保留原语言。
后端及适配器的具体错误保留其原语言。

## 测试

先构建前端，然后执行：

```bash
python3 test_tally.py
python3 test_sources.py
python3 test_e2e.py
cd frontend
npx playwright install chromium
npm test
npm run format:check
```

Playwright 使用真 Chromium 操作构建后的 React 页面，连接真实 Python 服务；上游 Jev 与数据源由本地确定性 fixture 替代。
测试 HOME、token、插件与缓存隔离，不调用收费模型。安装依赖/浏览器需联网，测试运行不依赖真实平台。
涵盖设置焦点、中英文、token 保存/失败、预设、自定义问法、历史、筛选排序、连续抓取、少量评论、加载/错误、原文转义和窄屏。

旧 `ui.html` 与假 DOM 测试已经移除。失败截图/trace 位于 `frontend/test-results/`。
通过本地 fixture 不等于所有真实站点适配器在线可用。

## 许可证

tally 使用 MIT。第三方组件和依赖保留各自许可证，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
