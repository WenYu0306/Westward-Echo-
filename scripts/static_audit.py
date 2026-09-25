#!/usr/bin/env python3.11
"""静态审计断头：找出"一跑就崩"的引用与未绑定变量，自身零执行、零 API。

背景：变异测试覆盖 src/ 的 28 个文件，但 scripts/ 覆盖为 0，src/ 另有 32 个
文件不在 TARGETS 内。变异测试结构上抓不到断头——它改的是已存在代码的行为，
而断头是"代码引用的东西根本不存在"。历史上已发生四次：
  - check_source.py：`from src.encoding import ...` 模块改名 → ImportError（已修）
  - run_one_segment.py：收尾打印引用只在循环体内赋值的 `glossary` → UnboundLocalError（已修）
  - adversarial_test.py：5bba508 删 Arabic 支持时未同步本脚本，`scan_arabic_blasphemy`
    不存在 → TEST4 无条件调用它，整个 harness 连 --skip-api 都跑不起来（已修）
  - ab_test.py：c121f0b 切三模型分工后 DEEPSEEK_API_KEY 与 MODEL_MAP["translate"]
    均失效 → 模块级 import 即崩（已废弃删除）
共同根因是"重构删了 A，调用方 B 没同步"。这类缺陷静态可查，但**不能用实跑来验**：
scripts/ 里有一批"跑就花钱"的脚本，且 TEST3 会写生产 job_store。

用法：
    python3.11 scripts/static_audit.py --self-test    # 先跑这个：27 条对照
    python3.11 scripts/static_audit.py                # 扫 scripts/ + 仓库根
    python3.11 scripts/static_audit.py --include-src  # 追加 src/ 全量

检测四类：
  A. 语法/编译错误
  B. import 断头 —— 目标模块不存在，或 from-import 的符号在目标模块中不存在
  C. 属性引用断头 —— `import src.x as m` 后引用 `m.ATTR`，而 ATTR 不在 src/x.py 顶层
  D. 可能未绑定的局部变量 —— 所有赋值都在 if/for/while/try 保护块内，
     却在保护块之外的后续语句被读取（循环体一次都不执行即 UnboundLocalError）

判据说明（防误报）：
  - B/C 只报"确定不存在"，不报"可能是动态属性"；模块顶层条件定义（if/try 内）
    一律视为存在，避免把可选依赖误判为断头。
  - D 每条命中都需人工复核：静态无法证明"循环至少执行一次"。

可信度：--self-test 的 27 条对照全通过（9 条阳性复刻上述真实断头模式，18 条阴性
含五轮误报复现）。五轮修复的真实缺陷已各自补入对照防回归：except 必然退出、
推导式独立作用域（含 DictComp 的 key/value）、if/else 穷尽赋值、try/except 穷尽
赋值（必然退出的 handler 不参与交集）、下标/属性赋值不创建本地名。
D 类误报因此从 14 条收敛到 1 条真阳性。

抓不到的：字典下标键存在性（`MODEL_MAP["translate"]`）。需手工核，参见
条款5 记录——本轮即靠人工发现 3 条此类 KeyError。

⚠️ 当前尚未接入 pre-commit/CI：scripts/ 两个剧本残留脚本仍有 5 条已知断头
（见条款4），接进去会天生红。按"优先消除而非豁免"，等断头修完/删完再接，
这样闸门天生是绿的，不需要基线豁免机制。
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # scripts/ 的上一级 = 项目根
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BUILTIN_NAMES = set(dir(builtins))
# 默认扫 scripts/ + 仓库根。--include-src 追加 src/：src/ 有 60 个 py 文件，
# 其中仅 28 个进了 mutation_test.py 的 TARGETS，剩下 32 个（read.py 等节点、
# api/、celery_app.py）变异覆盖为 0，断头同样没人验过。
SCAN_DIRS = ("scripts",)
SCAN_ROOT_FILES = True
# 这些 src 文件已被变异测试的 957/957 覆盖，B/C/D 类断头若存在，测试早已暴露；
# 仍一并扫（成本低），但报告中单独标注以免与真盲区混为一谈。
_MUTATION_COVERED = {
    "src/cultural_rules.py", "src/agent/fidelity.py", "src/output_guard.py",
    "src/chapter_splitter.py", "src/chapter_slicer.py", "src/measurements.py",
    "src/idioms.py", "src/dialect.py", "src/onomatopoeia.py",
    "src/sensitive_terms.py", "src/text_io.py", "src/agent/graph.py",
    "src/cms.py", "src/glossary/exact_store.py", "src/glossary/semantic_store.py",
    "src/style_memo.py", "src/job_store.py", "src/epub_builder.py",
    "src/backpressure.py", "src/circuit_breaker.py", "src/error_tracker.py",
    "src/agent/parse_utils.py", "src/completeness.py", "src/token_ledger.py",
    "src/agent/nodes/write.py", "src/health.py", "src/stats.py",
    "src/agent/nodes/read.py", "src/agent/nodes/readback.py",
    "src/agent/nodes/fix.py", "src/agent/nodes/review_terms.py", "src/config.py",
    "src/cost_gate.py",
}


# ── 模块解析 ────────────────────────────────────────────────────────────
def module_to_path(dotted: str) -> Path | None:
    """把 `src.a.b` 解析成 .py 文件路径；包目录返回其 __init__.py。"""
    rel = dotted.replace(".", "/")
    cand_file = ROOT / f"{rel}.py"
    if cand_file.is_file():
        return cand_file
    cand_init = ROOT / rel / "__init__.py"
    if cand_init.is_file():
        return cand_init
    return None


def package_of(dotted: str) -> str | None:
    """`from src.a.b import c` 中若 b 是包，c 可能是子模块。返回父模块点路径。"""
    return dotted.rpartition(".")[0] or None


def top_level_names(path: Path) -> set[str] | None:
    """收集一个模块的顶层可见名字。解析失败返回 None（区别于"空"）。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None

    names: set[str] = set()

    def walk_top(body: list[ast.stmt]) -> None:
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(st.name)
            elif isinstance(st, ast.Assign):
                for t in st.targets:
                    names.update(_target_names(t))
            elif isinstance(st, ast.AnnAssign):
                if st.target is not None:
                    names.update(_target_names(st.target))
            elif isinstance(st, ast.AugAssign):
                names.update(_target_names(st.target))
            elif isinstance(st, ast.Import):
                for a in st.names:
                    names.add((a.asname or a.name).split(".")[0])
            elif isinstance(st, ast.ImportFrom):
                for a in st.names:
                    if a.name == "*":
                        continue
                    names.add(a.asname or a.name)
            elif isinstance(st, ast.If):
                # 条件定义（可选依赖、版本分支）视为存在，避免误报
                walk_top(st.body)
                walk_top(st.orelse)
            elif isinstance(st, ast.Try):
                walk_top(st.body)
                for h in st.handlers:
                    walk_top(h.body)
                walk_top(st.orelse)
                walk_top(st.finalbody)
            elif isinstance(st, (ast.For, ast.While)):
                walk_top(st.body)
                walk_top(st.orelse)
            elif isinstance(st, ast.With):
                walk_top(st.body)
            elif isinstance(st, (ast.Match,)):
                for case in st.cases:
                    walk_top(case.body)

    walk_top(tree.body)

    dunder_all = _extract_dunder_all(tree)
    if dunder_all:
        names.update(dunder_all)
    return names


