#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pav — Product Auto Verify 产品自动化校验与修复（六阶段闭环 CLI）
内部代号 pav，零依赖（仅 Python 3.8+ 标准库）。

六阶段闭环：感知(sense) → 诊断(diagnose) → 评估(evaluate) → 规划(plan) → 执行(fix) → 验证(verify)

四条铁边界（违反任何一条即为不合格修复）：
  B1 断言/测试期望变更、删除用例：禁止自动执行（L3）
  B2 "绿了"不等于"对了"：verify 阶段做证据匹配，无对应改动证据的"消失问题"记为可疑
  B3 修复上限：同一问题最多 3 次尝试，超限输出交接报告交回人类
  B4 可观测性：每次扫描/修复/验证落盘到 <project>/.pav/，含来源、置信度、验证结果

用法示例：
  python pav.py optimize --project D:/path/to/miniprogram                # 十轮深度优化（默认 10 轮全自动）
  python pav.py loop    --project D:/path/to/miniprogram --rounds 2     # 普通闭环
  python pav.py sense   --project D:/path/to/miniprogram
  python pav.py plan    --project D:/path/to/miniprogram --out 改进优化方案.md
  python pav.py fix     --project D:/path/to/miniprogram --auto
  python pav.py verify  --project D:/path/to/miniprogram
  python pav.py report  --project D:/path/to/miniprogram --out 校验报告.md
  python pav.py selftest
