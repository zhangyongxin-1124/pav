# pav · Product Auto Verify 产品自动化校验与修复

<p align="center">
  <b>感知 → 诊断 → 评估 → 规划 → 执行 → 验证</b> · 六阶段闭环 · 十轮深度优化
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue" alt="python" />
  <img src="https://img.shields.io/badge/dependencies-zero-green" alt="zero deps" />
  <img src="https://img.shields.io/badge/license-MIT-orange" alt="license" />
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="platform" />
</p>

**English** | [中文](#中文说明)

---

## English

**pav** is a zero-dependency Python CLI that runs an AI-agent-friendly **six-stage closed loop** — *Sense → Diagnose → Evaluate → Plan → Fix → Verify* — over your product codebase (WeChat Mini Programs, web frontends, hybrid apps). It detects defects, fixes safe ones automatically (with sandbox backup and regression evidence), evaluates strengths/weaknesses, generates an actionable improvement plan, and iterates up to **10 rounds of deep optimization** with a per-round quality score.

### Core beliefs (hard boundaries, enforced in code)

1. **Green ≠ Correct.** A "fixed" issue must have modification evidence; disappeared issues without evidence are flagged as suspicious and NOT counted as success.
2. **Permission tiers.** L0/low-risk (console.log cleanup, obvious typos) auto-fix; L0m/medium whitelist (create missing page/component placeholders, http→https) auto-fix only in `optimize`/`--auto-level medium`; L1 generates diff patches for human review; L2 refactor = plan only, zero code changes; L3 (assertions, test expectations, data models, feature deletion) **never touched automatically**.
3. **Bounded autonomy.** Max 3 attempts per issue, then a handover report saying what was tried and where it got stuck.
4. **Full observability.** Every scan, fix, patch and score is written to `.pav/` for audit; every auto-fix is sandbox-backed and reversible.

### Quick start

```bash
# 10-round deep optimization (recommended entry): auto scan→fix→optimize loop
python scripts/pav.py optimize --project /path/to/your-project

# Single stages
python scripts/pav.py sense     --project /path/to/your-project   # ① scan
python scripts/pav.py diagnose  --project /path/to/your-project   # ② triage
python scripts/pav.py evaluate  --project /path/to/your-project   # ③ strengths/weaknesses
python scripts/pav.py plan      --project /path/to/your-project   # ④ improvement plan
python scripts/pav.py fix       --project /path/to/your-project --auto  # ⑤ fix (L0)
python scripts/pav.py verify    --project /path/to/your-project   # ⑥ regression + evidence match

# End-to-end self test (builds a defective fixture, verifies the whole loop)
python scripts/pav.py selftest
```

Outputs land in `<project>/.pav/`: `十轮优化报告.md` (10-round optimization report: score trend, fix stats, pending patches, leftover issues, boundary statement), `history.json` (per-round quality score), `fix-log.json` (every modification + sandbox backup path), `patches/` (L1 diffs awaiting approval), plus per-stage JSON.

**What it scans** (28+ checks): dead navigation links, missing pages/components, `wx.*` API typos, missing `bindtap` handlers, hardcoded secrets, plaintext http, hardcoded localhost, debug leftovers, TODO/FIXME, hot spots (long files/functions), loose equality, duplicated code, broken/missing images, `showLoading` without `hideLoading`, `wx.request` without fail callback, empty styles, empty pages, `!important` abuse…

See [SKILL.md](SKILL.md) (full manual) · [references/issue-codes.md](references/issue-codes.md) (issue code registry) · [references/fix-permission-matrix.md](references/fix-permission-matrix.md) (permission matrix).

### Designed for AI coding agents

pav is built to be driven by an AI agent (Claude Code, Cursor, WorkBuddy, etc.): each stage is a standalone CLI command with machine-readable JSON state, honest exit codes (2 = suspicious results), and hard rules the agent cannot bypass. Point your agent at `SKILL.md` and it knows the whole playbook.

---

## 中文说明

**pav（Product Auto Verify）** 是一个零依赖的 Python CLI 工具，为 AI 编程智能体设计，对产品代码库（微信小程序 / Web 前端 / 混合 App）执行「**感知 → 诊断 → 评估 → 规划 → 执行 → 验证**」六阶段闭环：自动校验功能缺陷 → 按权限分级修复（沙箱备份 + 回归证据）→ 自动评估优劣势 → 生成可执行改进方案 → 最多 **10 轮深度优化**（每轮带质量分，收敛即停）。

### 为什么不是"全自动跑测试"？

闭环的价值不是"全自动"，而是**每一环都有可观测的输入、证据和退出条件**：

| 铁边界 | 实现 |
|---|---|
| **绿了 ≠ 对了** | 问题消失但无修复改动证据 → 标记"可疑"，不计为修复成功；文件行数骤减 >30% → 警告"靠删代码变绿" |
| **权限分级** | L0 低风险自动改（沙箱备份）｜L0m 中风险白名单（补占位页/组件、http→https，仅 optimize 模式）｜L1 只生成 diff 补丁待人工审批｜L2 重构只出方案零落码｜L3 断言/测试期望/数据模型**禁止自动触碰** |
| **修复有上限** | 同一问题最多尝试 3 次，超限自动出《交接报告》说明尝试了什么、卡在哪 |
| **全程可审计** | 六阶段全部落盘 `.pav/`，每次自动修改都有沙箱备份路径，可整体回滚 |

### 快速开始

```bash
# ★ 十轮深度优化（推荐入口）：全自动 10 轮校验+修复+优化
python scripts/pav.py optimize --project D:/项目路径

# 普通闭环（N 轮，先 dry-run 只看不改）
python scripts/pav.py loop --project D:/项目路径 --rounds 2 --dry

# 端到端自测（构造含 10 类缺陷的临时工程，验证全闭环）
python scripts/pav.py selftest
```

### 产出物

| 产出 | 位置 | 内容 |
|---|---|---|
| 《十轮优化报告》 | `<项目>/.pav/十轮优化报告.md` | 质量分趋势表 + 按类型修复成果 + 待审批补丁清单 + 遗留问题清单（按权限分级）+ 边界声明 |
| 质量分历史 | `.pav/history.json` | 每轮得分（0–100 = 100 − 红线×15 − 阻断×8 − 提醒×3 − 信息×1），连续 2 轮无改进自动收敛 |
| 修复日志 | `.pav/fix-log.json` | 每次修改的时间/文件/摘要/沙箱备份路径 |
| 待审批补丁 | `.pav/patches/` | L1 级问题的 unified diff，人工审批后应用 |
| 六阶段明细 | `.pav/sense.json`、`diagnose.json`、`evaluate.json`、`plan.json` | 每个阶段的机器可读状态 |

### 检测覆盖（28+ 项，持续增加）

- **功能层**：导航死链、app.json 声明页面缺失、usingComponents 组件缺失、wx API 拼写错误（相似度匹配）、bindtap 处理器缺失
- **安全/环境层**：密钥硬编码、明文 http、本地地址硬编码
- **可维护性**：超长文件/函数热点、console.log 残留、TODO/FIXME、宽松相等、跨文件重复代码
- **UI 完善层**：图片资源缺失（破图）/缺 src、showLoading 无 hideLoading（加载框卡死）、wx.request 无失败回调、空样式文件、白屏页面、!important 失控

### 设计目标：给 AI 编程智能体用

pav 为 AI Agent（Claude Code / Cursor / WorkBuddy 等）设计：每个阶段都是独立 CLI 命令 + 机器可读 JSON 状态 + 诚实的退出码（2 = 有可疑结果，不得宣称全部修复成功）+ 智能体无法绕过的硬规则。把 `SKILL.md` 喂给你的智能体即可。

### 适配范围

- ✅ 微信小程序（app.json / wxml / wxss / js）
- ✅ 通用 Web 前端 / 混合 App（html / js / css）
- ⚠️ 纯后端项目仅覆盖 js/ts 通用规则；Java/Kotlin 等暂不支持
- 静态扫描 ≠ 运行时测试：交互流程、网络异常、真机表现需人工手测补盲区

### 版本

- **v1.1.1** — 假红线治理：编码回读改为先 `utf-8-sig`（修复带 BOM 文件被误报 JSON 语法错误）、临时/锁/备份文件与 `__MACOSX` 不再计入扫描、`ctx["errors"]` 字典解包缺陷彻底修正；selftest 增加 BOM / UTF-16 / 临时文件回归断言
- **v1.1.0** — 十轮深度优化模式（质量分趋势 + 收敛判定 + 《十轮优化报告》）、UI 完善层 6 类扫描、组件缺失检测、中风险白名单自动修复
- **v1.0.0** — 六阶段闭环、28 项静态扫描、L0–L3 权限分级、沙箱备份、3 次上限交接、回归证据匹配守卫

## License

[MIT](LICENSE)