def _target_names(node: ast.expr) -> set[str]:
    out: set[str] = set()
    if isinstance(node, ast.Name):
        out.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            out.update(_target_names(e))
    elif isinstance(node, ast.Starred):
        out.update(_target_names(node.value))
    return out


def _extract_dunder_all(tree: ast.Module) -> set[str]:
    for st in tree.body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                if isinstance(t, ast.Name) and t.id == "__all__":
                    v = st.value
                    if isinstance(v, (ast.List, ast.Tuple, ast.Set)):
                        return {
                            e.value
                            for e in v.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        }
    return set()


# ── B/C：import 与属性引用 ──────────────────────────────────────────────
def check_imports(tree: ast.Module, rel: str, findings: list[dict]) -> None:
    """检查 from-import 的模块存在性与符号存在性，以及别名属性引用。"""
    alias_attrs: dict[str, str] = {}  # local alias -> dotted module

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if not a.name.startswith(("src.", "scripts.")):
                    continue
                if module_to_path(a.name) is None:
                    findings.append(dict(
                        kind="B-import-module-missing", file=rel, line=node.lineno,
                        detail=f"import {a.name} —— 模块不存在",
                    ))
                if a.asname:
                    alias_attrs[a.asname] = a.name

        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入，scripts/ 下少见，跳过
                continue
            mod = node.module or ""
            if not mod.startswith(("src.", "scripts.", "src", "scripts")):
                continue
            if mod not in ("src", "scripts") and module_to_path(mod) is None:
                # 可能是 `from src.a import b` 而 b 本身是子模块
                findings.append(dict(
                    kind="B-import-module-missing", file=rel, line=node.lineno,
                    detail=f"from {mod} import ... —— 模块不存在",
                ))
                continue
            target = module_to_path(mod)
            if target is None:
                continue
            avail = top_level_names(target)
            if avail is None:
                continue  # 目标模块自身语法坏，另由 A 类报出
            for a in node.names:
                if a.name == "*":
                    continue
                if a.name in avail:
                    continue
                # 可能是 `from src.a import submodule`
                sub = module_to_path(f"{mod}.{a.name}")
                if sub is not None:
                    continue
                findings.append(dict(
                    kind="B-import-symbol-missing", file=rel, line=node.lineno,
                    detail=(f"from {mod} import {a.name} —— 符号在 "
                            f"{target.relative_to(ROOT)} 顶层不存在"),
                ))
            if node.names and node.names[0].name != "*":
                asname = node.asname if hasattr(node, "asname") else None
                if asname:
                    alias_attrs[asname] = mod

    if not alias_attrs:
        return

    # C：别名属性引用
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        base = node.value
        if not isinstance(base, ast.Name) or base.id not in alias_attrs:
            continue
        mod = alias_attrs[base.id]
        path = module_to_path(mod)
        if path is None:
            continue
        avail = top_level_names(path)
        if avail is None:
            continue
        if node.attr in avail:
            continue
        if module_to_path(f"{mod}.{node.attr}") is not None:
            continue
        findings.append(dict(
            kind="C-attr-missing", file=rel, line=node.lineno,
            detail=f"{base.id}.{node.attr} —— {node.attr} 不在 {mod} 顶层"
                   f"（模块别名自 {path.relative_to(ROOT)}）",
        ))