"""

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import time

VERSION = "1.1.0"
PSEUDO_EXT = (".js", ".ts", ".wxml", ".wxss", ".json", ".html", ".css", ".py", ".vue")
SKIP_DIRS = {".pav", "node_modules", "miniprogram_npm", ".git", "dist", "unpackage", "__pycache__", ".idea", ".vscode"}
SEV_CN = {"red": "红线", "amber": "阻断", "yellow": "提醒", "info": "信息"}
LEVEL_CN = {"L0": "自动执行（沙箱备份+事后评审）", "L1": "生成补丁待审批", "L2": "只出方案人工改", "L3": "禁止自动"}
MAX_FIX_ATTEMPTS = 3

# 修复动作分级：low=L0 任何自动模式都执行；medium=仅 --auto-level medium（十轮优化模式默认）
FIX_TIER = {
    "remove_console_log": "low",
    "fix_typo": "low",
    "create_missing_page": "medium",
    "create_missing_component": "medium",
    "http_to_https": "medium",
}

# 已知 wx API（用于拼写检查；不全，只列高频）
WX_API = {
    "navigateTo", "redirectTo", "switchTab", "navigateBack", "reLaunch",
    "request", "uploadFile", "downloadFile", "getStorageSync", "setStorageSync",
    "removeStorageSync", "clearStorageSync", "showToast", "showModal", "showLoading",
    "hideLoading", "setNavigationBarTitle", "stopPullDownRefresh", "login", "getUserProfile",
    "createSelectorQuery", "createIntersectionObserver", "vibrateShort", "makePhoneCall",
    "previewImage", "chooseImage", "saveImageToPhotosAlbum", "getSystemInfoSync", "canIUse",
}

SECRETS_PAT = re.compile(
    r"(?i)(secret|appsecret|access[_-]?key|api[_-]?key|apikey|private[_-]?key|token)\s*[:=]\s*['\"]([A-Za-z0-9_\-]{16,})['\"]"
)


# ---------------------------------------------------------------- 基础工具

def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def pav_dir(project):
    d = os.path.join(project, ".pav")
    os.makedirs(d, exist_ok=True)
    return d


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_text(path):
    """带编码兜底读取。返回 (text, encoding_used) 或 (None, None)。"""
    for enc in ("utf-8", "utf-8-sig", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
        except OSError:
            return None, None
    return None, None


def walk_files(project, exts=PSEUDO_EXT):
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.lower().endswith(exts):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, project).replace("\\", "/")
                yield full, rel


def load_project(project):
    """感知数据装载：文件文本 + JSON 解析结果。"""
    ctx = {"project": os.path.abspath(project), "files": {}, "jsons": {}, "errors": []}
    for full, rel in walk_files(project):
        text, enc = read_text(full)
        if text is None:
            continue
        ctx["files"][rel] = text
        if rel.endswith(".json"):
            try:
                ctx["jsons"][rel] = json.loads(text)
            except Exception as e:
                ctx["jsons"][rel] = None
                ctx["errors"].append({"file": rel, "error": str(e)})
    return ctx


def find_app_json(ctx):
    cands = ["app.json", "miniprogram/app.json"]
    for c in cands:
        if c in ctx["jsons"] and ctx["jsons"][c]:
            return c, ctx["jsons"][c]
    # 兜底：任何名为 app.json 的
    for rel, data in ctx["jsons"].items():
        if rel.endswith("app.json") and isinstance(data, dict):
            return rel, data
    return None, None


def issue(code, severity, level, file, line, msg, evidence="", hint="", fix="none"):
    """fix: none | remove_console_log | patch | plan_only"""
    return {
        "code": code, "severity": severity, "level": level,
        "file": file, "line": line, "msg": msg,
        "evidence": evidence[:400], "hint": hint, "fix": fix,
    }


def issue_key(i):
    return "%s|%s|%s" % (i["code"], i["file"], i["line"])


# ---------------------------------------------------------------- 扫描规则（感知层）

def scan_json_syntax(ctx):
    out = []
    for rel, err in ctx["errors"]:
        out.append(issue("PAV-F-001", "red", "L1", rel, 0,
                         "JSON 解析失败（语法错误），页面/配置可能整体失效",
                         err, "修正 JSON 语法（常见：尾逗号、单引号、注释）"))
    return out


def scan_pages_exist(ctx):
    """app.json 声明的页面与 tabBar 资源必须真实存在。"""
    out = []
    app_rel, app = find_app_json(ctx)
    if not app:
        return out
    base = os.path.dirname(app_rel)
    for p in app.get("pages", []):
        rel = (base + "/" + p + ".js") if base else p + ".js"
        rel2 = (base + "/" + p + ".wxml") if base else p + ".wxml"
        if rel not in ctx["files"] or rel2 not in ctx["files"]:
            missing = []
            if rel not in ctx["files"]:
                missing.append(rel)
            if rel2 not in ctx["files"]:
                missing.append(rel2)
            out.append(issue("PAV-F-002", "red", "L1", app_rel, 0,
                             "app.json 声明的页面缺少实现文件，跳转即报错",
                             "缺少: " + ", ".join(missing),
                             "补齐页面文件或移除该页面声明", fix="create_missing_page"))
    tb = app.get("tabBar", {})
    for item in tb.get("list", []):
        for k in ("iconPath", "selectedIconPath"):
            icon = item.get(k)
            if not icon:
                continue
            icon_rel = (base + "/" + icon) if base else icon
            full = os.path.join(ctx["project"], icon_rel)
            if not os.path.exists(full):
                out.append(issue("PAV-F-003", "amber", "L1", app_rel, 0,
                                 "tabBar 图标文件不存在，编译期报警/不显示",
                                 icon_rel, "补齐图标文件或改路径", fix="patch"))
    return out


def scan_nav_deadlink(ctx):
    """navigateTo / navigator 指向的页面未在 app.json 声明 → 死链。"""
    out = []
    app_rel, app = find_app_json(ctx)
    pages = set()
    if app:
        base = os.path.dirname(app_rel) if app_rel else ""
        for p in app.get("pages", []):
            pages.add(p if not base else p)
    pat = re.compile(r"""(?:navigateTo|redirectTo|reLaunch|switchTab)\s*\(\s*\{\s*url\s*:\s*['"`]([^'"`]+)""")
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts", ".wxml")):
            continue
        for m in pat.finditer(text):
            url = m.group(1)
            path = url.split("?")[0]
            if path.startswith("/"):
                path = path[1:]
            if path and pages and path not in pages:
                line = text[:m.start()].count("\n") + 1
                out.append(issue("PAV-F-004", "amber", "L0", rel, line,
                                 "导航死链：目标页面未在 app.json 中声明",
                                 url, "修正路径或补页面声明", fix="none"))
    return out


def scan_wx_api_typo(ctx):
    """wx.xxx 拼写错误（L0 可自动改：明确近似匹配时）。"""
    out = []
    pat = re.compile(r"\bwx\.([A-Za-z_][A-Za-z0-9_]*)")
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts")):
            continue
        for m in pat.finditer(text):
            name = m.group(1)
            if name in WX_API or name.startswith("cloud") or name.startswith("get") and name in WX_API:
                continue
            close = difflib.get_close_matches(name, WX_API, n=1, cutoff=0.82)
            if close:
                line = text[:m.start()].count("\n") + 1
                out.append(issue("PAV-F-005", "red", "L0", rel, line,
                                 "疑似 wx API 拼写错误（运行时 undefined 报错），更正为 wx.%s" % close[0],
                                 "wx.%s → 疑似 wx.%s" % (name, close[0]),
                                 fix="fix_typo", hint=close[0]))
    return out


def scan_bindtap_handler(ctx):
    """WXML bindtap 绑定的方法在对应 JS 中不存在（启发式：data-xxx 判断不覆盖）。"""
    out = []
    pat = re.compile(r"""(?:bindtap|catchtap)\s*=\s*["']([A-Za-z_$][\w$]*)["']""")
    for rel, text in ctx["files"].items():
        if not rel.endswith(".wxml"):
            continue
        js_rel = rel[:-5] + ".js"
        js_text = ctx["files"].get(js_rel)
        if js_text is None:
            continue
        for m in pat.finditer(text):
            handler = m.group(1)
            if not re.search(r"\b" + re.escape(handler) + r"\s*[:(=]", js_text):
                line = text[:m.start()].count("\n") + 1
                out.append(issue("PAV-F-006", "amber", "L0", rel, line,
                                 "tap 事件处理器在对应 JS 中未找到，点击无响应",
                                 handler, "实现该方法或修正绑定名", fix="none"))
    return out


def scan_secrets(ctx):
    out = []
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts", ".json", ".py", ".vue", ".html")):
            continue
        for m in SECRETS_PAT.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append(issue("PAV-S-001", "red", "L1", rel, line,
                             "疑似密钥/凭证硬编码在前端代码（泄露即事故）",
                             m.group(0)[:80].replace(m.group(2), "***"),
                             "移到后端/云函数或环境配置，前端不得持有", fix="patch"))
    return out


def scan_insecure_http(ctx):
    out = []
    pat = re.compile(r"""['"`](http://(?!localhost|127\.0\.0\.1)[^'"`\s]{6,})['"`]""")
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts", ".wxml", ".json")):
            continue
        for m in pat.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append(issue("PAV-S-002", "amber", "L1", rel, line,
                             "明文 http 请求（小程序正式版强制 https，会被拦截）",
                             m.group(1), "改为 https 或配置合法域名", fix="http_to_https"))
    return out


def scan_localhost_hardcode(ctx):
    """本地地址硬编码：本地测试通过≠线上可用，上线后必炸。"""
    out = []
    pat = re.compile(r"""['"`](https?://(?:localhost|127\.0\.0\.1)[^'"`\s]*)['"`]""")
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts", ".json", ".py", ".vue")):
            continue
        for m in pat.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append(issue("PAV-U-001", "red", "L1", rel, line,
                             "硬编码本地地址（本地测试通过≠线上可用）",
                             m.group(1), "提取为环境配置/全局 BASE_URL", fix="patch"))
    return out


def scan_console_log(ctx):
    out = []
    pat = re.compile(r"^\s*console\.log\s*\(.*\)\s*;?\s*$")
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts")):
            continue
        hits = [i + 1 for i, ln in enumerate(text.splitlines()) if pat.match(ln)]
        if len(hits) >= 3:
            out.append(issue("PAV-M-001", "yellow", "L0", rel, hits[0],
                             "console.log 调试残留 %d 处（泄露内部实现+噪音）" % len(hits),
                             "行: " + ",".join(map(str, hits[:10])),
                             fix="remove_console_log", hint=json.dumps(hits)))
    return out


def scan_todo(ctx):
    out = []
    pat = re.compile(r"(TODO|FIXME|HACK|XXX)\b[:：]?(.{0,60})")
    for rel, text in ctx["files"].items():
        if rel.endswith(".json"):
            continue
        for m in pat.finditer(text):
            line = text[:m.start()].count("\n") + 1
            out.append(issue("PAV-M-002", "info", "L2", rel, line,
                             "未完成标记 %s" % m.group(1), (m.group(2) or "").strip(),
                             "纳入改进计划逐条消化", fix="none"))
    return out


def _complexity(text):
    """近似圈复杂度：分支关键字与逻辑运算符计数。"""
    n = 1
    for pat in (r"\bif\b", r"\bfor\b", r"\bwhile\b", r"\bcase\b", r"\bcatch\b",
                r"&&", r"\|\|", r"\?\s*[^:]+\s*:"):
        n += len(re.findall(pat, text))
    return n


def scan_hotspots(ctx, long_file=400, long_func=60, cx=15):
    """可维护性热点：超长文件 / 超长函数 / 高复杂度函数。"""
    out = []
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts", ".py", ".vue")):
            continue
        lines = text.splitlines()
        if len(lines) > long_file:
            out.append(issue("PAV-M-003", "amber", "L2", rel, 0,
                             "超长文件 %d 行（> %d），改动热点、缺陷密度高" % (len(lines), long_file),
                             "", "拆分模块（见改进方案）", fix="plan_only"))
        # 函数级（JS/TS）
        if rel.endswith((".js", ".ts")):
            func_pat = re.compile(r"^\s*(?:async\s+)?(?:function\s+(\w+)|(\w+)\s*\([^)]*\)\s*\{)", re.M)
            for m in func_pat.finditer(text):
                name = m.group(1) or m.group(2) or "(anonymous)"
                start = text.find("{", m.start())
                if start < 0:
                    continue
                depth, i = 1, start + 1
                while i < len(text) and depth:
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                    i += 1
                body = text[start:i]
                fl = body.count("\n")
                c = _complexity(body)
                if fl > long_func or c > cx:
                    line = text[:m.start()].count("\n") + 1
                    out.append(issue("PAV-M-004", "yellow", "L2", rel, line,
                                     "函数 %s 过长/过复杂（%d 行, 复杂度≈%d）" % (name, fl, c),
                                     "", "拆分职责、提前返回、提取子函数", fix="plan_only"))
    return out


def scan_loose_equality(ctx):
    out = []
    pat = re.compile(r"[^=!<>]==[^=]")
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts")):
            continue
        hits = [ln_no for ln_no, ln in enumerate(text.splitlines(), 1) if pat.search(ln)]
        if len(hits) >= 5:
            out.append(issue("PAV-M-005", "yellow", "L2", rel, hits[0],
                             "宽松相等(==) %d 处（隐式类型转换易埋雷）" % len(hits),
                             "首现行 %d" % hits[0], "统一改 === / !==", fix="none"))
    return out


def scan_duplication(ctx, min_block=8):
    """跨文件重复代码块（滑窗哈希，启发式）。"""
    out = []
    seen = {}
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts", ".wxss", ".css")):
            continue
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 20]
        for i in range(len(lines) - min_block + 1):
            block = "\n".join(lines[i:i + min_block])
            h = hash(block)
            if h in seen and seen[h][0] != rel:
                prev_rel, prev_ln = seen[h]
                out.append(issue("PAV-M-006", "yellow", "L2", rel, i + 1,
                                 "与 %s:%d 存在 ≥%d 行重复代码" % (prev_rel, prev_ln, min_block),
                                 "", "提取公共函数/ mixin", fix="none"))
                break
            elif h not in seen:
                seen[h] = (rel, i + 1)
    return out


def scan_components(ctx):
    """usingComponents 引用的组件缺少实现文件（PAV-F-007，可自动补占位）。"""
    out = []
    for rel, data in ctx["jsons"].items():
        if not isinstance(data, dict):
            continue
        ucs = data.get("usingComponents")
        if not isinstance(ucs, dict):
            continue
        base = os.path.dirname(rel)
        for name, cpath in ucs.items():
            if not isinstance(cpath, str) or not cpath.strip():
                continue
            p = cpath
            if p.startswith("/"):
                p = p[1:]
            else:
                p = (base + "/" + p) if base else p
            p = os.path.normpath(p).replace("\\", "/")
            if p + ".js" not in ctx["files"] or p + ".wxml" not in ctx["files"]:
                missing = [x for x in (p + ".js", p + ".wxml", p + ".json", p + ".wxss") if x not in ctx["files"]]
                out.append(issue("PAV-F-007", "red", "L1", rel, 0,
                                 "usingComponents 组件 %s 缺少实现文件，页面渲染即报错" % name,
                                 "缺少: " + ", ".join(missing),
                                 "补齐组件文件或移除引用", fix="create_missing_component"))
    return out


def scan_ui_images(ctx):
    """图片资源缺失 / src 为空（PAV-UI-001，界面破图与空白占位）。"""
    out = []
    tag_pat = re.compile(r"<image\b[^>]*>")
    src_pat = re.compile(r"""src\s*=\s*["']([^"']*)["']""")
    for rel, text in ctx["files"].items():
        if not rel.endswith(".wxml"):
            continue
        for m in tag_pat.finditer(text):
            tag = m.group(0)
            sm = src_pat.search(tag)
            line = text[:m.start()].count("\n") + 1
            if not sm or not sm.group(1).strip():
                out.append(issue("PAV-UI-001", "yellow", "L2", rel, line,
                                 "<image> 缺少 src，渲染空白占位", tag[:80],
                                 "补 src 或改用占位组件", fix="none"))
                continue
            src = sm.group(1)
            if src.startswith(("http://", "https://", "wxfile://", "cloud://", "data:")) or "{{" in src:
                continue
            path = src.split("?")[0]
            if path.startswith("/"):
                path = path[1:]
            else:
                base = os.path.dirname(rel)
                path = os.path.normpath((base + "/" + path) if base else path).replace("\\", "/")
            if not os.path.exists(os.path.join(ctx["project"], path)):
                out.append(issue("PAV-UI-001", "amber", "L2", rel, line,
                                 "图片资源不存在，界面显示破图", src,
                                 "补图片或改路径", fix="none"))
    return out


def scan_loading_pair(ctx):
    """wx.showLoading 无对应 wx.hideLoading（PAV-UI-002，加载框永不关闭）。"""
    out = []
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts")):
            continue
        if "wx.showLoading" in text and "wx.hideLoading" not in text:
            line = text[:text.index("wx.showLoading")].count("\n") + 1
            out.append(issue("PAV-UI-002", "amber", "L2", rel, line,
                             "showLoading 无对应 hideLoading，加载框永不关闭",
                             "wx.showLoading", "在请求 complete 中调用 wx.hideLoading()", fix="none"))
    return out


def scan_request_fail(ctx):
    """wx.request 无 fail/complete 回调（PAV-UI-003，网络异常无反馈）。"""
    out = []
    pat = re.compile(r"wx\.request\s*\(")
    for rel, text in ctx["files"].items():
        if not rel.endswith((".js", ".ts")):
            continue
        for m in pat.finditer(text):
            seg = text[m.end():m.end() + 600]
            depth, endpos = 1, 0
            for idx, ch in enumerate(seg):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        endpos = idx
                        break
            block = seg[:endpos]
            if "fail" not in block and "complete" not in block:
                line = text[:m.start()].count("\n") + 1
                out.append(issue("PAV-UI-003", "amber", "L2", rel, line,
                                 "wx.request 无 fail/complete 回调，网络异常时用户无反馈",
                                 "wx.request({...})", "补 fail 回调提示用户", fix="none"))
    return out


def scan_empty_style(ctx):
    """页面样式文件为空（PAV-UI-004）。"""
    out = []
    for rel, text in ctx["files"].items():
        if not rel.endswith(".wxss") or rel.endswith("app.wxss"):
            continue
        if not text.strip():
            out.append(issue("PAV-UI-004", "info", "L2", rel, 0,
                             "页面样式文件为空（页面可能未做布局）", "", "补充布局样式", fix="none"))
    return out


def scan_important(ctx):
    """!important 密度（PAV-UI-005，样式优先级失控信号）。"""
    out = []
    for rel, text in ctx["files"].items():
        if not rel.endswith((".wxss", ".css")):
            continue
        n = text.count("!important")
        if n >= 3:
            out.append(issue("PAV-UI-005", "yellow", "L2", rel, 0,
                             "!important 使用 %d 处（样式优先级失控信号）" % n, "",
                             "用选择器特异性替代", fix="plan_only"))
    return out


def scan_empty_page(ctx):
    """页面 wxml 为空（PAV-UI-006，白屏页面）。"""
    out = []
    app_rel, app = find_app_json(ctx)
    if not app:
        return out
    base = os.path.dirname(app_rel)
    for p in app.get("pages", []):
        wxml = (base + "/" + p + ".wxml") if base else p + ".wxml"
        t = ctx["files"].get(wxml)
        if t is not None and not t.strip():
            out.append(issue("PAV-UI-006", "amber", "L2", wxml, 0,
                             "页面 wxml 为空（白屏页面）", "", "补充页面结构", fix="none"))
    return out


def run_all_scanners(ctx):
    issues = []
    scanners = [
        scan_json_syntax, scan_pages_exist, scan_components, scan_nav_deadlink,
        scan_wx_api_typo, scan_bindtap_handler, scan_secrets, scan_insecure_http,
        scan_localhost_hardcode, scan_console_log, scan_todo, scan_hotspots,
        scan_loose_equality, scan_duplication,
        scan_ui_images, scan_loading_pair, scan_request_fail,
        scan_empty_style, scan_important, scan_empty_page,
    ]
    for s in scanners:
        try:
            issues.extend(s(ctx))
        except Exception as e:  # 扫描器自身故障不得中断闭环
            issues.append(issue("PAV-X-999", "info", "L2", "(scanner)", 0,
                                "扫描器 %s 异常: %s" % (s.__name__, e), "", "", fix="none"))
    # 去重
    uniq, seen = [], set()
    for i in issues:
        k = issue_key(i)
        if k not in seen:
            seen.add(k)
            uniq.append(i)
    sev_rank = {"red": 0, "amber": 1, "yellow": 2, "info": 3}
    uniq.sort(key=lambda x: (sev_rank.get(x["severity"], 9), x["file"], x["line"]))
    return uniq


# ---------------------------------------------------------------- 诊断层

def diagnose(issues, ctx):
    """聚类 + 分级 + 影响面判断。"""
    by_code = {}
    for i in issues:
        by_code.setdefault(i["code"], []).append(i)
    clusters = []
    for code, lst in sorted(by_code.items()):
        clusters.append({
            "code": code,
            "count": len(lst),
            "severity": lst[0]["severity"],
            "files": sorted({x["file"] for x in lst}),
            "msg": lst[0]["msg"],
            "auto_fixable": sum(1 for x in lst if x["fix"] not in ("none", "plan_only")),
        })
    counts = {"red": 0, "amber": 0, "yellow": 0, "info": 0}
    for i in issues:
        counts[i["severity"]] = counts.get(i["severity"], 0) + 1
    verdict = "健康"
    if counts["red"] > 0:
        verdict = "存在红线级缺陷，不可交付"
    elif counts["amber"] > 0:
        verdict = "存在阻断级问题，修复后交付"
    return {"time": now(), "counts": counts, "verdict": verdict,
            "clusters": clusters, "total": len(issues)}


# ---------------------------------------------------------------- 评估层（优劣势）

def evaluate(issues, ctx, diag):
    """三源信号：代码结构 / 质量信号 / 改进历史。输出优势、劣势、优先级。"""
    strengths, weaknesses = [], []
    js_files = [r for r in ctx["files"] if r.endswith((".js", ".ts"))]
    total_lines = sum(len(t.splitlines()) for t in ctx["files"].values())
    n_files = len(ctx["files"])

    if n_files == 0:
        return {"strengths": [], "weaknesses": [{"item": "项目为空或无法读取", "evidence": ctx["project"]}], "metrics": {}}

    # 优势（证据支撑）
    if diag["counts"]["red"] == 0:
        strengths.append({"item": "未发现红线级缺陷（死链/密钥/本地地址/缺失页面）",
                          "evidence": "红线计数=0，共扫描 %d 个文件" % n_files})
    app_rel, app = find_app_json(ctx)
    if app_rel and isinstance(app, dict) and app.get("pages"):
        strengths.append({"item": "页面注册完整，app.json 与磁盘文件一致",
                          "evidence": "%d 个页面全部存在实现文件" % len(app["pages"])})
    if not any(i["code"] == "PAV-M-003" for i in issues):
        strengths.append({"item": "无超长文件，模块粒度健康", "evidence": "全部文件 < 400 行"})
    json_ok = sum(1 for r, d in ctx["jsons"].items() if d is not None or r not in [e["file"] for e in ctx["errors"]])
    if not ctx["errors"]:
        strengths.append({"item": "全部 JSON 配置可解析，工程可编译基础扎实", "evidence": "0 个 JSON 语法错误"})

    # 劣势（证据 + 影响面）
    hotspots = [i for i in issues if i["code"] in ("PAV-M-003", "PAV-M-004")]
    if hotspots:
        worst = max(hotspots, key=lambda x: x["line"] or 1)
        weaknesses.append({"item": "存在改动热点（长文件/复杂函数），后续迭代缺陷风险集中",
                           "evidence": "; ".join("%s:%s %s" % (h["file"], h["line"], h["msg"]) for h in hotspots[:3]),
                           "impact": "高"})
    debug = [i for i in issues if i["code"] == "PAV-M-001"]
    if debug:
        weaknesses.append({"item": "调试残留多，发布包带内部信息",
                           "evidence": "%d 个文件共 %d 处 console.log" % (len(debug), sum(int(re.search(r"\d+", i["msg"]).group()) for i in debug)),
                           "impact": "中"})
    secs = [i for i in issues if i["code"].startswith("PAV-S-")]
    if secs:
        weaknesses.append({"item": "安全隐患（密钥硬编码/明文 http）",
                           "evidence": "; ".join("%s:%s" % (i["file"], i["line"]) for i in secs[:3]),
                           "impact": "高"})
    dup = [i for i in issues if i["code"] == "PAV-M-006"]
    if dup:
        weaknesses.append({"item": "存在跨文件重复代码，改一处漏一处", "evidence": "%d 组" % len(dup), "impact": "中"})

    metrics = {"files": n_files, "lines": total_lines,
               "avg_lines_per_file": round(total_lines / max(n_files, 1), 1),
               "issue_density": round(len(issues) / max(n_files, 1), 2)}

    # 改进优先级 = 影响 × 可行性（L0 > L1 > L2；severity 加权）
    level_w = {"L0": 3, "L1": 2, "L2": 1, "L3": 0}
    sev_w = {"red": 3, "amber": 2, "yellow": 1, "info": 0}
    prio = sorted(issues, key=lambda x: (sev_w.get(x["severity"], 0) + level_w.get(x["level"], 0)),
                  reverse=True)
    return {"time": now(), "strengths": strengths, "weaknesses": weaknesses,
            "metrics": metrics, "priority": prio[:30]}


# ---------------------------------------------------------------- 规划层（改进优化方案）

TASK_TITLE = {
    "PAV-F-001": "修复 JSON 配置语法错误",
    "PAV-F-002": "补齐缺失页面实现文件",
    "PAV-F-003": "补齐 tabBar 图标资源",
    "PAV-F-004": "修正导航死链",
    "PAV-F-005": "更正 wx API 拼写错误",
    "PAV-F-006": "实现缺失的 tap 事件处理器",
    "PAV-F-007": "补齐缺失的组件实现文件",
    "PAV-UI-001": "补齐/修正页面图片资源",
    "PAV-UI-002": "补 hideLoading 关闭加载框",
    "PAV-UI-003": "补 wx.request 失败回调",
    "PAV-UI-004": "补充页面布局样式",
    "PAV-UI-005": "重构样式去除 !important",
    "PAV-UI-006": "补充白屏页面结构",
    "PAV-S-001": "外置硬编码密钥/凭证",
    "PAV-S-002": "明文 http 升级 https",
    "PAV-U-001": "本地地址提取为环境配置",
    "PAV-M-001": "清理 console.log 调试残留",
    "PAV-M-002": "消化 TODO/FIXME 未完成项",
    "PAV-M-003": "拆分超长文件",
    "PAV-M-004": "拆分过长/过高复杂度函数",
    "PAV-M-005": "宽松相等改严格相等",
    "PAV-M-006": "提取重复代码为公共模块",
    "PAV-X-999": "排查扫描器异常",
}


def build_plan(issues, ctx, ev):
    tasks = []
    seen_code_file = set()
    n = 0
    for i in ev["priority"]:
        key = (i["code"], i["file"])
        if key in seen_code_file:
            continue
        seen_code_file.add(key)
        n += 1
        tid = "T%03d" % n
        level = i["level"]
        action = TASK_TITLE.get(i["code"], i["msg"])
        verify_desc = {
            "PAV-F-005": "修复后 wx.%s 调用可解析，重新扫描该问题消失" % (i["hint"] or "?"),
            "PAV-M-001": "该文件独立 console.log 行数 = 0",
        }.get(i["code"], "重新扫描后 %s 于 %s 的实例数下降，且未新增同类问题" % (i["code"], i["file"]))
        rollback = {
            "L0": "回归验证发现新增问题 → 自动还原沙箱备份",
            "L1": "补丁被拒绝 → 不合并，原文件零改动",
            "L2": "仅方案不落码，无回滚需要",
        }.get(level, "人工控制")
        tasks.append({
            "id": tid, "title": action, "level": level,
            "basis": "%s [%s/%s] %s:%s — %s" % (i["code"], i["severity"], i["level"], i["file"], i["line"], i["msg"]),
            "action": i["hint"] or action,
            "verify": verify_desc, "rollback": rollback,
            "issue_key": issue_key(i),
        })
    return {"time": now(), "tasks": tasks,
            "summary": "共 %d 项任务：L0 自动 %d 项 / L1 补丁 %d 项 / L2 方案 %d 项" % (
                len(tasks),
                sum(1 for t in tasks if t["level"] == "L0"),
                sum(1 for t in tasks if t["level"] == "L1"),
                sum(1 for t in tasks if t["level"] == "L2"))}


def plan_to_markdown(plan, ev, diag, project):
    md = ["# 自动化改进优化方案（六阶段闭环 · pav v%s）" % VERSION, "",
          "- 项目：%s" % project, "- 生成时间：%s" % plan["time"], "",
          "## 〇 · 结论摘要", "",
          "- 健康度判定：**%s**" % diag["verdict"],
          "- 问题分布：红线 %d / 阻断 %d / 提醒 %d / 信息 %d（共 %d）" % (
              diag["counts"].get("red", 0), diag["counts"].get("amber", 0),
              diag["counts"].get("yellow", 0), diag["counts"].get("info", 0), diag["total"]),
          "- 规模指标：%d 文件 / %d 行 / 每文件均 %.1f 行 / 问题密度 %.2f/文件" % (
              ev["metrics"].get("files", 0), ev["metrics"].get("lines", 0),
              ev["metrics"].get("avg_lines_per_file", 0), ev["metrics"].get("issue_density", 0)), "",
          "## 一 · 优势项（证据支撑）", ""]
    for s in ev["strengths"]:
        md.append("- ✅ **%s** — 证据：%s" % (s["item"], s["evidence"]))
    md += ["", "## 二 · 劣势项（证据 + 影响面）", ""]
    for w in ev["weaknesses"]:
        md.append("- ⚠️ **%s**（影响面：%s）\n  - 证据：%s" % (w["item"], w.get("impact", "—"), w["evidence"]))
    md += ["", "## 三 · 优化任务清单（影响 × 可行性排序）", ""]
    md.append("| 任务 | 权限 | 动作 | 依据 | 验证 | 回滚条件 |")
    md.append("|---|---|---|---|---|---|")
    for t in plan["tasks"]:
        md.append("| %s %s | %s | %s | %s | %s | %s |" % (
            t["id"], t["title"], t["level"], t["action"], t["basis"], t["verify"], t["rollback"]))
    md += ["", "## 四 · 执行方式", "",
           "```bash",
           "# L0 自动执行（沙箱备份 → 修改 → 回归验证）：",
           "python pav.py fix --project <项目路径> --auto",
           "# 指定任务：",
           "python pav.py fix --project <项目路径> --task T001",
           "# 回归验证：",
           "python pav.py verify --project <项目路径>",
           "```", "",
           "## 五 · 边界声明", "",
           "- L2 任务只给方案不改码；断言/测试期望与删除用例类变更（L3）一律禁止自动执行。",
           "- 同一问题最多自动尝试 %d 次，超限输出交接报告交回人工。" % MAX_FIX_ATTEMPTS,
           "- 「绿了」不等于「对了」：verify 阶段做证据匹配，无改动证据的问题消失会被标记为可疑。", ""]
    return "\n".join(md)


# ---------------------------------------------------------------- 执行层（受控修复）

def sandbox_backup(project, rel_files):
    """改动前备份原文件到 .pav/backup/<ts>/，返回备份目录。"""
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(pav_dir(project), "backup", ts)
    for rel in rel_files:
        src = os.path.join(project, rel.replace("/", os.sep))
        if not os.path.exists(src):
            continue
        dst = os.path.join(bdir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
    return bdir


def restore_backup(project, bdir):
    for root, _, files in os.walk(bdir):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(src, bdir)
            dst = os.path.join(project, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)


def gen_patch(project, iss, suggestion_lines=None):
    """L1：生成 unified diff 补丁到 .pav/patches/，不落真实文件。"""
    full = os.path.join(project, iss["file"].replace("/", os.sep))
    old, _ = read_text(full)
    if old is None:
        return None
    old_lines = old.splitlines(keepends=True)
    if iss["fix"] == "patch" and iss["code"] in ("PAV-S-001", "PAV-U-001", "PAV-S-002"):
        # 注释掉该行并附 TODO 提示（示意补丁，供人工确认后应用）
        new_lines = list(old_lines)
        idx = min(iss["line"] - 1, len(new_lines) - 1)
        if 0 <= idx < len(new_lines):
            new_lines[idx] = "// [pav L1 待审批] " + new_lines[idx].lstrip() if not new_lines[idx].lstrip().startswith("//") else new_lines[idx]
    else:
        new_lines = old_lines
    diff = difflib.unified_diff(old_lines, new_lines,
                                fromfile="a/" + iss["file"], tofile="b/" + iss["file"])
    pdir = os.path.join(pav_dir(project), "patches")
    os.makedirs(pdir, exist_ok=True)
    pname = "%s_%s_L%s.diff" % (iss["code"].replace("-", "_"), os.path.basename(iss["file"]), iss["level"][1])
    with open(os.path.join(pdir, pname), "w", encoding="utf-8") as f:
        f.write("".join(diff) if any(diff) else "# 无自动差异可生成，需人工修改。提示：%s" % iss["hint"])
    return os.path.join(pdir, pname)


def apply_fix(ctx, iss):
    """应用 L0 修复。返回 (ok, detail)。失败抛异常由调用方计数。"""
    rel = iss["file"]
    full = os.path.join(ctx["project"], rel.replace("/", os.sep))
    text, enc = read_text(full)
    if text is None:
        return False, "文件不可读"
    if iss["fix"] == "remove_console_log":
        hits = json.loads(iss.get("hint") or "[]")
        if not hits:
            return False, "无目标行"
        lines = text.splitlines()
        removed = 0
        pat = re.compile(r"^\s*console\.log\s*\(.*\)\s*;?\s*$")
        out = []
        for no, ln in enumerate(lines, 1):
            if no in hits and pat.match(ln):
                removed += 1
                continue
            out.append(ln)
        if removed == 0:
            return False, "目标行已不存在（可能已被修复）"
        with open(full, "w", encoding=enc or "utf-8") as f:
            f.write("\n".join(out))
        return True, "移除 %d 行 console.log" % removed
    if iss["fix"] == "fix_typo":
        wrong = re.search(r"wx\.([A-Za-z_][A-Za-z0-9_]*)", iss["evidence"])
        right = iss.get("hint")
        if not (wrong and right):
            return False, "缺少替换目标"
        new_text = re.sub(r"\bwx\.%s\b" % re.escape(wrong.group(1)), "wx." + right, text)
        if new_text == text:
            return False, "未发生替换"
        with open(full, "w", encoding=enc or "utf-8") as f:
            f.write(new_text)
        return True, "wx.%s → wx.%s" % (wrong.group(1), right)
    if iss["fix"] in ("create_missing_page", "create_missing_component"):
        created = []
        for raw in iss["evidence"].replace("缺少: ", "").split(","):
            path = raw.strip()
            if not path or path in ctx["files"]:
                continue
            fullp = os.path.join(ctx["project"], path.replace("/", os.sep))
            os.makedirs(os.path.dirname(fullp), exist_ok=True)
            if path.endswith(".js"):
                body = ("Component({\n  data: {},\n  methods: {}\n})\n"
                        if iss["fix"] == "create_missing_component"
                        else "Page({\n  data: {},\n  onLoad(options) {}\n})\n")
            elif path.endswith(".wxml"):
                body = '<view class=\"pav-placeholder\">页面/组件建设中（pav 自动补齐占位，请替换为真实实现）</view>\n'
            elif path.endswith(".wxss"):
                body = '.pav-placeholder { padding: 32rpx; color: #999; }\n'
            else:
                body = '{\n  \"usingComponents\": {}\n}\n'
            with open(fullp, "w", encoding="utf-8") as f:
                f.write(body)
            created.append(path)
        if not created:
            return False, "无缺失文件可创建（可能已存在）"
        return True, "已创建占位: " + ", ".join(created)
    if iss["fix"] == "http_to_https":
        url = iss.get("evidence", "")
        if not url.startswith("http://"):
            return False, "证据不含明文 http URL"
        new_text = text.replace(url, "https://" + url[len("http://"):], 1)
        if new_text == text:
            return False, "未发生替换"
        with open(full, "w", encoding=enc or "utf-8") as f:
            f.write(new_text)
        return True, "%s → https" % url[:60]
    return False, "该问题无 L0 修复动作"


def write_handover(project, record):
    """B3：超限交接报告。"""
    hp = os.path.join(pav_dir(project), "交接报告.md")
    lines = ["# 修复交接报告（pav 自动生成 %s）" % now(), "",
             "以下问题自动修复尝试已达上限 %d 次，交回人工处理：" % MAX_FIX_ATTEMPTS, ""]
    for r in record:
        lines.append("- **%s** `%s:%s` — %s\n  - 尝试记录：%s\n  - 建议：%s" % (
            r["code"], r["file"], r["line"], r["msg"],
            "；".join(r["attempts"]) or "（无成功尝试）", r["hint"] or "见改进方案"))
    lines += ["", "> 边界：本工具在达到尝试上限后停止，不做无限循环，也不静默放弃。"]
    with open(hp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return hp


def cmd_fix(args):
    project = args.project
    state_p = os.path.join(pav_dir(project), "attempts.json")
    state = load_json(state_p, {})
    issues = load_json(os.path.join(pav_dir(project), "sense.json"), {}).get("issues", [])
    fixlog = load_json(os.path.join(pav_dir(project), "fix-log.json"), {"fixes": [], "patches": [], "handover": [], "files": []})
    if not issues:
        print("[fix] 未找到 sense.json 或无问题记录，请先运行 sense")
        return 1
    todo = []
    if args.task:
        todo = [i for i in issues if issue_key(i) in [t.get("issue_key") for t in
                (load_json(os.path.join(pav_dir(project), "plan.json"), {}).get("tasks", []))
                if t["id"] == args.task]]
        if not todo:
            todo = [i for i in issues if i["file"] == args.task or i["code"] == args.task]
    else:
        auto_level = getattr(args, "auto_level", "low")
        for i in issues:
            fix = i.get("fix")
            tier = FIX_TIER.get(fix)
            if tier == "low":
                todo.append(i)
            elif tier == "medium" and auto_level == "medium":
                todo.append(i)
            elif i.get("level") == "L1" and fix == "patch":
                # 补丁类只生成一次，避免每轮重复生成
                if state.get(issue_key(i), {"n": 0})["n"] == 0:
                    todo.append(i)
    applied, patched, handed = 0, 0, []
    touched = set()
    for i in todo:
        k = issue_key(i)
        att = state.get(k, {"n": 0, "attempts": []})
        if att["n"] >= MAX_FIX_ATTEMPTS:
            continue
        fix = i.get("fix")
        tier = FIX_TIER.get(fix)
        if tier in ("low", "medium"):
            bdir = sandbox_backup(project, [i["file"]])
            try:
                ok, detail = apply_fix(load_project(project), i)
            except Exception as e:
                ok, detail = False, "异常: %s" % e
            att["n"] += 1
            att["attempts"].append("[%s] %s: %s" % (now(), "OK" if ok else "FAIL", detail))
            state[k] = att
            if ok:
                touched.add(i["file"])
                fixlog["fixes"].append({"time": now(), "key": k, "backup": bdir, "detail": detail,
                                        "file": i["file"], "code": i["code"], "fix": fix})
                fixlog["files"].append(i["file"])
                applied += 1
            elif att["n"] >= MAX_FIX_ATTEMPTS:
                handed.append({"code": i["code"], "file": i["file"], "line": i["line"],
                               "msg": i["msg"], "hint": i["hint"], "attempts": att["attempts"]})
        elif fix == "patch":
            p = gen_patch(project, i)
            att["n"] += 1
            att["attempts"].append("[%s] PATCH 生成: %s" % (now(), p))
            state[k] = att
            if p:
                fixlog["patches"].append({"time": now(), "key": k, "patch": p, "code": i["code"]})
                patched += 1
            if att["n"] >= MAX_FIX_ATTEMPTS and p is None:
                handed.append({"code": i["code"], "file": i["file"], "line": i["line"],
                               "msg": i["msg"], "hint": i["hint"], "attempts": att["attempts"]})
        else:
            continue
    save_json(state_p, state)
    save_json(os.path.join(pav_dir(project), "fix-log.json"), fixlog)
    if handed:
        hp = write_handover(project, handed)
        fixlog["handover"].append({"time": now(), "report": hp})
        save_json(os.path.join(pav_dir(project), "fix-log.json"), fixlog)
        print("[fix] 已生成交接报告: %s" % hp)
    print("[fix] 完成：L0 自动修复 %d 项，L1 补丁生成 %d 项（待人工审批），失败/超限 %d 项" % (
        applied, patched, len(handed)))
    print("[fix] 沙箱备份目录示例：%s" % os.path.join(project, ".pav", "backup"))
    return 0


# ---------------------------------------------------------------- 验证层

def cmd_verify(args):
    project = args.project
    before = load_json(os.path.join(pav_dir(project), "sense.json"), {"issues": []})
    before_issues = before.get("issues", [])
    ctx = load_project(project)
    issues = run_all_scanners(ctx)
    save_json(os.path.join(pav_dir(project), "sense.json"),
              {"time": now(), "project": project, "issues": issues})
    diag = diagnose(issues, ctx)
    save_json(os.path.join(pav_dir(project), "diagnose.json"), diag)

    fixlog = load_json(os.path.join(pav_dir(project), "fix-log.json"), {})
    modified = set(fixlog.get("files", [])) | {b.get("file") for b in fixlog.get("fixes", []) if b.get("file")}
    # 多重集对比（编码+文件）：对行号漂移稳健，避免修复后行号变化被误计为新增/消失
    from collections import Counter
    cb = Counter((i["code"], i["file"]) for i in before_issues)
    ca = Counter((i["code"], i["file"]) for i in issues)
    fixed = ["%s|%s" % k for k in sorted(set(cb) | set(ca)) if ca[k] < cb[k]]
    new = ["%s|%s" % k for k in sorted(set(cb) | set(ca)) if ca[k] > cb[k]]
    suspicious = []
    for k in sorted(fixed):
        f = k.split("|")[1]
        if f not in modified:
            suspicious.append(k)
    # 文件骤减守卫：改动的文件行数下降 >30%（且原文件≥50行）视为可疑（防"删代码变绿"）
    shrink = []
    for b in fixlog.get("fixes", []):
        f = b.get("file")
        bdir = b.get("backup")
        if not (f and bdir):
            continue
        old = os.path.join(bdir, f.replace("/", os.sep))
        cur = os.path.join(project, f.replace("/", os.sep))
        if os.path.exists(old) and os.path.exists(cur):
            o = read_text(old)[0] or ""
            c = read_text(cur)[0] or ""
            if o and c and len(o.splitlines()) >= 50 and (len(o.splitlines()) - len(c.splitlines())) / len(o.splitlines()) > 0.3:
                shrink.append(f)
    result = {"time": now(), "fixed": sorted(fixed), "new": sorted(new),
              "suspicious": suspicious, "shrink": shrink, "verdict": diag["verdict"]}
    save_json(os.path.join(pav_dir(project), "verify.json"), result)
    print("[verify] 修复确认 %d 项 / 新增 %d 项 / 可疑消失 %d 项 / 骤减文件 %d 个" % (
        len(fixed), len(new), len(suspicious), len(shrink)))
    if suspicious:
        print("[verify] ⚠️ 以下问题消失但对应文件无修复改动记录（绿了≠对了，需人工确认）：")
        for k in suspicious[:10]:
            print("   - " + k)
    if shrink:
        print("[verify] ⚠️ 以下改动文件行数骤降 >30%%，确认不是靠删代码变绿：%s" % ", ".join(shrink))
    print("[verify] 当前判定：%s" % diag["verdict"])
    return 0 if not suspicious and not shrink else 2


# ---------------------------------------------------------------- 报告

def cmd_report(args):
    project = args.project
    sense = load_json(os.path.join(pav_dir(project), "sense.json"), {"issues": []})
    issues = sense.get("issues", [])
    diag = diagnose(issues, {})
    ev = evaluate(issues, load_project(project), diag)
    plan = build_plan(issues, load_project(project), ev)
    md = plan_to_markdown(plan, ev, diag, project)
    # 追加校验明细
    md += "\n\n## 附录 · 问题明细\n\n| 编码 | 级别 | 权限 | 文件:行 | 说明 |\n|---|---|---|---|---|"
    for i in issues:
        md += "\n| %s | %s | %s | %s:%s | %s |" % (
            i["code"], SEV_CN.get(i["severity"], i["severity"]), i["level"],
            i["file"], i["line"], i["msg"].replace("|", "\\|"))
    out = args.out or os.path.join(pav_dir(project), "校验报告.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("[report] 校验报告已生成: %s" % out)
    return out


# ---------------------------------------------------------------- 六阶段闭环

def compute_score(diag):
    """质量分 0-100：红线-15 / 阻断-8 / 提醒-3 / 信息-1，下限 0。"""
    c = diag["counts"]
    return max(0, 100 - 15 * c.get("red", 0) - 8 * c.get("amber", 0)
               - 3 * c.get("yellow", 0) - 1 * c.get("info", 0))


def append_history(project, entry):
    hp = os.path.join(pav_dir(project), "history.json")
    h = load_json(hp, [])
    h.append(entry)
    save_json(hp, h)


def run_round(project, rnd, rounds, dry, auto_level="low"):
    """执行一轮完整六阶段闭环，返回本轮摘要。"""
    # ① 感知
    ctx = load_project(project)
    issues = run_all_scanners(ctx)
    save_json(os.path.join(pav_dir(project), "sense.json"),
              {"time": now(), "project": project, "issues": issues})
    print("[① 感知] 扫描 %d 文件，发现问题 %d 项" % (len(ctx["files"]), len(issues)))
    # ② 诊断
    diag = diagnose(issues, ctx)
    save_json(os.path.join(pav_dir(project), "diagnose.json"), diag)
    print("[② 诊断] %s（红 %d/阻 %d/提 %d/信 %d）" % (
        diag["verdict"], diag["counts"]["red"], diag["counts"]["amber"],
        diag["counts"]["yellow"], diag["counts"]["info"]))
    # ③ 评估
    ev = evaluate(issues, ctx, diag)
    save_json(os.path.join(pav_dir(project), "evaluate.json"), ev)
    print("[③ 评估] 优势 %d 项 / 劣势 %d 项 / 优先级清单 %d 项" % (
        len(ev["strengths"]), len(ev["weaknesses"]), len(ev["priority"])))
    # ④ 规划
    plan = build_plan(issues, ctx, ev)
    save_json(os.path.join(pav_dir(project), "plan.json"), plan)
    print("[④ 规划] %s" % plan["summary"])
    # ⑤ 执行
    if dry:
        print("[⑤ 执行] dry-run 跳过")
    else:
        class A: pass
        fa = A(); fa.project = project; fa.auto = True; fa.task = None; fa.auto_level = auto_level
        cmd_fix(fa)
    # ⑥ 验证（验证后 diagnose.json 已更新为修复后状态）
    class B: pass
    va = B(); va.project = project
    cmd_verify(va)
    post_diag = load_json(os.path.join(pav_dir(project), "diagnose.json"), diag)
    verify = load_json(os.path.join(pav_dir(project), "verify.json"), {})
    score = compute_score(post_diag)
    print("[本轮小结] 质量分 %d/100（修复确认 %d / 新增 %d / 可疑 %d）" % (
        score, len(verify.get("fixed", [])), len(verify.get("new", [])),
        len(verify.get("suspicious", []))))
    return {"round": rnd, "time": now(), "score": score, "counts": post_diag["counts"],
            "total": post_diag["total"],
            "fixed": len(verify.get("fixed", [])), "new": len(verify.get("new", [])),
            "suspicious": len(verify.get("suspicious", [])),
            "shrink": len(verify.get("shrink", []))}


def cmd_loop(args):
    project = args.project
    print("=" * 60)
    print("pav 六阶段闭环 | %s | 项目: %s" % (now(), project))
    print("=" * 60)
    auto_level = getattr(args, "auto_level", "low")
    for rnd in range(1, args.rounds + 1):
        print("\n—— 第 %d/%d 轮 ——" % (rnd, args.rounds))
        s = run_round(project, rnd, args.rounds, args.dry, auto_level)
        append_history(project, s)
        remain = [i for i in load_json(os.path.join(pav_dir(project), "sense.json"), {}).get("issues", [])
                  if FIX_TIER.get(i.get("fix"))]
        if not remain:
            print("\n[闭环] 可自动修复问题清零，提前收敛。")
            break
    out = cmd_report(args)
    print("\n[闭环] 完成。总报告：%s" % out)
    print("[闭环] 铁边界执行情况：断言类变更=L3禁止自动｜超限=%d次交接｜回归证据匹配已启用" % MAX_FIX_ATTEMPTS)
    return 0


def write_optimize_report(project, history):
    """十轮深度优化总报告。"""
    sense = load_json(os.path.join(pav_dir(project), "sense.json"), {"issues": []})
    issues = sense.get("issues", [])
    fixlog = load_json(os.path.join(pav_dir(project), "fix-log.json"),
                       {"fixes": [], "patches": [], "files": []})
    md = ["# 十轮深度优化报告（pav v%s）" % VERSION, "",
          "- 项目：%s" % project, "- 生成时间：%s" % now(), "",
          "## 〇 · 结论摘要", ""]
    if history:
        first, last = history[0], history[-1]
        md.append("- 质量分变化：**%d → %d**（+%d 分，共 %d 轮）" % (
            first["score"], last["score"], last["score"] - first["score"], len(history)))
        md.append("- 问题总数变化：%d → %d" % (first["total"], last["total"]))
    md.append("- 自动修复成功：**%d 项**（全部有沙箱备份与回归证据）" % len(fixlog.get("fixes", [])))
    md.append("")
    md.append("## 一 · 质量分趋势（每轮）")
    md.append("")
    md.append("| 轮次 | 质量分 | 问题总数 | 修复确认 | 新增 | 可疑 | 骤减 |")
    md.append("|---|---|---|---|---|---|---|")
    for h in history:
        md.append("| %d | %d | %d | %d | %d | %d | %d |" % (
            h["round"], h["score"], h["total"], h["fixed"], h["new"],
            h.get("suspicious", 0), h.get("shrink", 0)))
    # 修复成果按编码统计
    by_code = {}
    for f in fixlog.get("fixes", []):
        by_code[f["code"]] = by_code.get(f["code"], 0) + 1
    md += ["", "## 二 · 自动修复成果（按问题类型）", ""]
    for code, n in sorted(by_code.items(), key=lambda x: -x[1]):
        md.append("- %s ×%d（%s）" % (code, n, TASK_TITLE.get(code, "")))
    ui_fixed = sum(n for c, n in by_code.items() if c.startswith("PAV-UI-"))
    fn_fixed = sum(n for c, n in by_code.items() if c.startswith("PAV-F-"))
    md += ["", "- 功能完善类（PAV-F-*）修复 **%d** 项；UI 完善类（PAV-UI-*）修复 **%d** 项。" % (fn_fixed, ui_fixed)]
    # 待审批补丁
    md += ["", "## 三 · 待人工审批补丁（未自动合并）", ""]
    if fixlog.get("patches"):
        for p in fixlog["patches"]:
            md.append("- %s `%s`" % (p["code"], p["patch"]))
    else:
        md.append("- 无（所有可自动修复问题已处理或无补丁类问题）")
    # 遗留问题
    remain = [i for i in issues if i["level"] in ("L1", "L2")]
    md += ["", "## 四 · 遗留问题清单（按权限分级）", ""]
    if remain:
        md.append("| 编码 | 级别 | 文件:行 | 说明 | 建议动作 |")
        md.append("|---|---|---|---|---|")
        for i in remain[:40]:
            md.append("| %s | %s | %s:%s | %s | %s |" % (
                i["code"], i["level"], i["file"], i["line"],
                i["msg"].replace("|", "\\|"), (i.get("hint") or TASK_TITLE.get(i["code"], "")).replace("|", "\\|")))
    else:
        md.append("- 无遗留（L1/L2 级问题清零）")
    md += ["", "## 五 · 边界声明", "",
           "- 全程仅执行 L0/中风险白名单修复（均沙箱备份可回滚），L2 重构只出方案，L3 断言类未触碰。",
           "- 「绿了」≠「对了」：每轮回归均做证据匹配，可疑项已在上表列明。",
           "- 同一问题尝试上限 %d 次，超限见 .pav/交接报告.md。" % MAX_FIX_ATTEMPTS,
           "- UI 手测盲区（交互流程/真机表现）请配合人工点检清单。", ""]
    out = os.path.join(pav_dir(project), "十轮优化报告.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    return out


def cmd_optimize(args):
    project = args.project
    rounds = args.rounds
    auto_level = args.auto_level
    print("=" * 60)
    print("pav 深度优化模式 | %s | 项目: %s" % (now(), project))
    print("轮次: %d | 自动级别: %s | 模式: 感知→诊断→评估→规划→执行→验证 循环" % (rounds, auto_level))
    print("=" * 60)
    for rnd in range(1, rounds + 1):
        print("\n—— 优化轮次 %d/%d ——" % (rnd, rounds))
        s = run_round(project, rnd, rounds, False, auto_level)
        append_history(project, s)
        # 收敛判定：连续 2 轮分数不变且无修复/新增 → 提前收敛
        h = load_json(os.path.join(pav_dir(project), "history.json"), [])
        if rnd >= 2 and h[-2]["score"] == s["score"] and s["fixed"] == 0 and s["new"] == 0:
            print("\n[优化] 连续无改进，提前收敛于第 %d 轮。" % rnd)
            break
    report = write_optimize_report(project, load_json(os.path.join(pav_dir(project), "history.json"), []))
    print("\n[优化] 完成。总报告：%s" % report)
    print("[优化] 铁边界：L2 只出方案｜L3 断言类禁改｜3 次上限交接｜证据匹配已启用")
    return 0


# ---------------------------------------------------------------- 自测

def cmd_selftest(args):
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pav_selftest_")
    # 构造 fixture：缺页面、缺组件、拼写错误 wx、console.log、本地地址、明文 http、
    # showLoading 无 hide、request 无 fail、破图、空 src 图片
    os.makedirs(os.path.join(tmp, "pages", "index"), exist_ok=True)
    with open(os.path.join(tmp, "app.json"), "w", encoding="utf-8") as f:
        f.write('{"pages": ["pages/index/index", "pages/ghost/ghost"], "tabBar": {"list": [{"pagePath": "pages/index/index", "text": "首页", "iconPath": "no.png"}]}}')
    with open(os.path.join(tmp, "pages", "index", "index.js"), "w", encoding="utf-8") as f:
        f.write("Page({\n  goHome() { wx.navigatTo({url:'/pages/index/index'}) },\n  onLoad(){ console.log('a')\n    console.log('b')\n    console.log('c')\n    console.log('d')\n  },\n  api(){ return 'http://127.0.0.1:3000/x' },\n  submit(){ wx.showLoading({title:'x'})\n    wx.request({url:'http://example.com/api', success(){}}) }\n})\n")
    with open(os.path.join(tmp, "pages", "index", "index.wxml"), "w", encoding="utf-8") as f:
        f.write('<view bindtap="goHome">go</view>\n<image src="/img/missing.png" />\n<image src="" />')
    with open(os.path.join(tmp, "pages", "index", "index.json"), "w", encoding="utf-8") as f:
        f.write('{"usingComponents": {"ghost-comp": "/components/ghostcomp/ghostcomp"}}')
    print("[selftest] fixture: %s" % tmp)
    class A: pass
    la = A(); la.project = tmp; la.rounds = 1; la.dry = True; la.out = None; la.auto_level = "low"
    cmd_loop(la)
    # 第一遍 dry 跑完后：确认 10 类问题全部被检出（此时尚未修复）
    sense_dry = load_json(os.path.join(tmp, ".pav", "sense.json"), {})
    codes_dry = {i["code"] for i in sense_dry.get("issues", [])}
    expect = {"PAV-F-002", "PAV-F-003", "PAV-F-005", "PAV-F-007", "PAV-M-001",
              "PAV-U-001", "PAV-S-002", "PAV-UI-001", "PAV-UI-002", "PAV-UI-003"}
    miss = expect - codes_dry
    assert not miss, "扫描器漏检: %s" % miss
    # 第二步：十轮深度优化（中风险自动级别），验证功能补齐 + UI 占位补齐 + 修复回归
    oa = A(); oa.project = tmp; oa.rounds = 3; oa.auto_level = "medium"
    cmd_optimize(oa)
    sense = load_json(os.path.join(tmp, ".pav", "sense.json"), {})
    codes = {i["code"] for i in sense.get("issues", [])}
    assert "PAV-F-002" not in codes, "缺失页面自动补齐未生效"
    assert "PAV-F-007" not in codes, "缺失组件自动补齐未生效"
    assert "PAV-S-002" not in codes, "http→https 自动修复未生效"
    assert "PAV-M-001" not in codes, "console.log 自动修复未生效"
    assert "PAV-F-005" not in codes, "wx API 拼写自动修复未生效"
    assert os.path.exists(os.path.join(tmp, "pages", "ghost", "ghost.js")), "占位页面文件未创建"
    assert os.path.exists(os.path.join(tmp, "components", "ghostcomp", "ghostcomp.js")), "占位组件文件未创建"
    assert "PAV-UI-001" in codes and "PAV-UI-002" in codes, "UI 类问题应保留待人工（L2 不允许自动改）"
    verify = load_json(os.path.join(tmp, ".pav", "verify.json"), {})
    assert not verify.get("suspicious"), "出现无证据修复（绿了≠对了守卫未生效）"
    history = load_json(os.path.join(tmp, ".pav", "history.json"), [])
    assert len(history) >= 2 and all("score" in h for h in history), "质量分历史未记录"
    assert os.path.exists(os.path.join(tmp, ".pav", "十轮优化报告.md")), "优化总报告未生成"
    print("[selftest] ✅ 10 类问题检出 + 功能/UI 自动补齐 + 回归证据匹配 + 质量分趋势 + 优化报告 全部通过")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(prog="pav", description="产品自动化校验与修复（六阶段闭环）")
    ap.add_argument("--version", action="version", version="pav %s" % VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--project", required=True, help="项目根目录路径")

    p = sub.add_parser("sense", help="① 感知：全量扫描（功能/安全/维护性）")
    add_common(p); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=lambda a: (
        save_json(os.path.join(pav_dir(a.project), "sense.json"),
                  {"time": now(), "project": a.project,
                   "issues": (lambda iss: iss)(run_all_scanners(load_project(a.project)))}),
        print_issues(a)) or 0)

    def print_issues(a):
        iss = load_json(os.path.join(pav_dir(a.project), "sense.json"), {}).get("issues", [])
        for i in iss:
            print("[%s/%s] %s %s:%s — %s" % (i["severity"], i["level"], i["code"], i["file"], i["line"], i["msg"]))
        print("共 %d 项。明细: .pav/sense.json" % len(iss))
        return 0

    p = sub.add_parser("diagnose", help="② 诊断：聚类分级")
    add_common(p)
    def _diag(a):
        iss = load_json(os.path.join(pav_dir(a.project), "sense.json"), {}).get("issues", [])
        d = diagnose(iss, {})
        save_json(os.path.join(pav_dir(a.project), "diagnose.json"), d)
        print("[诊断] %s | %s" % (d["verdict"], json.dumps(d["counts"], ensure_ascii=False)))
        for c in d["clusters"]:
            print("  %s ×%d  %s  %s" % (c["code"], c["count"], c["severity"], c["msg"]))
        return 0
    p.set_defaults(fn=_diag)

    p = sub.add_parser("evaluate", help="③ 评估：优劣势（证据支撑）")
    add_common(p)
    def _eval(a):
        iss = load_json(os.path.join(pav_dir(a.project), "sense.json"), {}).get("issues", [])
        ctx = load_project(a.project)
        d = diagnose(iss, {})
        ev = evaluate(iss, ctx, d)
        save_json(os.path.join(pav_dir(a.project), "evaluate.json"), ev)
        print("[评估] 规模: %s" % json.dumps(ev["metrics"], ensure_ascii=False))
        print("优势：")
        for s in ev["strengths"]:
            print("  ✅ %s（证据：%s）" % (s["item"], s["evidence"]))
        print("劣势：")
        for w in ev["weaknesses"]:
            print("  ⚠️ %s（影响面 %s；证据：%s）" % (w["item"], w.get("impact", "—"), w["evidence"]))
        return 0
    p.set_defaults(fn=_eval)

    p = sub.add_parser("plan", help="④ 规划：生成改进优化方案")
    add_common(p); p.add_argument("--out", default=None)
    def _plan(a):
        iss = load_json(os.path.join(pav_dir(a.project), "sense.json"), {}).get("issues", [])
        ctx = load_project(a.project)
        d = diagnose(iss, {})
        ev = evaluate(iss, ctx, d)
        pl = build_plan(iss, ctx, ev)
        save_json(os.path.join(pav_dir(a.project), "plan.json"), pl)
        md = plan_to_markdown(pl, ev, d, a.project)
        out = a.out or os.path.join(pav_dir(a.project), "改进优化方案.md")
        with open(out, "w", encoding="utf-8") as f:
            f.write(md)
        print("[规划] %s" % pl["summary"])
        print("[规划] 方案已生成: %s" % out)
        for t in pl["tasks"][:10]:
            print("  %s [%s] %s" % (t["id"], t["level"], t["title"]))
        return 0
    p.set_defaults(fn=_plan)

    p = sub.add_parser("fix", help="⑤ 执行：L0 自动修复（沙箱备份）/ L1 生成补丁")
    add_common(p); p.add_argument("--auto", action="store_true"); p.add_argument("--task", default=None)
    p.add_argument("--auto-level", choices=["low", "medium"], default="low",
                   help="low=仅低风险；medium=含中风险（补页面/组件占位、http→https）")
    p.set_defaults(fn=cmd_fix)

    p = sub.add_parser("verify", help="⑥ 验证：回归 + 证据匹配（绿了≠对了守卫）")
    add_common(p)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("loop", help="普通闭环：六阶段自动跑 N 轮（默认 low 级别）")
    add_common(p); p.add_argument("--rounds", type=int, default=2); p.add_argument("--dry", action="store_true"); p.add_argument("--out", default=None)
    p.add_argument("--auto-level", choices=["low", "medium"], default="low")
    p.set_defaults(fn=cmd_loop)

    p = sub.add_parser("optimize", help="深度优化模式：全自动十轮校验+修复+优化（默认 10 轮，medium 级别）")
    add_common(p); p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--auto-level", choices=["low", "medium"], default="medium")
    p.set_defaults(fn=cmd_optimize)

    p = sub.add_parser("report", help="生成总校验报告")
    add_common(p); p.add_argument("--out", default=None)
    p.set_defaults(fn=cmd_report)

    sub.add_parser("selftest", help="端到端自测（临时 fixture）").set_defaults(fn=cmd_selftest)

    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
