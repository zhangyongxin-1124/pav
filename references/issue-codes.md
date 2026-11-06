# pav 统一问题编码规范

格式：`PAV-<域>-<序号>`。

## 域代码

| 域 | 含义 | 覆盖层次 |
|---|---|---|
| PAV-F | 功能正确性 | 功能层 |
| PAV-UI | UI 完善层 | 界面完善 |
| PAV-S | 安全 | 非功能 |
| PAV-U | 稳定/环境 | 非功能 |
| PAV-M | 可维护性 | 非功能 |
| PAV-X | 工具自身异常 | 可观测 |

权限列说明：L0=低风险自动；L0m=中风险白名单（仅 --auto-level medium，optimize 默认）；L1=补丁待审批；L2=只出方案；—=仅诊断。

## 编码注册表（v1.0.0）

| 编码 | 级别 | 修复权限 | 说明 |
|---|---|---|---|
| PAV-F-001 | 红线 | L1 | JSON 配置语法错误 |
| PAV-F-002 | 红线 | L1 | app.json 页面缺实现文件 |
| PAV-F-003 | 阻断 | L1 | tabBar 图标缺失 |
| PAV-F-004 | 阻断 | — | 导航死链 |
| PAV-F-005 | 红线 | L0 | wx API 拼写错误（可自动更正） |
| PAV-F-006 | 阻断 | — | bindtap 处理器缺失 |
| PAV-F-007 | 红线 | L0m（补占位） | usingComponents 组件缺实现文件 |
| PAV-S-001 | 红线 | L1 | 密钥/凭证硬编码 |
| PAV-S-002 | 阻断 | L0m（http→https） | 明文 http 请求 |
| PAV-U-001 | 红线 | L1 | 本地地址硬编码 |
| PAV-M-001 | 提醒 | L0 | console.log 残留 ≥3 行 |
| PAV-M-002 | 信息 | L2 | TODO/FIXME/HACK 标记 |
| PAV-M-003 | 阻断 | L2 | 超长文件（>400 行） |
| PAV-M-004 | 提醒 | L2 | 过长/高复杂度函数（>60 行或复杂度>15） |
| PAV-M-005 | 提醒 | — | 宽松相等 ==（≥5 处） |
| PAV-M-006 | 提醒 | L2 | 跨文件重复代码块 |
| PAV-UI-001 | 阻断/提醒 | — | 图片资源不存在（破图）/ image 缺 src |
| PAV-UI-002 | 阻断 | L2 | showLoading 无对应 hideLoading |
| PAV-UI-003 | 阻断 | L2 | wx.request 无 fail/complete 回调 |
| PAV-UI-004 | 信息 | L2 | 页面样式文件为空 |
| PAV-UI-005 | 提醒 | L2 | !important 密度失控（≥3 处） |
| PAV-UI-006 | 阻断 | L2 | 页面 wxml 为空（白屏页面） |
| PAV-X-999 | 信息 | — | 扫描器自身异常（降级不中断） |

## 新增编码流程

新问题模式 → 在 pav.py 增加扫描器并注册编码 → `pav selftest` 回归 → 更新本表 → 若为踩坑类，用 `rfe pitfall add --pending` 同步沉淀知识库。