# ── D：可能未绑定的局部变量 ─────────────────────────────────────────────
_GUARD_KINDS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With,
                ast.AsyncWith, ast.Match)


def check_unbound_locals(tree: ast.Module, rel: str, findings: list[dict]) -> None:
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        _check_one_function(fn, rel, findings)


def _check_one_function(fn, rel: str, findings: list[dict]) -> None:
    # 参数名一律视为已绑定
    bound_always: set[str] = set()
    args = fn.args
    for a in (list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)):
        bound_always.add(a.arg)
    if args.vararg:
        bound_always.add(args.vararg.arg)
    if args.kwarg:
        bound_always.add(args.kwarg.arg)

    global_declared: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            global_declared.update(node.names)

    stores: dict[str, list[tuple[int, tuple[str, ...]]]] = {}
    loads: dict[str, list[tuple[int, tuple[str, ...]]]] = {}
    # 嵌套函数里的名字属于内层作用域，排除掉避免误报
    skip_ranges: list[tuple[int, int]] = []
    for node in ast.walk(fn):
        if node is not fn and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            skip_ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno))

    def in_nested(lineno: int) -> bool:
        return any(s <= lineno <= e for s, e in skip_ranges)

    def visit(body: list[ast.stmt], guard: tuple[str, ...]) -> None:
        for st in body:
            _visit_stmt(st, guard, stores, loads, in_nested)
            if isinstance(st, _GUARD_KINDS):
                label = type(st).__name__
                sub = guard + (label,)
                if isinstance(st, (ast.If, ast.Match)):
                    if isinstance(st, ast.If):
                        visit(st.body, sub)
                        visit(st.orelse, sub)
                    else:
                        for case in st.cases:
                            visit(case.body, sub)
                elif isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
                    # 迭代变量本身在循环头绑定，但循环不执行时同样未绑定。
                    # 用 _target_names 而非 ast.walk：`for d[k] in x` 的 target
                    # 是 Subscript，其中的 d 是 Load 而非绑定。
                    if isinstance(st, (ast.For, ast.AsyncFor)):
                        for n in _target_names(st.target):
                            stores.setdefault(n, []).append((st.lineno, sub))
                    visit(st.body, sub)
                    visit(st.orelse, sub)
                elif isinstance(st, ast.Try):
                    if st.handlers and all(_always_exits(h.body) for h in st.handlers):
                        # 每个 except 都必然离开函数 → 能走到 try 之后的代码，
                        # 一定是 try 体正常完成的，try 体内的赋值等价于无条件。
                        visit(st.body, guard)
                    else:
                        visit(st.body, sub)
                        for h in st.handlers:
                            visit(h.body, sub + ("ExceptHandler",))
                    visit(st.orelse, sub)
                    # finally 总会执行，不算保护
                    visit(st.finalbody, guard)
                elif isinstance(st, (ast.With, ast.AsyncWith)):
                    visit(st.body, guard)  # with 体总会执行
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                pass  # 嵌套定义体不递归（作用域不同）
            elif hasattr(st, "body") and isinstance(getattr(st, "body"), list):
                visit(st.body, guard)

    visit(fn.body, ())

    # if/else 两分支都赋值 → 名字在函数体内必然绑定，等价于无条件赋值。
    # 静态遍历函数体收集这类名字（只扫本函数直接持有的一层，不下探嵌套控制流
    # 之外的东西；_names_definitely_bound 内部已处理 If/Try/With 的嵌套）。
    definitely_bound = _names_definitely_bound(fn.body)

    for name, load_list in loads.items():
        if name in bound_always or name in global_declared or name in BUILTIN_NAMES:
            continue
        if name in definitely_bound:
            continue  # 所有路径都会赋值，读取时必然已绑定
        st_list = stores.get(name)
        if not st_list:
            continue  # 从未在本函数赋值 → 外层/全局名，不归 D 管
        if any(g == () for _, g in st_list):
            continue  # 存在无条件赋值路径 → 不报
        # 所有赋值都在保护块内；找保护块之外的读取
        max_store_line = max(l for l, _ in st_list)
        guards = sorted({"/".join(g) for _, g in st_list})
        for lline, lguard in load_list:
            if lguard == () and lline > max_store_line:
                findings.append(dict(
                    kind="D-possibly-unbound", file=rel, line=lline,
                    detail=(f"局部变量 {name!r} 的全部赋值都在保护块内"
                            f"（{', '.join(guards)}，最晚第 {max_store_line} 行），"
                            f"却在第 {lline} 行的无条件路径被读取 —— "
                            f"保护块一次都不执行即 UnboundLocalError"),
                ))
                break  # 每个名字每个函数只报一次


