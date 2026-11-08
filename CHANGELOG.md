# Changelog

## v1.1.1 (2026-11-08)

### 修复（假红线治理）
- **带 BOM 文件被误报为 JSON 语法错误（PAV-F-001）**：`read_text` 的编码尝试顺序原为 `utf-8 → utf-8-sig`，而带 BOM 的文件用 `utf-8` 也能"成功"解码（BOM 变成 `\ufeff` 留在字符串里），永远不会轮到 `utf-8-sig`，导致 `json.loads` 抛 `Unexpected UTF-8 BOM`。现改为 `utf-8-sig → utf-8 → gbk → utf-16`，并强制剥除任何残留 BOM。
- **`ctx["errors"]` 被当元组解包**：错误的 `for rel, err in ctx["errors"]` 会迭代出字典键名 `file`/`error`，产出无意义的红线记录。改为显式取字典字段并做类型守卫。
- **临时/锁/备份文件进入扫描范围**：新增 `is_junk_file()` 过滤 `~$*`、`.~*`、`#*`、`*.tmp/.temp/.bak/.orig/.swp/.swo/.crdownload/.partial`、`desktop.ini`、`thumbs.db`、`.ds_store`；目录忽略新增 `__MACOSX`。

### 新增
- JSON 解析失败且文本以 BOM 开头时自动剥除重试一次，仍失败才计入 PAV-F-001
- `load_json` 改用 `utf-8-sig`，同时兼容有/无 BOM 的状态文件
- selftest 增加三类回归 fixture 与断言：UTF-8-BOM 的合法 JSON 不得报红、`~$`/`.bak` 临时文件不得进入扫描、UTF-16 JSON 必须能正确回读

## v1.1.0 (2026-11-07)

### 新增
- **十轮深度优化模式 `optimize`**：全自动最多 10 轮「校验→修复→优化」循环，每轮记录质量分（0–100），连续 2 轮无改进自动收敛
- **UI 完善层扫描**（PAV-UI-001~006）：图片资源缺失/破图、showLoading 无 hideLoading、wx.request 无失败回调、空样式文件、!important 失控、白屏页面
- **组件缺失检测 PAV-F-007**：usingComponents 引用的组件缺实现文件，可自动补占位四件套
- **中风险白名单自动修复**（`--auto-level medium`，optimize 默认）：补缺失页面/组件占位、明文 http→https，均沙箱备份可回滚
- **质量分趋势与历史**：`.pav/history.json`，《十轮优化报告.md》总汇报
- selftest 升级：10 类缺陷 fixture 端到端验证（检出→修复→回归→守卫→报告）

### 变更
- PAV-S-002（明文 http）由 L1 补丁升级为 L0m 自动修复
- verify 回归对比改为（编码+文件）多重集，对行号漂移稳健

## v1.0.0 (2026-11-06)

- 六阶段闭环：感知 → 诊断 → 评估 → 规划 → 执行 → 验证
- 28 项静态扫描编码（PAV-F/S/U/M/X）
- L0–L3 修复权限分级 + 修复权限矩阵文档
- 沙箱备份、同一问题 3 次上限自动交接、《交接报告》
- 回归验证「绿了≠对了」证据匹配守卫、文件骤减守卫
- 零依赖 Python CLI，selftest 端到端自测