def _visit_stmt(st, guard, stores, loads, in_nested) -> None:
    # 先记录本语句自身的赋值（在 guard 下）。
    # 只用 _target_names：它对 Subscript/Attribute 返回空集——`d[k] = v`、
    # `obj.a = v` 修改的是已存在对象，并不在本地作用域创建名字。若像早先那样
    # 用 ast.walk(target) 收集，会把 d/obj 误记为"本地赋值"，进而在
    # `return d[key]` 处报出假 UnboundLocalError（src/circuit_breaker.py:224
    # 的模块级 _breakers 就是这样被误报的）。
    if isinstance(st, ast.Assign):
        for t in st.targets:
            for n in _target_names(t):
                if not in_nested(getattr(st, "lineno", 0)):
                    stores.setdefault(n, []).append((st.lineno, guard))
    elif isinstance(st, ast.AnnAssign):
        if st.value is not None and st.target is not None:
            for n in _target_names(st.target):
                if not in_nested(st.lineno):
                    stores.setdefault(n, []).append((st.lineno, guard))
    elif isinstance(st, ast.AugAssign):
        for n in _target_names(st.target):
            if not in_nested(st.lineno):
                stores.setdefault(n, []).append((st.lineno, guard))
    elif isinstance(st, (ast.Import, ast.ImportFrom)):
        for a in st.names:
            if a.name == "*":
                continue
            nm = a.asname or a.name.split(".")[0]
            stores.setdefault(nm, []).append((st.lineno, guard))
    elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        stores.setdefault(st.name, []).append((st.lineno, guard))
    elif isinstance(st, (ast.With, ast.AsyncWith)):
        for item in st.items:
            if item.optional_vars is not None:
                for n in _target_names(item.optional_vars):
                    if not in_nested(st.lineno):
                        stores.setdefault(n, []).append((st.lineno, guard))

    # 记录读取：只扫本语句表达式层的 Name(Load)，不下探嵌套控制流（由 visit 负责）
    for node in _expr_nodes_of(st):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if not in_nested(node.lineno):
                loads.setdefault(node.id, []).append((node.lineno, guard))


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _expr_nodes_of(st):
    """产出语句自身直接持有的表达式节点，不进入子语句体。

    推导式（ListComp/SetComp/DictComp/GeneratorExp）有独立作用域，其内部绑定名
    不是外层函数的局部变量，故不下探——否则 `{f['x'] for f in items}` 里的 `f`
    会被误判为可能未绑定的局部变量。
    """
    for field, value in ast.iter_fields(st):
        if field in ("body", "orelse", "finalbody", "handlers", "cases"):
            continue
        if isinstance(value, ast.AST):
            yield from _walk_excluding_comprehensions(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.AST):
                    yield from _walk_excluding_comprehensions(item)


def _walk_excluding_comprehensions(node):
    """ast.walk，但不下探任何推导式子树。

    推导式（含 DictComp/SetComp）的 key/value/elt 与生成器都在推导式自己的
    作用域里求值，其中的名字不是外层函数的局部变量。必须整棵跳过：只跳过
    "作为子节点出现的推导式"不够，因为 DictComp 本身就是被 yield 的根，
    其 key/value 作为它的子节点仍会被走下去。
    """
    if isinstance(node, _COMPREHENSIONS):
        return  # 整棵子树都在独立作用域，不产出任何名字
    yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_excluding_comprehensions(child)


def _names_definitely_bound(body: list[ast.stmt]) -> set[str]:
    """返回"该语句块正常执行完后，一定已绑定的名字"。

    与 stores 的守卫记录互补：stores 记录"某条路径上可能赋值"，这里记录
    "所有路径上都赋值"。用途是识别 if/else 穷尽赋值——两个分支都赋值时，
    该名字在 if 之后等价于无条件绑定，不该报"可能未绑定"。

    刻意保守：循环体（可能零次执行）、无 else 的 if、match（穷尽性无法静态
    证明）都不算必然绑定；try 只在"每个 except 都必然退出"时把 try 体视为
    必然完成，finally 体总是执行故并入。
    """
    out: set[str] = set()
    for st in body:
        if isinstance(st, ast.Assign):
            for t in st.targets:
                out |= _target_names(t)
        elif isinstance(st, ast.AnnAssign):
            if st.value is not None and st.target is not None:
                out |= _target_names(st.target)
        elif isinstance(st, ast.AugAssign):
            out |= _target_names(st.target)
        elif isinstance(st, (ast.Import, ast.ImportFrom)):
            for a in st.names:
                if a.name != "*":
                    out.add(a.asname or a.name.split(".")[0])
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(st.name)
        elif isinstance(st, (ast.For, ast.AsyncFor, ast.While)):
            pass  # 循环体可能一次都不执行 → 不算必然绑定
        elif isinstance(st, ast.If):
            if st.body and st.orelse:
                # 两分支都必然绑定才算必然绑定（取交集）
                out |= _names_definitely_bound(st.body) & _names_definitely_bound(st.orelse)
        elif isinstance(st, ast.Try):
            if st.handlers:
                # 穷尽性：能走到 try 之后，要么 try 体正常完成，要么某个
                # except 体正常完成（必然退出的 handler 到不了 try 之后，
                # 故不参与交集——否则会把它"没赋值"错当成反例）。
                live_handlers = [h for h in st.handlers if not _always_exits(h.body)]
                if not live_handlers:
                    out |= _names_definitely_bound(st.body)
                else:
                    sets = [_names_definitely_bound(st.body)]
                    sets += [_names_definitely_bound(h.body) for h in live_handlers]
                    common = sets[0]
                    for s in sets[1:]:
                        common &= s
                    out |= common
            out |= _names_definitely_bound(st.finalbody)
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            for item in st.items:
                if item.optional_vars is not None:
                    out |= _target_names(item.optional_vars)
            out |= _names_definitely_bound(st.body)
        # ast.Match 穷尽性无法静态证明，保守跳过
    return out


def _always_exits(body: list[ast.stmt]) -> bool:
    """判断一个语句块是否总是退出当前作用域（return/raise/break/continue）。

    用于识别 `try: x = ...  except E: return None` 这种模式——既然异常路径
    必然离开函数，try 体之后的代码就只在 try 体正常完成时到达，try 体内的
    赋值等价于无条件赋值，不该报"可能未绑定"。
    """
    if not body:
        return False
    last = body[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(last, ast.If):
        return bool(last.body) and bool(last.orelse) and \
            _always_exits(last.body) and _always_exits(last.orelse)
    if isinstance(last, ast.Try):
        # try 体退出 且 每个 handler 都退出 → 整体必然退出
        return _always_exits(last.body) and bool(last.handlers) and \
            all(_always_exits(h.body) for h in last.handlers)
    return False


# ── 主流程 ──────────────────────────────────────────────────────────────
def collect_targets() -> list[Path]:
    files: list[Path] = []
    self_path = Path(__file__).resolve()
    dirs = list(SCAN_DIRS) + (["src"] if "--include-src" in sys.argv else [])
    for d in dirs:
        base = ROOT / d
        if base.is_dir():
            files.extend(
                p for p in base.rglob("*.py")
                if "__pycache__" not in p.parts
                and p.resolve() != self_path
                and not p.name.endswith("_scan_tmp.py")
            )
    if SCAN_ROOT_FILES:
        files.extend(
            p for p in ROOT.glob("*.py")
            if p.resolve() != self_path and not p.name.endswith("_scan_tmp.py")
        )
    return sorted(set(files), key=lambda p: str(p.relative_to(ROOT)))


def scan_source(src: str, rel: str, findings: list[dict]) -> bool:
    """对一段源码跑全部检查。返回 False 表示连解析都失败。"""
    try:
        tree = ast.parse(src, filename=rel)
    except SyntaxError as exc:
        findings.append(dict(
            kind="A-syntax-error", file=rel, line=exc.lineno or 0,
            detail=f"SyntaxError: {exc.msg}",
        ))
        return False
    except UnicodeDecodeError as exc:
        findings.append(dict(kind="A-decode-error", file=rel, line=0, detail=str(exc)))
        return False
    check_imports(tree, rel, findings)
    check_unbound_locals(tree, rel, findings)
    return True


# ── 自检：阳性/阴性对照 ─────────────────────────────────────────────────
# 阳性对照复刻本项目真实发生过的两类断头（HANDOFF_20260921_audit_untrusted.md
# §六 第11/12条）：模块改名导致的 ImportError、只在循环体内赋值的收尾打印
# UnboundLocalError。阴性对照是"长得像但不该报"的安全写法。
# 工具没经过这种对照就不能用来下结论——它可能既漏报又误报，而输出看着很绿。
_POSITIVE_CASES = [
    ("import 已改名的模块", "B-import-module-missing",
     "from src.encoding import detect_and_read\n"),
    ("from-import 不存在的符号", "B-import-symbol-missing",
     "from src.text_io import this_symbol_does_not_exist\n"),
    ("模块别名引用不存在的属性", "C-attr-missing",
     "import src.text_io as tio\n\n\ndef f():\n    return tio.no_such_attr(1)\n"),
    ("只在 for 循环体内赋值，循环外读取", "D-possibly-unbound",
     "def f(items):\n    for i in items:\n        v = i * 2\n    print(len(v))\n"),
    ("只在 if 真分支赋值，分支外读取", "D-possibly-unbound",
     "def f(flag):\n    if flag:\n        r = 1\n    return r\n"),
    ("只在 while 体内赋值，循环外读取", "D-possibly-unbound",
     "def f(n):\n    while n > 0:\n        g = n\n        n -= 1\n    return g\n"),
    ("try 内赋值但 except 不退出，try 后读取", "D-possibly-unbound",
     "def f():\n    try:\n        t = do()\n    except Exception as e:\n        print(e)\n"
     "    return t\n"),
    # 实测确认（python3.11）：函数内任何位置赋值都使该名字在整个函数内成为
    # 局部变量，遮蔽同名模块级全局；f(True) 返回 False，f(False) 抛
    # UnboundLocalError。这是真实 bug 模式，属阳性。
    ("函数内赋值遮蔽模块级全局名", "D-possibly-unbound",
     "GLOBAL_FLAG = True\n\n\ndef f(flag):\n    if flag:\n        GLOBAL_FLAG = False\n"
     "    return GLOBAL_FLAG\n"),
    ("语法错误", "A-syntax-error",
     "def f(:\n    pass\n"),
]

_NEGATIVE_CASES = [
    ("try 赋值 + except 总是 return，try 后读取", "tree",
     "def f():\n    try:\n        tree = parse()\n    except SyntaxError:\n"
     "        return None\n    return tree.body\n"),
    ("try 赋值 + except raise，try 后读取", "val",
     "def f():\n    try:\n        val = go()\n    except OSError as e:\n"
     "        raise RuntimeError('x') from e\n    return val + 1\n"),
    ("推导式变量不是外层局部", "item",
     "def f(rows):\n    return [{'k': row['k']} for row in rows]\n"),
    ("with 体内赋值（with 体总会执行）", "fh",
     "def f(p):\n    with open(p) as fh:\n        pass\n    return fh.name\n"),
    ("函数顶部无条件赋值", "total",
     "def f(xs):\n    total = 0\n    for x in xs:\n        total += x\n    return total\n"),
    ("形参名不报", "a",
     "def f(a, b=1):\n    if b:\n        a = 2\n    return a\n"),
    ("global 声明的名字不报", "g",
     "g = 1\n\n\ndef f(flag):\n    global g\n    if flag:\n        g = 2\n    return g\n"),
    ("内置名不报", "len",
     "def f(flag):\n    if flag:\n        len = 1\n    return len([1])\n"),
    ("for 体内赋值且仅在体内读取", "row",
     "def f(rows):\n    out = []\n    for row in rows:\n        out.append(row * 2)\n"
     "    return out\n"),
    # 以下两条来自 scripts/adversarial_test.py:300/392 与 verify_read_only.py:52
    # 的真实误报，修复后补入对照以防回归。
    ("if/else 两分支都赋值（穷尽）", "chapters",
     "def f(p):\n    if p.exists():\n        chapters = p.read_text().split('x')\n"
     "    else:\n        chapters = gen()\n    return len(chapters)\n"),
    ("if/try 且 except 必然 return，else 也赋值（穷尽）", "detail",
     "def f(n):\n    if n == 0:\n        detail = 'a'\n"
     "    else:\n        detail = f'{n}'\n    return detail\n"),
    ("DictComp 的 key/value 内名字不是外层局部", "td",
     "def f(analysis):\n    for td in analysis.get('x', []):\n        pass\n"
     "    return {td.get('k'): td.get('v', '') for td in analysis.get('x', [])}\n"),
    # 来自 src/error_tracker.py:150/153 的真实误报：try 体正常赋值、except 体
    # 赋兜底值，两路穷尽，读取时必然已绑定。
    ("try 赋值 + except 赋兜底值（穷尽）", "total",
     "def f(job):\n    try:\n        total = job['n']\n"
     "    except Exception:\n        total = 0\n    return total * 2\n"),
    ("try 赋值 + except 赋兜底 + finally 也跑（穷尽）", "v",
     "def f(x):\n    try:\n        v = x.a\n    except AttributeError:\n        v = None\n"
     "    finally:\n        pass\n    return v\n"),
    # 来自 src/agent/nodes/write.py:135/139/164 的真实误报：第一个 handler 纯
    # raise（必然退出，到不了 try 之后），只有活着的 handler 才该参与穷尽判定。
    ("try 赋值 + 一个 handler 纯 raise、另一个赋值（穷尽）", "response",
     "def f(llm):\n    try:\n        response = llm.invoke('x')\n"
     "    except OpenError:\n        raise\n"
     "    except Exception as e:\n        if not recoverable(e):\n            raise\n"
     "        response = other.invoke('x')\n    return response.content\n"),
    # 来自 src/circuit_breaker.py:218/224 的真实误报：`d[k] = v` 是修改已存在
    # 对象，不在本地作用域创建名字，d 仍是模块级全局。
    ("模块级字典的下标赋值不创建本地名", "_cache",
     "_cache = {}\n\n\ndef f(key):\n    if key not in _cache:\n"
     "        _cache[key] = 1\n    return _cache[key]\n"),
    ("实例属性赋值不创建本地名", "obj",
     "def f(obj):\n    if obj is None:\n        obj.ready = True\n    return obj.ready\n"),
    ("for 下标目标不创建本地名", "d",
     "d = {}\n\n\ndef f(pairs):\n    for d[k] in pairs:\n        pass\n    return len(d)\n"),
]


def self_test() -> int:
    print("=== 扫描器自检：阳性对照（必须被抓到）===")
    fails = 0
    for label, expect_kind, src in _POSITIVE_CASES:
        got: list[dict] = []
        scan_source(src, "<positive>", got)
        kinds = {f["kind"] for f in got}
        ok = expect_kind in kinds
        print(f"  [{'PASS' if ok else 'FAIL'}] {label} → 期望 {expect_kind}，实得 "
              f"{sorted(kinds) or '无发现'}")
        if not ok:
            fails += 1

    print()
    print("=== 扫描器自检：阴性对照（不得误报）===")
    for label, _var, src in _NEGATIVE_CASES:
        got: list[dict] = []
        scan_source(src, "<negative>", got)
        # A/B/C/D 任一类命中都算误报
        ok = not got
        detail = "" if ok else "；".join(f"{f['kind']}:{f['line']}" for f in got)
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
              + (f" → 误报 {detail}" if detail else ""))
        if not ok:
            fails += 1

    total = len(_POSITIVE_CASES) + len(_NEGATIVE_CASES)
    print()
    print(f"自检合计: {total - fails}/{total} 通过")
    if fails:
        print("FAIL: 扫描器存在漏报或误报，其扫描结论不可采信。")
    return 1 if fails else 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    targets = collect_targets()
    findings: list[dict] = []
    syntax_fail: list[str] = []

    for path in targets:
        rel = str(path.relative_to(ROOT))
        src = path.read_text(encoding="utf-8", errors="replace")
        # 与实际扫描共用同一入口：自检通过的才是这套逻辑
        if not scan_source(src, rel, findings):
            syntax_fail.append(rel)

    print(f"扫描文件数: {len(targets)}")
    print(f"语法/解码失败: {len(syntax_fail)}")
    if "--include-src" in sys.argv:
        blind = [t for t in targets
                 if str(t.relative_to(ROOT)) not in _MUTATION_COVERED]
        print(f"其中 src/ 变异盲区（不在 mutation_test.py TARGETS 内）: {len(blind)}")
    by_kind: dict[str, list[dict]] = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    print()
    for kind in sorted(by_kind):
        items = by_kind[kind]
        print(f"── {kind}: {len(items)} 条 ──")
        for f in sorted(items, key=lambda x: (x["file"], x["line"])):
            tag = "" if f["file"] not in _MUTATION_COVERED else "  [变异已覆盖]"
            print(f"  {f['file']}:{f['line']}  {f['detail']}{tag}")
        print()
    blind_hits = [f for f in findings if f["file"] not in _MUTATION_COVERED]
    print(f"合计: {len(findings)} 条发现，涉及 {len({f['file'] for f in findings})} 个文件")
    print(f"其中落在变异盲区: {len(blind_hits)} 条，"
          f"涉及 {len({f['file'] for f in blind_hits})} 个文件")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
